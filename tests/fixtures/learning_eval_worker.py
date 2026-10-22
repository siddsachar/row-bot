"""A fake Learning evaluation worker (L2c2): the same lines on stdin/stdout as the real one.

Run by path with the app's base interpreter and ``-I`` (standard library only). It reads its
``start`` spec, answers ``ready``, then answers each ``task`` with a deterministic result: tasks whose
id ends in an odd digit pass for the arm named ``new``, every other arm fails them. Options in the
spec's ``fake`` object:

- ``spawn_child``: start a child process that waits forever (its pid in ``work/child.pid``);
- ``refresh``: report a refreshed sign-in once (a new access token);
- ``leak``: write the first credential's value to its log (standard error), to test redaction;
- ``refuse``: answer ``failed`` instead of ``ready``.

On standard input closing (the app is gone) it exits.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def emit(frame: dict) -> None:
    sys.stdout.write(json.dumps(frame) + "\n")
    sys.stdout.flush()


def main() -> int:
    first = sys.stdin.readline()
    spec = json.loads(first).get("spec") or {}
    fake = spec.get("fake") or {}
    if fake.get("refuse"):
        emit({"type": "failed", "reason": "refused"})
        return 2
    if fake.get("spawn_child"):
        child = subprocess.Popen([sys.executable, "-I", "-c", "import time\nwhile True: time.sleep(1)"])
        with open(os.path.join(os.getcwd(), "child.pid"), "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    credentials = spec.get("credentials") or []
    if fake.get("leak") and credentials:
        value = next(iter((credentials[0].get("values") or {}).values()), "")
        sys.stderr.write(f"debug: using {value}\n")
        sys.stderr.flush()
    with open(os.path.join(spec["data"], "seen.json"), "w", encoding="utf-8") as handle:
        json.dump({"arms": [arm["name"] for arm in spec.get("arms") or ()], "tools": spec.get("tools") or [],
                   "credential_kinds": [item.get("kind") for item in credentials]}, handle)
    emit({"type": "ready", "tools": spec.get("tools") or [], "notes": ["fake worker"]})
    refreshed = False
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("type") == "stop":
            return 0
        if message.get("type") != "task":
            continue
        if fake.get("refresh") and not refreshed:
            refreshed = True
            emit({"type": "refreshed", "provider": "codex", "bundle": {"access_token": "fresh-access-token",
                                                                      "refresh_token": "fresh-refresh-token"}})
        task = message["task"]
        arm = message["arm"]
        passed = arm == "new" and task["id"][-1:] in "13579"
        tokens = 1200 if arm == "main" else 900
        emit({"type": "result", "id": message["id"], "budget": message.get("budget"), "case": {
            "task_id": task["id"], "suite": "pack", "kind_of_work": "", "status": "passed" if passed else "failed",
            "check": task["verifier"]["kind"], "tokens": tokens, "local_tokens": 0 if arm == "main" else tokens,
            "steps": 2, "seconds": 1.5, "detail": ""},
            "usage": [{"provider": "openai" if arm == "main" else "ollama", "model": arm, "input": tokens - 100,
                       "output": 100, "cache_read": 0, "cache_write": 0, "reasoning": 0}]})
        time.sleep(0.001)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
