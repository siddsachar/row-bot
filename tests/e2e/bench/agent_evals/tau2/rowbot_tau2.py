"""tau2-bench's side of the Row-Bot bridge. Runs in tau2-bench's own virtual environment, never Row-Bot's.

    <tau2 venv>/python rowbot_tau2.py --dump-tools airline                 # the domain's tool schemas
    ROW_BOT_BENCH_BRIDGE=<port>:<secret> <tau2 venv>/python rowbot_tau2.py run --domain airline \
        --split train --num-trials 1 --user-model codex:gpt-5.2 --user-effort low --save-to <run>/tau2/airline

The worker (``tests.e2e.bench.agent_evals.worker``) starts this with the bridge's address. Everything
else is tau2-bench 1.0.1 unchanged: its environment runs the tools, its user simulator (its prompt,
its guidelines) plays the customer, its orchestrator and evaluator grade the trajectory, and its
``results.json`` is the record ``tau2 submit prepare`` reads. Only three things are routed to Row-Bot:

- the agent: :class:`RowBotAgent`, registered as ``rowbot``, hands each message to Row-Bot and
  returns Row-Bot's act (one tool call, or its reply) as tau2's ``AssistantMessage``;
- the user simulator's model calls (``--user-model``): through Row-Bot's model layer, so the
  ChatGPT plan, counted in the ledger;
- the NL-assertion judge's calls (``--nl-judge``, retail): the same way. Without it a task that
  needs that judge is refused, never sent to a paid API from here.

Nothing here reaches the network but the loopback bridge: litellm reads its local cost map, and
every HTTP proxy points at a closed loopback port (the worker sets both).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _load_wire() -> Any:
    """wire.py, the bridge's standard-library side, loaded by path: putting its folder on ``sys.path`` would
    let this folder's own package (named ``tau2``) shadow tau2-bench's."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("rowbot_bench_wire", HERE.parent / "wire.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wire = _load_wire()

AGENT_NAME = "rowbot"
#: A model name tau2 records as the agent's ``llm``: the run's real model is Row-Bot's pinned one.
AGENT_LLM = "rowbot"
PROTOCOL = "tau2-bench 1.0.1, Row-Bot as the agent through the bench bridge"


def standard_instructions(domain_policy: str) -> str:
    """tau2's own system prompt for its standard agent (``LLMAgent.system_prompt``), unchanged: the
    instruction and the domain's policy. Row-Bot reads it as its Agent Profile's instructions."""
    from tau2.agent.llm_agent import AGENT_INSTRUCTION, SYSTEM_PROMPT

    return SYSTEM_PROMPT.format(domain_policy=domain_policy, agent_instruction=AGENT_INSTRUCTION)


def tool_schemas(tools: list[Any]) -> list[dict[str, Any]]:
    return [tool.openai_schema for tool in tools]


#: This process's exit code when Row-Bot says the run stopped (the worker's runner.json says why).
EXIT_STOPPED = 3


def checked(client: Any, reply: dict[str, Any]) -> dict[str, Any]:
    """*reply*, unless it says the run stopped (a usage limit, the cost cap, the STOP file): then this
    whole process ends at once, so no later task is attempted. tau2 has saved every finished simulation."""
    if wire.stopping(reply):
        sys.stderr.write(f"Row-Bot stopped the run: {reply.get('kind')}: {reply.get('error')}\n")
        client.close()
        os._exit(EXIT_STOPPED)
    return reply


def build_agent_class(client: Any) -> type:
    from tau2.agent.base_agent import AgentError, HalfDuplexAgent
    from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolCall, ToolMessage, UserMessage
    from pydantic import BaseModel

    class RowBotAgentState(BaseModel):
        system_messages: list = []
        messages: list = []
        session: str = ""

    class RowBotAgent(HalfDuplexAgent):
        """Row-Bot as tau2's agent. Each tool call Row-Bot makes is one ``AssistantMessage`` with that
        call (tau2's orchestrator runs it and sends the ``ToolMessage`` back); its reply ends its turn."""

        def __init__(self, tools: list[Any], domain_policy: str, task: Any = None, llm: str = AGENT_LLM,
                     llm_args: dict | None = None) -> None:
            super().__init__(tools=tools, domain_policy=domain_policy)
            self.task = task
            self.llm, self.llm_args = llm, dict(llm_args or {})

        def get_init_state(self, message_history: list | None = None) -> RowBotAgentState:
            session = f"tau2-{getattr(self.task, 'id', 'task')}-{uuid.uuid4().hex[:8]}"
            checked(client, client.start(session, tool_schemas(self.tools), standard_instructions(self.domain_policy),
                                         {"task_id": str(getattr(self.task, "id", "")), "tau2": PROTOCOL,
                                          "history": history_of(message_history or [])}))
            return RowBotAgentState(session=session)

        def generate_next_message(self, message: Any, state: RowBotAgentState) -> tuple[Any, RowBotAgentState]:
            if isinstance(message, MultiToolMessage):
                if len(message.tool_messages) != 1:
                    raise AgentError("Row-Bot hands out one tool call at a time")
                message = message.tool_messages[0]
            state.messages.append(message)
            if isinstance(message, ToolMessage):
                reply = checked(client, client.tool_result(state.session, message.id, message.content or "",
                                                           error=message.error))
            elif isinstance(message, UserMessage):
                reply = checked(client, client.user(state.session, message.content or ""))
            else:
                raise AgentError(f"unexpected message for the agent: {type(message).__name__}")
            answer = to_assistant_message(reply, state.session, AssistantMessage, ToolCall)
            if answer is None:
                raise AgentError(f"Row-Bot: {reply.get('kind')}: {reply.get('error')}")
            state.messages.append(answer)
            return answer, state

    return RowBotAgent


def history_of(messages: list[Any]) -> list[dict[str, str]]:
    """The conversation so far as plain turns: tau2 opens every retail and airline conversation with its agent's
    greeting ("Hi! How can I help you today?"), which Row-Bot's conversation starts with too. A seeded history
    with tool calls is refused (no retail or airline task has one)."""
    found = []
    for message in messages:
        role = str(getattr(message, "role", "") or "")
        if role not in {"assistant", "user"} or getattr(message, "tool_calls", None):
            raise ValueError(f"only plain user and assistant turns can be seeded, not {role or type(message).__name__}")
        found.append({"role": role, "content": str(getattr(message, "content", "") or "")})
    return found


def to_assistant_message(reply: dict[str, Any], session: str, assistant: Any, tool_call: Any) -> Any:
    """Row-Bot's act as tau2's ``AssistantMessage``, or None for an error act."""
    raw = {"rowbot_session": session}
    if reply.get("type") == "tool_call":
        call = tool_call(id=str(reply["id"]), name=str(reply["name"]), arguments=dict(reply.get("arguments") or {}),
                         requestor="assistant")
        return assistant(role="assistant", content=None, tool_calls=[call], raw_data=raw)
    if reply.get("type") == "message":
        return assistant(role="assistant", content=str(reply.get("text") or ""), raw_data=raw)
    return None


def routed_generate(client: Any, route: dict[str, tuple[str, str]], original: Any, purpose: str) -> Any:
    """tau2's ``generate`` for the user simulator (or the NL judge): calls for a routed model go to Row-Bot's
    model layer; anything else is refused (this process never calls a provider itself)."""
    from tau2.data_model.message import AssistantMessage
    from tau2.utils.llm_utils import to_litellm_messages

    def generate(model: str, messages: list, tools: list | None = None, call_name: str | None = None,
                 **kwargs: Any) -> Any:
        if model not in route:
            raise RuntimeError(f"refused: {purpose} model {model!r} is not routed through Row-Bot")
        if tools:
            raise RuntimeError(f"refused: the {purpose} has tools; only text-only user simulators are bridged")
        target, effort = route[model]
        reply = checked(client, client.complete(purpose, target, effort, [
            {"role": str(item.get("role")), "content": str(item.get("content") or "")}
            for item in to_litellm_messages(messages)]))
        if reply.get("type") == "error":
            raise RuntimeError(f"{purpose}: {reply.get('error')}")
        return AssistantMessage(role="assistant", content=str(reply.get("text") or ""), cost=0.0)

    generate.replaced = original  # type: ignore[attr-defined]
    return generate


def install_routes(client: Any, user_model: str, user_effort: str, judge_model: str, judge_effort: str) -> dict:
    import tau2.user.user_simulator as user_simulator
    import tau2.evaluator.evaluator_nl_assertions as nl_assertions
    from tau2.config import DEFAULT_LLM_NL_ASSERTIONS

    user_name = f"rowbot-route/{user_model}"
    user_simulator.generate = routed_generate(client, {user_name: (user_model, user_effort)},
                                              user_simulator.generate, "user_simulator")
    judge_route = {DEFAULT_LLM_NL_ASSERTIONS: (judge_model, judge_effort)} if judge_model else {}
    nl_assertions.generate = routed_generate(client, judge_route, nl_assertions.generate, "nl_judge")
    return {"user_llm": user_name, "nl_judge": judge_model or "refused",
            "nl_judge_published": DEFAULT_LLM_NL_ASSERTIONS}


def run(args: argparse.Namespace) -> int:
    from tau2.data_model.simulation import TextRunConfig
    from tau2.registry import registry
    from tau2.run import run_domain

    client = wire.BridgeClient()
    try:
        routes = install_routes(client, args.user_model, args.user_effort, args.nl_judge, args.nl_judge_effort)
        agent_class = build_agent_class(client)
        if AGENT_NAME not in registry.get_agents():
            registry.register_agent_factory(
                lambda tools, domain_policy, **kwargs: agent_class(tools=tools, domain_policy=domain_policy,
                                                                   task=kwargs.get("task")), AGENT_NAME)
        config = TextRunConfig(
            domain=args.domain, agent=AGENT_NAME, llm_agent=AGENT_LLM, llm_args_agent={},
            user="user_simulator", llm_user=routes["user_llm"],
            llm_args_user={"reasoning_effort": args.user_effort} if args.user_effort else {},
            num_trials=args.num_trials, seed=args.seed, max_steps=args.max_steps, max_concurrency=1,
            task_split_name=args.split, task_ids=args.task_ids.split(",") if args.task_ids else None,
            save_to=str(Path(args.save_to).resolve()), auto_resume=bool(args.resume))
        results = run_domain(config)
        for simulation in results.simulations:
            session = next((message.raw_data.get("rowbot_session") for message in simulation.messages
                            if getattr(message, "raw_data", None) and message.raw_data.get("rowbot_session")), "")
            if not session:
                continue
            reward = simulation.reward_info.reward if simulation.reward_info else None
            client.end(session, {"task_id": simulation.task_id, "trial": simulation.trial, "reward": reward,
                                 "termination_reason": str(simulation.termination_reason),
                                 "simulation_id": simulation.id, "domain": args.domain, "routes": routes,
                                 "reward_breakdown": {str(key): value for key, value in (
                                     simulation.reward_info.reward_breakdown or {}).items()}
                                 if simulation.reward_info else {}})
    finally:
        client.close()
    return 0


def dump_tools(domain: str) -> int:
    from tau2.registry import registry

    environment = registry.get_env_constructor(domain)()
    try:
        user_tools = tool_schemas(environment.get_user_tools() or [])
    except ValueError:  # retail and airline: the customer has no tools
        user_tools = []
    payload = {"domain": domain, "policy_chars": len(environment.get_policy()),
               "tools": tool_schemas(environment.get_tools()), "user_tools": user_tools}
    sys.stdout.write(json.dumps(payload) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dump-tools", default="", help="print one domain's tool schemas and exit")
    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--domain", required=True, choices=("airline", "retail"))
    run_parser.add_argument("--split", default="train", choices=("train", "test", "base"))
    run_parser.add_argument("--task-ids", default="")
    run_parser.add_argument("--num-trials", type=int, default=1)
    run_parser.add_argument("--seed", type=int, default=300)
    run_parser.add_argument("--max-steps", type=int, default=200)
    run_parser.add_argument("--user-model", required=True, help="provider:model, run by Row-Bot (codex:gpt-5.2)")
    run_parser.add_argument("--user-effort", default="low")
    run_parser.add_argument("--nl-judge", default="", help="provider:model for retail's NL assertions")
    run_parser.add_argument("--nl-judge-effort", default="")
    run_parser.add_argument("--save-to", required=True)
    run_parser.add_argument("--resume", action="store_true",
                            help="tau2's checkpoint resume: only the (task, trial) pairs not saved, with their own seeds")
    args = parser.parse_args(argv)
    if args.dump_tools:
        return dump_tools(args.dump_tools)
    if args.command == "run":
        return run(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
