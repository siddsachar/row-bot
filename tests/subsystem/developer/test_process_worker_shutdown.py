"""Exercise the stdlib bootstrap without importing the application runtime."""
from pathlib import Path
import json
import subprocess
import sys
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux supervised bootstrap")]


@pytest.mark.parametrize("missing", [True, False])
def test_supervised_worker_exits_with_control_input_still_open(missing):
    worker = Path(__file__).resolve().parents[3] / "src/row_bot/developer/process_worker.py"
    argv = ["row-bot-disposable-nonexistent-executable"] if missing else [sys.executable, "-c", "print('done')"]
    process = subprocess.Popen([sys.executable, "-I", "-S", "-B", str(worker)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        request = {"argv": argv, "owner_id": str(uuid4()), "container_id": "0" * 64, "key": "ab" * 32}
        process.stdin.write(json.dumps(request).encode() + b"\n")
        process.stdin.flush()
        # Keep control stdin open: normal completion must not depend on EOF.
        assert process.wait(timeout=15) == 0
        assert process.stderr.read() == b""
        frames = [json.loads(line) for line in process.stdout]
        final = frames[-1]
        assert final["event"] == ("failed" if missing else "exited")
        if missing:
            assert final["code"] == "process_start_failed"
        else:
            assert final["exit_code"] == 0
        assert final["receipt"]["payload"]["quiesced"] is True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
