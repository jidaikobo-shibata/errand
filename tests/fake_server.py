"""Deterministic wire peer; tests exercise the actual subprocess transport."""
import json
import sys

turn = 0
pending = None
thread_params = {}

def model(name, efforts, default, **extra):
    return {"id": "picker-" + name, "model": name, "displayName": name.title(),
            "description": "Synthetic model", "hidden": False, "isDefault": False,
            "inputModalities": ["text"], "defaultReasoningEffort": default,
            "supportedReasoningEfforts": [{"reasoningEffort": effort, "description": effort + " description"}
                                           for effort in efforts], **extra}
def send(value):
    print(json.dumps(value), flush=True)
def reply(message, result):
    send({"id": message["id"], "result": result})
def event(method, **params):
    send({"method": method, "params": {"threadId": "thread-1", **params}})
def complete(text="回答"):
    event("item/agentMessage/delta", itemId=f"answer-{turn}", delta=text)
    event("item/completed", item={"id": f"answer-{turn}", "type": "agentMessage", "text": text})
    event("turn/completed", turn={"id": f"turn-{turn}", "status": "completed"})

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        reply(message, {"userAgent": "fake"})
    elif method == "model/list":
        if "--fail-models" in sys.argv:
            send({"id": message["id"], "error": {"code": -32000, "message": "catalogue unavailable"}})
        elif "--cycle-models" in sys.argv:
            reply(message, {"data": [], "nextCursor": "same"})
        elif message["params"].get("cursor") == "page-2":
            reply(message, {"data": [model("fake-steady", ["medium"], "medium"),
                                     model("fake-no-effort", [], "medium")], "nextCursor": None})
        else:
            reply(message, {"data": [model("fake-fast", ["low", "high", "future-effort"], "high"),
                                     model("fake-hidden", ["low"], "low", hidden=True),
                                     model("fake-image", ["low"], "low", inputModalities=["image"])],
                            "nextCursor": "page-2"})
    elif method == "thread/start":
        thread_params = message["params"]
        default = "fake-hidden" if "--default-hidden" in sys.argv else "fake-unlisted" if "--default-unlisted" in sys.argv else "fake-fast"
        reply(message, {"thread": {"id": "thread-1"}, "model": thread_params.get("model", default),
                        "reasoningEffort": thread_params.get("config", {}).get("model_reasoning_effort", "low")})
    elif method == "turn/start":
        turn += 1
        text = message["params"]["input"][0]["text"]
        reply(message, {"turn": {"id": f"turn-{turn}", "status": "inProgress"}})
        event("turn/started", turn={"id": f"turn-{turn}"})
        if text == "approval":
            pending = "approval"
            send({"id": "approval-1", "method": "item/commandExecution/requestApproval", "params": {
                "threadId": "thread-1", "turnId": f"turn-{turn}", "itemId": "cmd-1", "command": "pwd", "startedAtMs": 0}})
        elif text == "question":
            pending = "question"
            send({"id": "question-1", "method": "item/tool/requestUserInput", "params": {
                "threadId": "thread-1", "turnId": f"turn-{turn}", "itemId": "q-1", "isBlocking": True,
                "questions": [{"id": "name", "header": "名前", "question": "名前は？"}]}})
        elif text == "permissions":
            pending = "permissions"
            send({"id": "permissions-1", "method": "item/permissions/requestApproval", "params": {
                "threadId": "thread-1", "turnId": f"turn-{turn}", "itemId": "p-1", "startedAtMs": 0,
                "cwd": "/tmp", "reason": "検証出力の作成", "permissions": {"fileSystem": {"write": ["/tmp/test-output.pdf"]}}}})
        elif text == "unknown":
            pending = "unknown"
            send({"id": "unknown-1", "method": "unknown/request", "params": {}})
        elif text == "wait":
            pass
        elif text == "exit":
            sys.exit(0)
        elif text == "settings":
            complete(json.dumps({"thread": thread_params, "turn": message["params"]}))
        else:
            complete(text)
    elif method == "turn/interrupt":
        reply(message, {})
        event("turn/completed", turn={"id": f"turn-{turn}", "status": "interrupted"})
    elif method is None and pending:
        complete(json.dumps(message, ensure_ascii=False))
        pending = None
