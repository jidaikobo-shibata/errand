from pathlib import Path
import json
import sys
import threading
import time
import unittest

from errand.protocol import RpcError, server_command
from errand.session import Session

ROOT = Path(__file__).resolve().parent.parent

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.changed = threading.Condition()
        def emit(kind, data):
            with self.changed:
                self.events.append((kind, data))
                self.changed.notify_all()
        self.session = Session(emit, command=[sys.executable, str(ROOT / "tests/fake_server.py")])

    def tearDown(self):
        self.session.close()

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        with self.changed:
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.fail(f"Timed out; events: {self.events}")
                self.changed.wait(remaining)

    def finished(self):
        self.wait(lambda: any(k == "state" and not d["busy"] for k, d in self.events))

    def catalogue(self):
        self.session.load_models()
        self.wait(lambda: any(k == "models_state" and not d["loading"] for k, d in self.events))
        return next(d["models"] for k, d in self.events if k == "models")

    def settings(self):
        return json.loads(next(d["text"] for k, d in self.events if k == "assistant"))

    def test_catalogue_pagination_and_supported_efforts(self):
        models = self.catalogue()
        self.assertEqual([entry["model"] for entry in models], ["fake-fast", "fake-steady", "fake-no-effort"])
        self.assertEqual(models[0]["supportedReasoningEfforts"][-1]["reasoningEffort"], "future-effort")
        self.assertIsNone(self.session.thread_id)
        defaults = next(d for k, d in self.events if k == "models")
        self.assertEqual(defaults["default_model"], "fake-fast")
        self.assertEqual(defaults["default_effort"], "low")
        self.assertNotEqual(defaults["default_effort"], models[0]["defaultReasoningEffort"])

    def test_current_hidden_model_is_visible(self):
        self.session.command.append("--default-hidden")
        models = self.catalogue()
        self.assertIn("fake-hidden", [entry["model"] for entry in models])
        defaults = next(d for k, d in self.events if k == "models")
        self.assertEqual(defaults["default_model"], "fake-hidden")

    def test_current_unlisted_model_is_not_replaced(self):
        self.session.command.append("--default-unlisted")
        models = self.catalogue()
        self.assertEqual(models[0]["model"], "fake-unlisted")
        self.assertEqual(models[0]["supportedReasoningEfforts"][0]["reasoningEffort"], "low")

    def test_selected_model_and_effort_reach_server_and_continue(self):
        self.catalogue()
        self.events.clear()
        self.session.send("settings", model="fake-fast", effort="future-effort")
        self.finished()
        settings = self.settings()
        self.assertEqual(settings["thread"]["model"], "fake-fast")
        self.assertEqual(settings["thread"]["config"], {"model_reasoning_effort": "future-effort"})
        self.assertEqual(settings["turn"]["model"], "fake-fast")
        self.assertEqual(settings["turn"]["effort"], "future-effort")
        thread = next(d for k, d in self.events if k == "thread")
        self.assertEqual(thread["reasoning_effort"], "future-effort")
        self.events.clear()
        self.session.send("settings", model="fake-fast", effort="future-effort")
        self.finished()
        self.assertEqual(self.settings()["turn"]["effort"], "future-effort")
        self.events.clear()
        original_thread = self.session.thread_id
        self.session.send("settings", model="fake-steady", effort="medium")
        self.finished()
        self.assertEqual(self.session.thread_id, original_thread)
        self.assertEqual(self.settings()["turn"]["model"], "fake-steady")
        self.assertEqual(self.settings()["turn"]["effort"], "medium")
        self.events.clear()
        self.session.send("settings", model="fake-no-effort")
        self.finished()
        self.assertIsNone(self.settings()["turn"]["effort"])
        self.session.reset()
        self.events.clear()
        self.session.send("settings", model="fake-steady", effort="medium")
        self.finished()
        self.assertEqual(self.settings()["thread"]["model"], "fake-steady")

    def test_reviewer_is_tab_local_and_can_change_between_turns(self):
        self.session.set_approval_reviewer("auto_review")
        self.session.send("settings")
        self.finished()
        settings = self.settings()
        self.assertEqual(settings["thread"]["approvalsReviewer"], "auto_review")
        self.assertEqual(settings["turn"]["approvalsReviewer"], "auto_review")
        thread_id = self.session.thread_id
        self.events.clear()
        self.session.set_approval_reviewer("user")
        self.session.send("settings")
        self.finished()
        self.assertEqual(self.session.thread_id, thread_id)
        self.assertEqual(self.settings()["turn"]["approvalsReviewer"], "user")
        with self.assertRaises(RpcError):
            self.session.set_approval_reviewer("never")

    def test_session_scoped_requested_permissions(self):
        self.session.send("permissions")
        self.wait(lambda: "permissions-1" in self.session._requests)
        grant = {"permissions": {"fileSystem": {"write": ["/tmp/test-output.pdf"]}}, "scope": "session"}
        self.session.answer("permissions-1", grant)
        self.finished()
        response = next(d for k, d in self.events if k == "assistant")
        import json
        self.assertEqual(json.loads(response["text"])["result"], grant)

    def test_default_does_not_override_config(self):
        self.session.send("settings")
        self.finished()
        settings = self.settings()
        self.assertNotIn("model", settings["thread"])
        self.assertNotIn("config", settings["thread"])
        self.assertNotIn("model", settings["turn"])
        self.assertIsNone(settings["turn"]["effort"])

    def test_model_without_reasoning_options(self):
        self.catalogue()
        self.events.clear()
        self.session.send("settings", model="fake-no-effort")
        self.finished()
        self.assertIsNone(self.settings()["turn"]["effort"])

    def test_invalid_model_and_effort_are_rejected_before_send(self):
        with self.assertRaises(RpcError):
            self.session.send("test", model="unavailable")
        self.catalogue()
        for model, effort in [("picker-fake-fast", "low"), ("fake-fast", "medium"),
                              (None, "low"), ("fake-hidden", "low"), ("fake-image", "low")]:
            with self.subTest(model=model, effort=effort), self.assertRaises(RpcError):
                self.session.send("test", model=model, effort=effort)
        self.assertFalse(self.session.busy)
        self.assertIsNone(self.session.thread_id)

    def test_catalogue_failure_and_retry(self):
        self.session.command.append("--fail-models")
        self.session.load_models()
        self.wait(lambda: any(k == "models_state" and not d["loading"] for k, d in self.events))
        self.assertTrue(any(k == "models_error" for k, _ in self.events))
        self.assertFalse(self.session.busy)
        self.session.command.pop()
        self.events.clear()
        self.assertEqual(len(self.catalogue()), 3)

    def test_catalogue_cursor_cycle_is_an_error(self):
        self.session.command.append("--cycle-models")
        self.session.load_models()
        self.wait(lambda: any(k == "models_state" and not d["loading"] for k, d in self.events))
        self.assertTrue(any(k == "models_error" and "繰り返" in d["message"] for k, d in self.events))

    def test_reset_does_not_drop_queued_catalogue_updates(self):
        queued = []
        self.session.dispatch = queued.append
        self.session._emit("models_state", loading=True)
        self.session._emit("models", models=[])
        self.session._emit("models_state", loading=False)
        self.session._emit("delta", item_id="stale", text="old conversation")
        self.session.reset()
        for callback in queued:
            callback()
        self.assertEqual([k for k, _ in self.events], ["models_state", "models", "models_state", "reset"])

    def test_continue_and_reset(self):
        self.session.send("first")
        self.finished()
        thread = self.session.thread_id
        self.events.clear()
        self.session.send("second")
        self.finished()
        self.assertEqual(thread, self.session.thread_id)
        self.assertFalse(any(k == "thread" for k, _ in self.events))
        self.assertTrue(any(k == "assistant" and d["text"] == "second" for k, d in self.events))
        self.session.reset()
        self.assertIsNone(self.session.thread_id)

    def test_approval_and_question(self):
        for prompt, request_id, result in [
            ("approval", "approval-1", {"decision": "decline"}),
            ("question", "question-1", {"answers": {"name": {"answers": ["test"]}}}),
        ]:
            self.events.clear()
            self.session.send(prompt)
            self.wait(lambda: any(k == "request" for k, _ in self.events))
            self.assertTrue(self.session.answer(request_id, result))
            self.assertFalse(self.session.answer(request_id, result))
            self.finished()

    def test_paths_in_prompt_and_turn_scoped_permission_grants(self):
        self.session.send("settings")
        self.finished()
        settings = self.settings()
        self.assertEqual(settings["thread"]["sandbox"], "read-only")
        self.assertEqual(settings["turn"]["sandboxPolicy"], {"type": "readOnly", "networkAccess": False})
        for permission in ({"fileSystem": {"write": ["/tmp/test-output.pdf"]}}, {}):
            self.events.clear()
            self.session.send("permissions")
            self.wait(lambda: any(k == "request" for k, _ in self.events))
            self.session.answer("permissions-1", {"permissions": permission, "scope": "turn"})
            self.finished()
            response = json.loads(next(d["text"] for k, d in self.events if k == "assistant"))
            self.assertEqual(response["result"], {"permissions": permission, "scope": "turn"})

    def test_interrupt_and_busy_guard(self):
        self.session.send("wait")
        self.wait(lambda: self.session._turn_id is not None)
        with self.assertRaises(RpcError):
            self.session.send("another")
        with self.assertRaises(RpcError):
            self.session.reset()
        self.session.interrupt()
        self.finished()
        self.assertFalse(self.session.busy)

    def test_paths_do_not_automatically_change_sandbox(self):
        self.session.send("settings")
        self.finished()
        self.assertEqual(self.settings()["turn"]["sandboxPolicy"]["type"], "readOnly")
        self.events.clear()
        self.session.send("/tmp/output.pdf を作成して")
        self.finished()
        self.assertEqual(self.session._server.cwd, self.session._scratch.name)

    def test_unknown_request_is_rejected(self):
        self.session.send("unknown")
        self.finished()
        self.assertTrue(any(k == "error" and "unknown/request" in d["message"] for k, d in self.events))
        self.assertTrue(any(k == "assistant" and "-32601" in d["text"] for k, d in self.events))

    def test_disconnect_and_reconnect(self):
        self.session.send("exit")
        self.finished()
        self.session.reset()
        self.events.clear()
        self.session.send("reconnected")
        self.finished()
        self.assertTrue(any(k == "assistant" for k, _ in self.events))

    def test_no_shell_or_persistent_approval_override(self):
        command = server_command("/bin/codex")
        self.assertEqual(command[:3], ["/bin/codex", "app-server", "--stdio"])
        self.assertIn('approvals_reviewer="user"', command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

if __name__ == "__main__":
    unittest.main()
