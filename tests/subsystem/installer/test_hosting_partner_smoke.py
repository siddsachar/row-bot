"""The hosting smoke (H9) runs the image as a host does, and its fake model is one Row-Bot can use.

The smoke itself runs in container.yml on amd64 and arm64; these cover what it
decides without Docker: the host's run command, the log rule and the memory
summary, and the fake model server it starts inside the container.
"""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import ThreadingHTTPServer
from urllib import error, request

import pytest

from scripts import dummy_openai_endpoint as fake_model
from scripts import smoke_hosting_partner as smoke


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]

CODE = "ABCDEFGHJKMN"
KEY = "0123456789abcdef" * 4


def test_the_host_runs_one_container_with_one_volume_one_port_and_no_secret_on_the_command_line() -> None:
    args = smoke.partner_run_args(
        image="row-bot:verify-amd64", container_name="row-bot-smoke-a1b2c3d4",
        volume_name="row-bot-smoke-data-a1b2c3d4", ownership="a1b2c3d4", port=8155, env_file="/tmp/host.env",
    )

    assert args[:3] == ("docker", "run", "--detach") and args[-1] == "row-bot:verify-amd64"
    assert [args[index + 1] for index, arg in enumerate(args) if arg == "--mount"] == [
        "type=volume,src=row-bot-smoke-data-a1b2c3d4,dst=/data"
    ]
    assert [args[index + 1] for index, arg in enumerate(args) if arg == "--publish"] == ["127.0.0.1:8155:8080"]
    # The image's own user, entrypoint and health check, with no hardening a host may not add.
    assert not {"--user", "--entrypoint", "--read-only", "--init", "--health-cmd", "--cap-drop"} & set(args)
    assert args[args.index("--env-file") + 1] == "/tmp/host.env"
    assert not any("ROW_BOT_SETUP_CODE" in arg or "ROW_BOT_SECRET_STORE_KEY" in arg for arg in args)
    assert {"ROW_BOT_HOSTED_BY=pikapods", "ROW_BOT_PUBLIC_URL=http://127.0.0.1:8155"} <= set(args)


def test_the_log_may_show_the_code_once_at_the_first_start_and_never_a_secret() -> None:
    first_start = "Row-Bot server mode listening\n" + "=" * 72 + "\nRow-Bot setup code: ABCD-EFGH-JKMN (open your Row-Bot and enter it)\n"
    restarted = first_start + "Row-Bot server mode listening\n"

    smoke.check_container_log(restarted, code=CODE, secret_values=(KEY,))
    failures = {
        "": "does not show the setup code",
        restarted + "Row-Bot setup code: ABCD-EFGH-JKMN\n": "after the first start's code line",
        restarted + f"claimed with {CODE}\n": "after the first start's code line",
        restarted + f"key={KEY}\n": "A secret appears",
    }
    for log, reason in failures.items():
        with pytest.raises(smoke.SmokeError, match=reason) as raised:
            smoke.check_container_log(log, code=CODE, secret_values=(KEY,))
        assert CODE not in str(raised.value) and KEY not in str(raised.value)


def test_the_summary_flags_a_memory_high_water_mark_over_the_release_budget() -> None:
    report, within = smoke.memory_report("x86_64", [1_150_000_000, 1_420_000_000])
    assert within and "**1.42 GB**" in report and "under 3.2 GB." in report

    report, within = smoke.memory_report("aarch64", [3_300_000_000, None])
    assert not within and "**3.30 GB**" in report and "**over**" in report

    report, within = smoke.memory_report("x86_64", [None, None])
    assert within and "not measured" in report


@pytest.fixture
def model_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake_model.DummyOpenAIHandler)
    server.api_key_sha256 = hashlib.sha256(b"fake-model-key").hexdigest()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    server.server_close()


def _post(url: str, body: dict, key: str = "fake-model-key") -> tuple[int, str]:
    outbound = request.Request(url + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                               headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with request.urlopen(outbound, timeout=5) as response:
            return response.status, response.read().decode()
    except error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_the_fake_model_answers_only_the_saved_key_and_calls_the_probe_tool(model_server: str) -> None:
    probe = {"model": "row-bot-dummy-chat", "messages": [{"role": "user", "content": "Call the tool."}],
             "tools": [{"type": "function", "function": {"name": "row_bot_probe_echo", "parameters": {}}}]}

    assert _post(model_server, probe, key="another-key")[0] == 401

    status, text = _post(model_server, probe)
    call = json.loads(text)["choices"][0]["message"]["tool_calls"][0]
    assert status == 200 and call["function"]["name"] == "row_bot_probe_echo"
    assert json.loads(call["function"]["arguments"]) == {"value": "ok"}

    status, text = _post(model_server, {**probe, "stream": True})
    chunks = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]
    assert status == 200 and chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "row_bot_probe_echo"

    answered = {**probe, "messages": [*probe["messages"], {"role": "assistant", "tool_calls": [call]},
                                      {"role": "tool", "tool_call_id": call["id"], "content": "{\"value\":\"ok\"}"}]}
    status, text = _post(model_server, answered)
    assert status == 200 and json.loads(text)["choices"][0]["message"]["content"]

    status, text = _post(model_server, {"model": "row-bot-dummy-chat", "stream": True,
                                        "messages": [{"role": "user", "content": "Hello"}]})
    assert status == 200 and smoke.FAKE_MODEL_REPLY == "".join(
        json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
        for line in text.splitlines() if line.startswith("data: {")
    )
