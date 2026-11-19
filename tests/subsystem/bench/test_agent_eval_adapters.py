"""Row-Bot as a benchmark's agent (lane E) behaves without any provider or benchmark install.

The bridge runs over a real loopback socket with a scripted benchmark on one side and a fake
Row-Bot turn on the other; scoring reads hand-made tau2 result files; the tau2 side's message
mapping runs on stand-in message classes. The live runs are opt-in (``tests/e2e/test_agent_evals.py``).
"""
from __future__ import annotations

import gzip
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.e2e.bench.agent_evals import bridge, cli, wire
from tests.e2e.bench.agent_evals.host import RuntimeHost, model_ref
from tests.e2e.bench.agent_evals.tau2 import scoring
from tests.e2e.bench.agent_evals.worker import DEAD_PROXY, runner_env
from tests.e2e.bench.trajectory import Step, read_jsonl

pytestmark = pytest.mark.subsystem

GET_USER = {"type": "function", "function": {
    "name": "get_user_details", "description": "Get the details of a user.",
    "parameters": {"type": "object", "properties": {"user_id": {"type": "string", "description": "The user id."}},
                   "required": ["user_id"]}}}
CANCEL = {"type": "function", "function": {
    "name": "cancel_reservation", "description": "Cancel a reservation.",
    "parameters": {"type": "object", "properties": {"reservation_id": {"type": "string"}},
                   "required": ["reservation_id"]}}}


@dataclass
class FakeHost:
    """Row-Bot's side, scripted: each turn calls the benchmark's tools through the router, then replies."""

    router: bridge.ToolRouter
    script: list[list[tuple[str, dict[str, Any]]]] = field(default_factory=list)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    ended: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = field(default_factory=dict)
    completions: list[tuple[str, str, str, int]] = field(default_factory=list)
    fail_with: Exception | None = None

    def new_session(self, session, tools, instructions, meta):
        self.sessions[session] = {"tools": tools, "instructions": instructions, "meta": meta}

    def turn(self, session, prompt):
        if self.fail_with is not None:
            raise self.fail_with
        results = [self.router.call(name, arguments) for name, arguments in (self.script.pop(0) if self.script else [])]
        return Step(kind="turn", prompt=prompt, answer=f"Done: {' | '.join(results)}")

    def complete(self, purpose, model, effort, messages):
        self.completions.append((purpose, model, effort, len(messages)))
        return "I'd like to cancel my booking."

    def end(self, session, outcome, calls):
        self.ended[session] = (outcome, calls)


def _serve(host: FakeHost, allowed: list[str], **kwargs: Any) -> tuple[bridge.BridgeServer, threading.Thread]:
    server = bridge.BridgeServer(host, host.router, allowed, **kwargs)
    thread = threading.Thread(target=server.serve, kwargs={"accept_timeout_s": 10}, daemon=True)
    thread.start()
    return server, thread


# ── The benchmark's tools inside Row-Bot ────────────────────────────────────


def test_the_benchmarks_tools_reach_the_model_with_their_own_schemas_and_run_on_the_benchmark() -> None:
    from langchain_core.utils.function_calling import convert_to_openai_tool

    calls: list[tuple[str, dict[str, Any]]] = []
    tools = bridge.proxy_tools([GET_USER, CANCEL], lambda name, arguments: calls.append((name, arguments)) or "ok")
    assert [convert_to_openai_tool(tool) for tool in tools] == [GET_USER, CANCEL]
    assert tools[1].invoke({"reservation_id": "ZFA04Y"}) == "ok"
    assert calls == [("cancel_reservation", {"reservation_id": "ZFA04Y"})]
    assert bridge.ToolRouter().call("cancel_reservation", {}).startswith("Error:")  # no turn running
    with pytest.raises(ValueError):
        bridge.proxy_tools([{"type": "function", "function": {"description": "no name"}}], lambda *_: "")


# ── One conversation over the bridge ────────────────────────────────────────


def test_a_turn_hands_out_each_tool_call_in_order_and_ends_with_the_reply() -> None:
    router = bridge.ToolRouter()
    host = FakeHost(router, script=[[("get_user_details", {"user_id": "mia_li_3668"}),
                                     ("cancel_reservation", {"reservation_id": "ZFA04Y"})]])
    server, thread = _serve(host, ["get_user_details", "cancel_reservation"])
    client = wire.BridgeClient(server.address, timeout_s=10)
    assert client.start("s1", [GET_USER, CANCEL], "<policy>Be kind.</policy>", {"task_id": "7"}) == {"ok": True}
    first = client.user("s1", "Cancel ZFA04Y please, I'm mia_li_3668.")
    assert (first["type"], first["name"], first["arguments"]) == ("tool_call", "get_user_details",
                                                                   {"user_id": "mia_li_3668"})
    second = client.tool_result("s1", first["id"], '{"name": "Mia"}')
    assert (second["type"], second["name"]) == ("tool_call", "cancel_reservation")
    assert client.tool_result("s1", "call_unknown", "x")["kind"] == "protocol"  # only the waiting call is answered
    reply = client.tool_result("s1", second["id"], "Reservation not found", error=True)
    assert reply == {"type": "message", "text": 'Done: {"name": "Mia"} | Error: Reservation not found'}
    assert client.complete("user_simulator", "codex:gpt-5.2", "low",
                           [{"role": "system", "content": "You are Mia."}]) == {"text": "I'd like to cancel my booking."}
    client.end("s1", {"task_id": "7", "reward": 0.0})
    client.close()
    thread.join(5)
    assert host.sessions["s1"]["instructions"] == "<policy>Be kind.</policy>"
    outcome, calls = host.ended["s1"]
    assert outcome == {"task_id": "7", "reward": 0.0}
    assert [(call["name"], call["error"]) for call in calls] == [("get_user_details", False),
                                                                  ("cancel_reservation", True)]
    assert host.completions == [("user_simulator", "codex:gpt-5.2", "low", 1)]
    assert server.counts == {"sessions": 1, "turns": 1, "tool_calls": 2, "completions": 1}


def test_the_bridge_refuses_a_wrong_token_and_tools_that_were_not_pinned() -> None:
    router = bridge.ToolRouter()
    server, thread = _serve(FakeHost(router), ["get_user_details"])
    with pytest.raises(wire.BridgeError, match="wrong bridge token"):
        wire.BridgeClient(f"{server.port}:not-the-secret", timeout_s=10)
    thread.join(5)
    server, thread = _serve(FakeHost(router), ["get_user_details"])
    client = wire.BridgeClient(server.address, timeout_s=10)
    with pytest.raises(wire.BridgeError, match="not pinned"):
        client.start("s1", [GET_USER, CANCEL], "", {})
    client.close()
    thread.join(5)
    with pytest.raises(wire.BridgeError):
        wire.BridgeClient("no-port", timeout_s=1)


def test_a_usage_limit_stops_the_run_and_every_later_session_is_refused_at_once() -> None:
    router = bridge.ToolRouter()
    host = FakeHost(router, fail_with=bridge.Stopped("limit", "429: usage limit reached, try again in 2h"))
    server, thread = _serve(host, ["get_user_details"])
    client = wire.BridgeClient(server.address, timeout_s=10)
    client.start("s1", [GET_USER], "", {})
    reply = client.user("s1", "hello")
    assert wire.stopping(reply) and reply["kind"] == "limit"
    host.fail_with = None
    assert wire.stopping(client.start("s2", [GET_USER], "", {}))  # no model call is made for it
    assert wire.stopping(client.complete("user_simulator", "codex:gpt-5.2", "low", []))
    assert "s2" not in host.sessions and host.completions == []
    client.close()
    thread.join(5)
    assert server.stopped is not None and server.stopped.kind == "limit"


def test_the_stop_file_lets_the_running_session_finish_and_starts_no_new_one(tmp_path) -> None:
    router = bridge.ToolRouter()
    host = FakeHost(router)
    server = bridge.BridgeServer(host, router, ["get_user_details"])
    stop = tmp_path / "STOP"
    thread = threading.Thread(target=server.serve, kwargs={"accept_timeout_s": 10, "stop": stop.exists},
                              daemon=True)
    thread.start()
    client = wire.BridgeClient(server.address, timeout_s=10)
    client.start("s1", [GET_USER], "", {})
    stop.write_text("stop", encoding="utf-8")
    assert client.user("s1", "hi")["type"] == "message"
    reply = client.start("s2", [GET_USER], "", {})
    assert wire.stopping(reply) and reply["kind"] == "stop"
    client.close()
    thread.join(5)


def test_the_benchmarks_runner_gets_no_secret_no_data_folder_and_no_way_out_but_loopback() -> None:
    env = runner_env({"PATH": "/bin", "OPENAI_API_KEY": "sk-live", "ANTHROPIC_AUTH_TOKEN": "t", "HF_TOKEN": "h",
                      "ROW_BOT_DATA_DIR": "/scratch", "ROW_BOT_LIVE_EVAL_CODEX_PROFILE": "/profile",
                      "JUDGE_API_KEY": "j"}, "4321:secret")
    assert env["PATH"] == "/bin" and env[wire.TOKEN_ENV] == "4321:secret"
    assert not {"OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN", "HF_TOKEN", "JUDGE_API_KEY"} & set(env)
    assert [key for key in env if key.startswith("ROW_BOT_")] == [wire.TOKEN_ENV]
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == DEAD_PROXY and "127.0.0.1" in env["NO_PROXY"]
    assert env["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"


# ── The host on a runtime ───────────────────────────────────────────────────


class _Runtime:
    def __init__(self, steps: list[Step]) -> None:
        self.steps = steps
        self.config = SimpleNamespace(to_dict=lambda: {"provider": "codex", "model": "gpt-5.2"})
        self.local_model, self.notes = {}, []

    def turn(self, thread_id, prompt, name=""):
        step = self.steps.pop(0)
        step.prompt, step.name = prompt, name
        return step


def test_each_session_is_one_trajectory_with_only_its_own_turns_tool_calls(tmp_path) -> None:
    from tests.e2e.bench.agent_evals.bridge import Stopped
    from tests.e2e.bench.runtime import ProviderLimit
    from tests.e2e.bench.trajectory import Trajectory

    first = [{"name": "get_user_details", "args": {"user_id": "u1"}}]
    runtime = _Runtime([Step(kind="turn", answer="Hi Mia", tool_calls=list(first)),
                        Step(kind="turn", answer="Cancelled", tool_calls=first + [{"name": "cancel_reservation"}])])
    host = RuntimeHost(runtime, "tau2-bench", tmp_path / "trajectories.jsonl")  # type: ignore[arg-type]
    host.threads["s1"], host.trajectories["s1"] = "t1", Trajectory(task_id="s1", benchmark="tau2-bench")
    host.turn("s1", "I'm Mia")
    host.turn("s1", "Cancel it")
    host.end("s1", {"task_id": "7", "reward": 1.0}, [])
    (saved,) = read_jsonl(tmp_path / "trajectories.jsonl")
    turns = [step for step in saved.steps if step.kind == "turn"]
    assert [step.name for step in turns] == ["turn 1", "turn 2"]
    assert [[call["name"] for call in step.tool_calls] for step in turns] == [["get_user_details"],
                                                                              ["cancel_reservation"]]
    assert saved.status == "done" and saved.steps[-1].metrics["reward"] == 1.0

    class Limited(_Runtime):
        def turn(self, *_args, **_kwargs):
            raise ProviderLimit("429 usage limit reached")

    host = RuntimeHost(Limited([]), "tau2-bench", tmp_path / "limited.jsonl")  # type: ignore[arg-type]
    host.threads["s2"], host.trajectories["s2"] = "t2", Trajectory(task_id="s2", benchmark="tau2-bench")
    with pytest.raises(Stopped) as stopped:
        host.turn("s2", "hello")
    assert stopped.value.kind == "limit"
    host.flush()
    (limited,) = read_jsonl(tmp_path / "limited.jsonl")
    assert limited.status == "provider_limit"
    assert model_ref("codex:gpt-5.2") == "model:codex:gpt-5.2"
    with pytest.raises(ValueError):
        model_ref("gpt-5.2")


def test_the_benchmarks_instructions_reach_the_conversation_as_its_agent_profile(tmp_path, reload_for_data_dir):
    threads, _tasks = reload_for_data_dir(tmp_path / "data", "row_bot.threads", "row_bot.tasks")
    import row_bot.agent as agent

    runtime = _Runtime([])
    runtime.config = SimpleNamespace(to_dict=lambda: {}, approval_mode="full", model_ref="model:codex:gpt-5.2",
                                     reasoning_effort="")
    host = RuntimeHost(runtime, "tau2-bench", tmp_path / "t.jsonl")  # type: ignore[arg-type]
    policy = "<instructions>\nYou are a customer service agent.\n</instructions>\n<policy>\n# Airline policy\n</policy>"
    host.new_session("s1", [GET_USER], policy, {"task_id": "3"})
    host.new_session("s2", [GET_USER], policy, {"task_id": "4"})
    first, second = host.threads["s1"], host.threads["s2"]
    context = agent._agent_profile_system_context(first)
    assert "Profile instructions:" in context and "# Airline policy" in context
    assert agent._agent_profile_system_context(second) == context  # one profile for the same instructions
    assert threads.incognito_state(first).incognito  # nothing from a task is ever extracted into memory
    assert host.trajectories["s1"].config["tools"] == ["get_user_details"]


# ── tau2's side ─────────────────────────────────────────────────────────────


def test_row_bots_acts_become_tau2_assistant_messages(monkeypatch) -> None:
    import sys

    monkeypatch.syspath_prepend(str(Path(cli.TAU2_SIDE).parent.parent))
    sys.modules.pop("rowbot_tau2", None)
    monkeypatch.syspath_prepend(str(Path(cli.TAU2_SIDE).parent))
    import rowbot_tau2

    def message(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    def call(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    acted = rowbot_tau2.to_assistant_message({"type": "tool_call", "id": "c1", "name": "get_user_details",
                                              "arguments": {"user_id": "u1"}}, "s1", message, call)
    assert acted == {"role": "assistant", "content": None, "raw_data": {"rowbot_session": "s1"}, "tool_calls": [
        {"id": "c1", "name": "get_user_details", "arguments": {"user_id": "u1"}, "requestor": "assistant"}]}
    assert rowbot_tau2.to_assistant_message({"type": "message", "text": "Done."}, "s1", message, call)["content"] \
        == "Done."
    assert rowbot_tau2.to_assistant_message({"type": "error", "kind": "turn"}, "s1", message, call) is None
    sys.modules.pop("rowbot_tau2", None)


# ── Scores ──────────────────────────────────────────────────────────────────


def _results(rows: list[tuple[str, float, str]], *, published: bool = False) -> dict[str, Any]:
    if published:
        return {"simulation_index": [{"task_id": task, "trial": number, "reward": reward, "termination_reason": why}
                                     for number, (task, reward, why) in enumerate(rows)]}
    return {"simulations": [{"task_id": task, "reward_info": {"reward": reward}, "termination_reason": why}
                            for task, reward, why in rows]}


def test_pass_hat_k_is_tau2s_estimator_over_trials() -> None:
    rewards = scoring.trial_rewards(_results([("1", 1.0, "user_stop"), ("1", 1.0, "user_stop"), ("1", 0.0, "max_steps"),
                                              ("1", 1.0, "user_stop"), ("2", 0.0, "user_stop"), ("2", 1.0, "user_stop"),
                                              ("2", 0.0, "user_stop"), ("2", 0.0, "user_stop")]))
    assert scoring.pass_hat_k(rewards, 1) == pytest.approx((3 / 4 + 1 / 4) / 2)
    assert scoring.pass_hat_k(rewards, 2) == pytest.approx((3 / 6 + 0) / 2)
    assert scoring.pass_hat_k(rewards, 4) == pytest.approx(0.0)
    assert scoring.pass_hat_k(rewards, 5) is None
    assert scoring.pass_hat_k(rewards, 1, tasks=["2", "unknown"]) == pytest.approx(0.25)
    stopped = scoring.trial_rewards(_results([("1", 0.0, "provider_limit")]), exclude=["provider_limit"])
    assert stopped == {}


def test_our_run_is_compared_task_by_task_with_the_published_trials_on_the_same_tasks(tmp_path) -> None:
    published = tmp_path / "results.json"
    published.write_bytes(gzip.compress(json.dumps(_results(
        [("1", 1.0, ""), ("1", 1.0, ""), ("2", 1.0, ""), ("2", 0.0, ""), ("3", 0.0, ""), ("3", 0.0, "")],
        published=True)).encode("utf-8")))
    theirs = scoring.trial_rewards(scoring.load_results(published))
    ours = scoring.trial_rewards(_results([("1", 0.0, ""), ("2", 1.0, "")]))
    comparison = scoring.Comparison(tasks=["1", "2", "3"], ours=ours, theirs=theirs, k_max=2)
    summary = comparison.summary()
    assert (summary["tasks"], summary["tasks_run"]) == (3, 2)
    assert summary["worse"] == ["1"] and summary["better"] == ["2"]
    assert summary["theirs_on_ours"]["pass^1"] == pytest.approx((1.0 + 0.5) / 2)  # only the tasks we ran
    assert summary["theirs"]["pass^1"] == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    text = scoring.render(comparison, title="airline / train", ours_label="Row-Bot", theirs_label="GPT-5.2 high")
    assert "| 1 | 0/1 | 2/2 |" in text and "Worse than theirs on 1 task(s): 1" in text


def test_only_the_published_setup_is_labelled_matched() -> None:
    gpt52 = "openrouter:openai/gpt-5.2"
    args = SimpleNamespace(model=gpt52, reasoning_effort="high", user_model=gpt52,
                           user_effort="low", trials=4, seed=300, max_steps=200, domain="airline", nl_judge="")
    assert cli.matched_label(args).startswith("matched")
    for change in ({"model": "codex:gpt-6.1-sol"}, {"trials": 1}, {"user_effort": "high"},
                   {"user_model": "codex:gpt-5.6-sol"}, {"domain": "retail"},
                   {"domain": "retail", "nl_judge": "codex:gpt-5.6-sol"}):
        assert cli.matched_label(SimpleNamespace(**{**vars(args), **change})) == "diagnostic, not matched"
    assert cli.matched_label(SimpleNamespace(**{**vars(args), "domain": "retail",
                                                "nl_judge": "openrouter:openai/gpt-4.1"})).startswith("matched")


def _tau2_checkout(tmp_path: Path) -> Path:
    for domain, splits, judged in (("airline", {"train": ["0", "1", "2"], "test": ["3"], "base": ["0", "1", "2", "3"]},
                                    set()),
                                   ("retail", {"train": ["0", "1"], "test": ["2"], "base": ["0", "1", "2"]}, {"1"})):
        folder = tmp_path / "tau2" / "data" / "tau2" / "domains" / domain
        folder.mkdir(parents=True)
        (folder / "split_tasks.json").write_text(json.dumps(splits), encoding="utf-8")
        # Airline's tasks carry NL assertions too, but their reward never counts them: no judge call.
        basis = ["DB", "NL_ASSERTION"] if domain == "retail" else ["DB", "COMMUNICATE"]
        (folder / "tasks.json").write_text(json.dumps([
            {"id": task, "evaluation_criteria": {"reward_basis": basis,
                                                 "nl_assertions": ["Agent refunds"] if task in judged or
                                                 domain == "airline" else None}}
            for task in splits["base"]]), encoding="utf-8")
    return tmp_path / "tau2"


def test_the_plan_counts_conversations_and_the_tasks_a_paid_judge_would_grade(tmp_path, capsys) -> None:
    root = _tau2_checkout(tmp_path)
    assert cli.main(["tau2", "plan", "--tau2-dir", str(root), "--domain", "retail", "--split", "train",
                     "--trials", "4"]) == 0
    out = capsys.readouterr().out
    assert "2 task(s) x 4 trial(s) = 8 conversation(s)" in out and "1 task(s) graded" in out
    assert cli.main(["tau2", "plan", "--tau2-dir", str(root), "--domain", "airline", "--limit", "2"]) == 0
    assert "2 task(s) x 1 trial(s)" in capsys.readouterr().out
    with pytest.raises(ValueError, match="not in the train split"):
        cli.main(["tau2", "plan", "--tau2-dir", str(root), "--domain", "airline", "--task-ids", "3"])


def test_a_run_needs_the_bench_switch_a_free_lane_and_a_judge_for_judged_tasks(tmp_path, monkeypatch, capsys):
    root = _tau2_checkout(tmp_path)
    base = ["tau2", "run", "--tau2-dir", str(root), "--domain", "retail", "--model", "ollama:qwen3.8:27b",
            "--user-model", "ollama:qwen3.8:27b"]
    monkeypatch.delenv("ROW_BOT_BENCH", raising=False)
    assert cli.main(base) == 2 and "ROW_BOT_BENCH=1" in capsys.readouterr().out
    monkeypatch.setenv("ROW_BOT_BENCH", "1")
    monkeypatch.setenv("ROW_BOT_BENCH_MAX_USD", "0")
    monkeypatch.setenv("ROW_BOT_BENCH_BOOKING_FILE", str(tmp_path / "booking.txt"))
    assert cli.main([*base, "--nl-judge", "openai:gpt-4.1-2025-04-14"]) == 2
    assert "paid API and the paid cap is $0" in capsys.readouterr().out
    assert cli.main(base) == 2
    assert "need tau2's NL-assertion judge" in capsys.readouterr().out
    assert cli.main([*base[:5], "airline", *base[6:], "--model", "ollama:gpt-oss:20b"]) == 2
    assert "qwen3.8:27b (stock weights) only" in capsys.readouterr().out
    monkeypatch.setenv("ROW_BOT_BENCH_MAX_USD", "5")  # the owner's yes for a paid judge: it still needs its key
    monkeypatch.setenv("ROW_BOT_BENCH_KEYS_FILE", str(tmp_path / "bench-keys.env"))
    assert cli.main([*base, "--nl-judge", "openai:gpt-4.1-2025-04-14"]) == 2
    assert "paid models go through OpenRouter" in capsys.readouterr().out  # one key, one balance
    monkeypatch.setattr(cli.openrouter, "fetch_endpoints", lambda model: [
        {"provider_name": "OpenAI", "quantization": "unknown", "pricing": {"prompt": "0.000002"}}])
    assert cli.main([*base, "--nl-judge", "openrouter:openai/gpt-4.1"]) == 2
    assert "does not exist (it needs OPENROUTER_API_KEY)" in capsys.readouterr().out


# ── OpenRouter ──────────────────────────────────────────────────────────────


def test_a_matched_openrouter_model_runs_only_on_its_first_party_host_at_the_named_precision() -> None:
    from tests.e2e.bench.agent_evals import openrouter

    assert openrouter.pin_for("openai/gpt-5.2") == {"order": ["OpenAI"], "only": ["OpenAI"], "allow_fallbacks": False}
    assert openrouter.pin_for("recursive/flint-v1.0:free", quantization="bf16")["quantizations"] == ["bf16"]
    endpoints = [{"provider_name": "Azure", "quantization": "unknown", "pricing": {"prompt": "0.000001"}},
                 {"provider_name": "OpenAI", "quantization": "unknown",
                  "pricing": {"prompt": "0.00000175", "completion": "0.000014", "input_cache_read": "0.000000175"}}]
    chosen = openrouter.check_endpoints("openai/gpt-5.2", endpoints)
    assert chosen["provider_name"] == "OpenAI"  # never the cheaper other host
    assert openrouter.rates_document({"openai/gpt-5.2": chosen})["models"] == {
        "openrouter:openai/gpt-5.2": {"input": 1.75, "output": 14.0, "cache_read": 0.175}}
    deepseek = [{"provider_name": "Fireworks", "quantization": "fp8"}, {"provider_name": "Novita", "quantization": "fp8"}]
    with pytest.raises(openrouter.RoutingError, match="no DeepSeek endpoint"):
        openrouter.check_endpoints("deepseek/deepseek-v4.1-flash", deepseek)
    with pytest.raises(openrouter.RoutingError, match="not at bf16"):
        openrouter.check_endpoints("recursive/flint-v1.0:free", [{"provider_name": "Recursive", "quantization": "fp8"}],
                                   quantization="bf16")
    with pytest.raises(openrouter.RoutingError):
        openrouter.pin_for("mystery/model")


def test_every_openrouter_request_carries_its_pin_and_an_unpinned_model_is_refused() -> None:
    from row_bot.providers.transports.openrouter_cancellable import CancellableChatOpenRouter
    from tests.e2e.bench.agent_evals import openrouter

    pinned = CancellableChatOpenRouter(model_name="openai/gpt-5.2", openrouter_api_key="not-a-key")
    stray = CancellableChatOpenRouter(model_name="openai/gpt-4o", openrouter_api_key="not-a-key")
    ids: list[str] = []
    undo = openrouter.install({"openai/gpt-5.2": openrouter.pin_for("openai/gpt-5.2")}, ids)
    try:
        assert pinned._default_params["provider"] == {"order": ["OpenAI"], "only": ["OpenAI"],
                                                      "allow_fallbacks": False}
        with pytest.raises(openrouter.RoutingError, match="no provider pin"):
            stray._default_params  # noqa: B018
    finally:
        undo()
    assert "provider" not in pinned._default_params  # Row-Bot as it ships again

    class Lookups:
        def get(self, url, params=None, headers=None):
            assert headers == {"Authorization": "Bearer not-a-key"}
            return SimpleNamespace(json=lambda: {"data": {"provider_name": "OpenAI"}})

    assert openrouter.served_providers(["gen-1", "gen-2"], "not-a-key", client=Lookups()) == {"OpenAI": 2}


# ── Paid keys ───────────────────────────────────────────────────────────────


def test_paid_keys_come_only_from_the_owners_file_and_their_values_are_never_repeated(tmp_path) -> None:
    from tests.e2e.bench.agent_evals import keys

    path = tmp_path / "bench-keys.env"
    assert keys.names_for(["openai", "anthropic", "openai"]) == ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
    with pytest.raises(keys.KeysError, match="no bench key"):
        keys.names_for(["mistral"])
    assert keys.problem([], None) == ""
    assert keys.KEYS_FILE_ENV in keys.problem(["OPENAI_API_KEY"], None)
    assert "does not exist" in keys.problem(["OPENAI_API_KEY"], path) and not path.exists()  # never created
    path.write_text('# filled in by the owner\nOPENAI_API_KEY="not-a-real-key-0123456789"\nANTHROPIC_API_KEY=\n',
                    encoding="utf-8")
    why = keys.problem(["OPENAI_API_KEY", "ANTHROPIC_API_KEY"], path)
    assert why == "the keys file has no value for ANTHROPIC_API_KEY" and "not-a-real" not in why
    environ: dict[str, str] = {}
    assert keys.load_into(environ, ["OPENAI_API_KEY"], path) == {"OPENAI_API_KEY": "keys file"}
    assert environ == {"OPENAI_API_KEY": "not-a-real-key-0123456789"}
    with pytest.raises(keys.KeysError):
        keys.load_into({}, ["ANTHROPIC_API_KEY"], path)
    assert "OPENAI_API_KEY" not in runner_env(environ, "1:x")  # the benchmark's runner never gets it


def test_a_key_saved_in_the_eval_profile_is_read_from_its_one_record_and_wins_over_the_file(tmp_path,
                                                                                             monkeypatch) -> None:
    from row_bot import secret_store
    from tests.e2e.bench.agent_evals import keys

    profile = tmp_path / "eval-profile"
    # The record's place and identity are secret_store's own (a Row-Bot bound to that folder would read it there).
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(profile))
    expected = secret_store._persistent_server_secret_identity(secret_store.service_name_for(profile),
                                                               "api_keys:ANTHROPIC_API_KEY")
    assert keys.record_path(profile, "ANTHROPIC_API_KEY") == expected
    path, identity = expected
    path.parent.mkdir(parents=True)
    path.write_bytes(b"ROWBOT-DPAPI-V1\x00" + b"sealed")

    def unprotect(data: bytes, entropy: bytes) -> bytes:  # stands in for the Windows user's DPAPI key
        assert (data, entropy) == (b"sealed", identity)
        return b"profile-key-0123456789"

    keys_file = tmp_path / "bench-keys.env"
    keys_file.write_text("ANTHROPIC_API_KEY=file-key-0123456789\nOPENAI_API_KEY=file-openai-0123456789\n",
                         encoding="utf-8")
    assert keys.status(["ANTHROPIC_API_KEY", "OPENAI_API_KEY"], keys_file, profile) == {
        "ANTHROPIC_API_KEY": "found (eval profile)", "OPENAI_API_KEY": "found (keys file)"}
    environ: dict[str, str] = {}
    sources = keys.load_into(environ, ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"], keys_file, profile,
                             unprotect=unprotect)
    assert sources == {"ANTHROPIC_API_KEY": "eval profile", "OPENAI_API_KEY": "keys file"}
    assert environ["ANTHROPIC_API_KEY"] == "profile-key-0123456789"
    assert keys.problem(["ANTHROPIC_API_KEY"], None, profile) == ""
    assert "eval profile has no saved key" in keys.problem(["OPENAI_API_KEY"], None, profile)


# ── The app's follow-up and the failure taxonomy ────────────────────────────


def test_with_app_followups_a_reply_that_stops_on_its_next_step_carries_on_once_before_the_person_reads_it(
        tmp_path, monkeypatch) -> None:
    from row_bot.application import conversation_followups
    from tests.e2e.bench.trajectory import Trajectory

    checked: list[str] = []

    def still_working(thread_id, reply, model_ref, *, unchecked):  # the chat's own model, as the app asks it
        checked.append(reply)
        return "refund rules" in reply

    monkeypatch.setattr(conversation_followups, "_still_working", still_working)

    def host(answers: list[str], followups: str) -> tuple[RuntimeHost, _Runtime]:
        runtime = _Runtime([Step(kind="turn", answer=answer) for answer in answers])
        runtime.config.model_ref = "model:codex:gpt-6.1-sol"
        made = RuntimeHost(runtime, "tau2-bench", tmp_path / f"{followups}.jsonl", followups=followups)  # type: ignore[arg-type]
        made.threads["s"], made.trajectories["s"] = "t", Trajectory(task_id="s", benchmark="tau2-bench")
        return made, runtime

    stopping = ["I found your booking. Next I'll check the refund rules.", "It is refundable: $120 back to your card."]
    carried, _ = host(list(stopping), "app")
    step = carried.turn("s", "Cancel my booking")
    assert step.answer == ("I found your booking. Next I'll check the refund rules.\n\n"
                           "It is refundable: $120 back to your card.")
    assert [item.name for item in carried.trajectories["s"].steps] == ["turn 1", "carry on"]
    plain, runtime = host(list(stopping), "none")
    assert plain.turn("s", "Cancel my booking").answer == stopping[0] and len(runtime.steps) == 1
    asking, runtime = host(["Next I'll cancel it. Shall I go ahead?"], "app")
    assert asking.turn("s", "Cancel it").answer.endswith("Shall I go ahead?") and runtime.steps == []
    # The word rule fires on a short "I'll read …, created report.md" reply; the model check says it's done.
    done, runtime = host(["I'll read the file and total it. Created report.md with the totals.", "unused"], "app")
    assert done.turn("s", "Make the report").answer.startswith("I'll read") and len(runtime.steps) == 1
    assert done.trajectories["s"].config["followups"] == ["carry on: the stop check said the work was done"]
    assert len(checked) == 2


def test_failures_are_bucketed_by_what_the_grader_found_and_what_the_agent_did() -> None:
    from tests.e2e.bench.agent_evals.tau2 import diagnose

    def simulation(task: str, reward: float, breakdown: dict[str, float], *, reason: str = "user_stop",
                   actions: list[dict[str, Any]] | None = None, unsaid: list[str] | None = None) -> dict[str, Any]:
        return {"task_id": task, "trial": 0, "termination_reason": reason, "reward_info": {
            "reward": reward, "reward_breakdown": breakdown, "action_checks": actions or [],
            "communicate_checks": [{"info": item, "met": False} for item in unsaid or []]}, "messages": [
            {"role": "assistant", "content": "Hi! How can I help you today?"},
            {"role": "user", "content": "Cancel ZFA04Y"},
            {"role": "assistant", "content": '{"message":"I\'m checking the reservation before proceeding."}'},
            {"role": "assistant", "content": None, "tool_calls": [{"name": "transfer_to_human_agents"}]},
            {"role": "tool", "content": "Error: not allowed", "error": True},
            {"role": "assistant", "content": "Done. Anything else?"}]}

    write = {"action": {"name": "cancel_reservation", "arguments": {"reservation_id": "ZFA04Y"}},
             "tool_type": "write", "action_match": False}
    found = diagnose.taxonomy([
        simulation("1", 1.0, {"DB": 1.0}),
        simulation("2", 0.0, {"DB": 0.0}, actions=[write]),
        simulation("3", 0.0, {"DB": 0.0}),
        simulation("4", 0.0, {"DB": 1.0, "COMMUNICATE": 0.0}, unsaid=["1628"]),
        simulation("5", 0.0, {}, reason="max_steps"),
        simulation("6", 0.0, {}, reason="agent_error"),
    ])
    assert found["buckets"] == {"wrong write": 1, "extra write": 1, "not communicated": 1, "ran out": 1,
                                "harness": 1}
    rows = {row["task_id"]: row for row in found["rows"]}
    assert rows["2"]["missed_actions"] == ['cancel_reservation({"reservation_id": "ZFA04Y"})']
    assert rows["4"]["not_communicated"] == ["1628"]
    assert found["behaviour"] == {"agent_messages": 18, "json_wrapped": 6, "progress_stops": 6, "tool_calls": 6,
                                  "tool_errors": 6, "transfers": 6}
    assert not diagnose.progress_stop("Next I'll cancel it. Please confirm?")  # it asks: a hand-over, not a stop
    assert diagnose.unwrap('{"message": "Hello"}') == ("Hello", True)
    assert "| 4 | not communicated |" in diagnose.render(found, title="t")
    gave_up = simulation("8", 0.0, {"DB": 0.0}, actions=[write])
    gave_up["messages"].append({"role": "user", "content": "###OUT-OF-SCOPE###"})
    assert diagnose.bucket(gave_up) == "customer gave up"  # the simulated customer, not a wrong write


def test_a_bench_variant_changes_one_thing_in_the_worker_and_is_undone() -> None:
    import row_bot.agent as agent
    from tests.e2e.bench.agent_evals import variants

    shipped = (agent.SILENT_ROUNDS, agent.PROGRESS_NOTE)
    undo = variants.apply("no-progress-note")
    assert agent.SILENT_ROUNDS > 10 ** 6 and agent.PROGRESS_NOTE == shipped[1]
    undo()
    undo = variants.apply("progress-note-with-call")
    assert agent.PROGRESS_NOTE == variants.NOTE_WITH_CALL and agent.SILENT_ROUNDS == shipped[0]
    undo()
    assert (agent.SILENT_ROUNDS, agent.PROGRESS_NOTE) == shipped
    variants.apply("")()
    with pytest.raises(ValueError):
        variants.apply("something-else")
