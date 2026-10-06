#!/usr/bin/env python3
"""Opt-in actual Codex catalogue and model/effort check; no user document input."""
import hashlib
import argparse
from pathlib import Path
import time

from live_smoke import Check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue-only", action="store_true", help="Resolve current settings without sending a prompt")
    args = parser.parse_args()
    config = Path.home() / ".codex/config.toml"
    digest = hashlib.sha256(config.read_bytes()).digest() if config.exists() else None
    check = Check()
    try:
        check.session.load_models()
        deadline = time.monotonic() + 90
        with check.changed:
            while not any(k == "models_state" and not d["loading"] for k, d in check.events):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Model discovery timed out")
                check.changed.wait(min(remaining, 10))
        errors = [d["message"] for k, d in check.events if k == "models_error"]
        if errors:
            raise RuntimeError("; ".join(errors))
        catalogue = next(d for k, d in check.events if k == "models")
        models = catalogue["models"]
        current = next(entry for entry in models if entry["model"] == catalogue["default_model"])
        print(f"PASS: current model={current['model']} effort={catalogue['default_effort']}", flush=True)
        if args.catalogue_only:
            current_digest = hashlib.sha256(config.read_bytes()).digest() if config.exists() else None
            assert current_digest == digest, "Codex config changed"
            print("PASS: current selection resolved without a prompt; config.toml unchanged", flush=True)
            return
        candidates = [entry for entry in models if entry.get("supportedReasoningEfforts")]
        if not candidates:
            raise RuntimeError("No selectable reasoning model")
        entry = next((entry for entry in candidates if entry.get("isDefault")), candidates[0])
        efforts = [option["reasoningEffort"] for option in entry["supportedReasoningEfforts"]]
        effort = "low" if "low" in efforts else efforts[0]
        print(f"PASS: discovered {len(models)} selectable models", flush=True)
        print(f"Checking model={entry['model']} effort={effort}", flush=True)
        check.turn("ツールを使わず、『設定確認』とだけ答えてください。", model=entry["model"], effort=effort)
        thread = next(data for kind, data in check.events if kind == "thread")
        assert thread["model"] == entry["model"], thread
        assert thread["reasoning_effort"] == effort, thread
        current = hashlib.sha256(config.read_bytes()).digest() if config.exists() else None
        assert current == digest, "Codex config changed"
        print("PASS: selected model and effort applied; config.toml unchanged", flush=True)
    finally:
        check.session.close()


if __name__ == "__main__":
    main()
