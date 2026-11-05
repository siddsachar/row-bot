"""The precedence study's runs: one model, its conditions, one model call each (Lane B, ``PRECEDENCE.md``).

One process per model (Row-Bot binds its data folder when imported), in a scratch profile ``prec_<model>``. Each
call is a **real Agent turn of Row-Bot at c2ad4a2ba up to its first model call**, so the request is Row-Bot's own
prompt assembly:

- a new chat with the person's tools (S1's Mara: files, memory, tasks, calculator, chart, web search, Row-Bot's
  settings tools) and the fake calendar, mail and tracker, Row-Bot's real calendar and mail tools taken out of
  this process (the fake apps stand in for them, as in the pilot), approval level Ask;
- the item's **skill** created as a user skill and **pinned** to the chat, so it sits in the system prompt's
  Skills section, where Row-Bot puts a chat's skills;
- the item's **memory** handed to auto-recall (``agent._turn_recall`` returns it instead of searching), so it is
  formatted by Row-Bot's own ``format_recall_block`` and placed where recall puts it (the turn context);
- the **note** (conditions 4 and 6) appended to that recall block by the harness, one line;
- the person's request last.

Each model call is made by Row-Bot's own model wrapper. A reply whose calls only read (:data:`scorer.READS`)
goes back to the turn, so those reads run (the fake apps, the skill, a sum) and the model is called again; the
first reply that acts or answers is kept and the turn is stopped there, so **no acting tool runs** (at most
:data:`scorer.MAX_MODEL_CALLS` calls). That reply's first action is scored by :mod:`scorer`. Results go to ``<out>/<model>.jsonl``, one
line per call, and a rerun continues where it stopped.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from tests.e2e.learning.precedence import items as items_module
from tests.e2e.learning.precedence import scorer
from tests.e2e.learning.seeded.scratch import GpuGate, ScratchError, check_scratch, in_quiet_hours, QUIET_STOP

logger = logging.getLogger("row_bot.precedence")

PERSON_TOOLS = ("filesystem", "memory", "task", "calculator", "chart", "duckduckgo", "row_bot_status")
STAND_INS = ("calendar", "gmail", "outlook_calendar", "outlook_mail")
LOCAL_MODELS = {"qwen3.5-4b": "prec-qwen3.5-4b:latest", "qwen3.5-9b": "qwen3.5:9b", "qwen3.8-27b": "qwen3.8:27b"}
EXIT_CAP = 75
EXIT_LIMIT = 76


class FirstCall(Exception):
    """Raised after the first model call's reply is kept: the turn goes no further, no tool runs."""


class Capture:
    """What the current call sent and got back."""

    def __init__(self) -> None:
        self.prompt: Any = None
        self.sent: list[Any] | None = None
        self.response: Any = None
        self.model_calls = 0
        self.reads: list[str] = []
        self.tokens = {"input": 0, "output": 0}


CURRENT = Capture()


def _install_hooks() -> None:
    """The memory for auto-recall, and the stop after the first model call (both this process only)."""

    from row_bot import agent
    from row_bot.memory_policy import format_recall_block

    def recall(messages: list, max_tokens: int) -> tuple[list[dict], Any]:
        prompt = CURRENT.prompt
        if prompt is None or prompt.memory is None:
            return [], None
        memory = {"id": f"mem-{prompt.item.id}", "category": "fact", "subject": prompt.item.memory_subject,
                  "content": prompt.memory}

        def block(memories: list[dict]) -> str:
            text = format_recall_block(memories, context_window=max(1, int(max_tokens / 0.85)))
            return f"{text}\n{prompt.note}" if prompt.note and text else text

        return [memory], block

    agent._turn_recall = recall  # noqa: SLF001 - harness only

    original = agent._AgentModelCall.bound_model  # noqa: SLF001

    def bound_model(self: Any) -> Any:
        real = original(self)

        class Once:
            def invoke(self_inner: Any, messages: list, config: Any = None) -> Any:  # noqa: N805
                response = real.invoke(messages, config)
                CURRENT.model_calls += 1
                usage = getattr(response, "usage_metadata", None) or {}
                CURRENT.tokens["input"] += int(usage.get("input_tokens") or 0)
                CURRENT.tokens["output"] += int(usage.get("output_tokens") or 0)
                calls = list(getattr(response, "tool_calls", None) or ())
                if CURRENT.sent is None:
                    CURRENT.sent = list(messages)
                if scorer.only_reads(calls) and CURRENT.model_calls < scorer.MAX_MODEL_CALLS:
                    CURRENT.reads.extend(str(call.get("name") or "") for call in calls)
                    return response  # the reads run; the model is called again
                CURRENT.response = response
                raise FirstCall()

            def __getattr__(self_inner: Any, name: str) -> Any:  # noqa: N805
                return getattr(real, name)

        return Once()

    agent._AgentModelCall.bound_model = bound_model  # noqa: SLF001


def _tools(workspace: Path) -> list[str]:
    from row_bot.learning import eval_worker
    from row_bot.learning.packs.fake_apps import FakeApps
    from row_bot.tools import registry

    for name in STAND_INS:
        registry._tools.pop(name, None)  # noqa: SLF001 - this process only
    apps = eval_worker._register_fake_apps()  # noqa: SLF001
    wanted = set(PERSON_TOOLS) | set(apps)
    for tool in registry.get_all_tools():
        if registry.is_enabled(tool.name) != (tool.name in wanted):
            registry.set_enabled(tool.name, tool.name in wanted)
    workspace.mkdir(parents=True, exist_ok=True)
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    eval_worker._CURRENT.apps = FakeApps({})  # noqa: SLF001
    return [tool.name for tool in registry.get_enabled_tools()]


def _sign_in(eval_profile: Path) -> str:
    """The eval profile's ChatGPT sign-in in memory only; a refresh or write-back is refused."""

    from tests.e2e.learning.seeded import pilot_run
    from tests.e2e.learning.seeded.live import SignIn
    from row_bot.providers import codex
    from tests.e2e.live_eval import eval_profile as eval_module

    pilot_run._guard_sign_in()  # noqa: SLF001 - the pilot's own guard
    SignIn(eval_profile).install()
    return eval_module.choose_model([item.model_id for item in codex.fetch_codex_model_infos()])


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or ():
        if isinstance(block, dict) and block.get("type") in (None, "text", "output_text"):
            parts.append(str(block.get("text") or ""))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def _add_skill(prompt: Any) -> str | None:
    from row_bot import skills

    if prompt.skill is None:
        return None
    item = prompt.item
    _remove_skill(item.skill_name)
    skills.create_skill(name=item.skill_name, display_name=item.skill_display, icon="📌",
                        description=item.skill_description, instructions=prompt.skill, enabled=True)
    return item.skill_name


def _remove_skill(name: str) -> None:
    from row_bot import skills

    skills._ensure_skills_loaded()  # noqa: SLF001
    found = skills._skills_cache.pop(name, None)  # noqa: SLF001 - harness only
    skills._enabled.pop(name, None)  # noqa: SLF001
    folder = skills.USER_SKILLS_DIR / name.replace(" ", "-").lower()
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    if found is not None:
        skills._save_config()  # noqa: SLF001


def one_call(prompt: Any, *, model_ref: str, tools: list[str], save_prompt: Path | None = None) -> dict[str, Any]:
    """One item in one condition: Row-Bot's first model call, scored."""

    from row_bot import agent, threads
    from row_bot.learning import eval_worker
    from row_bot.learning.packs.fake_apps import FakeApps
    from row_bot.skills_activation import delete_thread_activation_state, pin_skill
    from row_bot.thread_cleanup import delete_thread

    item = prompt.item
    eval_worker._CURRENT.apps = FakeApps(item.apps or {})  # noqa: SLF001
    thread_id = f"prec-{item.id}-c{prompt.condition}-{uuid.uuid4().hex[:6]}"
    threads.create_thread(item.request[:48], thread_id=thread_id, seed_default_skills=False, approval_mode="approve")
    skill = _add_skill(prompt)
    if skill:
        pin_skill(thread_id, skill)
    CURRENT.__init__()
    CURRENT.prompt = prompt
    config = {"configurable": {"thread_id": thread_id, "model_override": model_ref, "runtime_mode": "agent",
                               "runtime_surface": "normal_chat", "approval_mode": "approve",
                               "generation_id": uuid.uuid4().hex}}
    started = time.monotonic()
    error = None
    try:
        for event in agent.stream_agent(item.request, tools, config, stop_event=threading.Event()):
            if event and event[0] in ("error", "terminal") and CURRENT.response is None:
                error = str(event[1])[:300] if len(event) > 1 else event[0]
    except FirstCall:
        pass
    except Exception as exc:  # noqa: BLE001 - recorded
        if CURRENT.response is None:
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
    seconds = round(time.monotonic() - started, 2)
    record: dict[str, Any] = {"item": item.id, "kind": item.kind, "condition": prompt.condition,
                              "seconds": seconds, "model": model_ref}
    response = CURRENT.response
    if response is None:
        record.update({"error": error or "no model reply", "choice": None})
    else:
        calls = [{"name": call.get("name"), "args": call.get("args")} for call in getattr(response, "tool_calls", [])]
        text = _text_of(getattr(response, "content", ""))
        record.update(scorer.first_action(item, calls, text))
        if scorer.only_reads(calls):
            record["via"], record["choice"] = "reads_only", scorer.NEITHER  # still reading at the last call
        record["calls"] = calls[:3]
        record["text"] = text[:4000]
    record["tokens"] = dict(CURRENT.tokens)
    record["model_calls"] = CURRENT.model_calls
    record["reads"] = list(CURRENT.reads)
    if save_prompt is not None and CURRENT.sent is not None:
        save_prompt.parent.mkdir(parents=True, exist_ok=True)
        save_prompt.write_text(json.dumps([{"type": getattr(m, "type", ""), "content": _text_of(m.content)}
                                               for m in CURRENT.sent or []], indent=1, ensure_ascii=False),
                                   encoding="utf-8")
    if skill:
        _remove_skill(skill)
    delete_thread_activation_state(thread_id)
    try:
        delete_thread(thread_id)
    except Exception:  # noqa: BLE001 - the chat stays in the scratch profile
        logger.debug("couldn't delete %s", thread_id, exc_info=True)
    CURRENT.prompt = None
    return record


def _limit_error(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(word in lowered for word in ("rate limit", "rate_limit", "usage limit", "usage_limit", "429",
                                            "too many requests", "quota"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The precedence study: one model's calls")
    parser.add_argument("--model", required=True, help="main, or one of " + ", ".join(LOCAL_MODELS))
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--conditions", default="1,2,3,4,5,6")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--items", type=int, default=items_module.COUNT)
    parser.add_argument("--first-item", type=int, default=0, help="skip this many items (a probe uses others)")
    parser.add_argument("--seed", type=int, default=items_module.SEED)
    parser.add_argument("--eval-profile", default="")
    parser.add_argument("--cap-tokens", type=int, default=0, help="stop once this many ChatGPT-lane tokens are spent")
    parser.add_argument("--gpu-booking", default="")
    parser.add_argument("--chunk", type=int, default=40, help="items per booking of the card")
    parser.add_argument("--redo-reads", action="store_true",
                        help="calls scored by the one-call rule that stopped at a read are made again (moved aside)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    os.environ["ROW_BOT_DESKTOP_NOTIFICATIONS"] = "off"
    os.environ.setdefault("ROW_BOT_AUTO_START_OLLAMA", "0")
    data = Path(args.data).resolve()
    check_scratch(data, Path(args.eval_profile) if args.eval_profile else None)
    if "prec_" not in data.as_posix():
        raise ScratchError(f"{data}: the study's profiles are prec_ folders")
    from row_bot.data_paths import get_row_bot_data_dir

    if get_row_bot_data_dir().resolve() != data:
        raise ScratchError("set ROW_BOT_DATA_DIR to --data")
    from row_bot import models

    if args.model == "main":
        if not args.eval_profile:
            raise ScratchError("--eval-profile is needed for the main model")
        model_ref = f"model:codex:{_sign_in(Path(args.eval_profile))}"
    else:
        from row_bot.learning.training import serving

        # Row-Bot's local catalog from what Ollama says each model can do, as Row-Bot's own serving refreshes it
        # after it registers a model (the 4B was registered by serve_4b.py).
        serving.refresh_ollama_catalog()
        model_ref = f"model:ollama:{LOCAL_MODELS[args.model]}"
    models.adopt_saved_default(model_ref)
    tools = _tools(data.parent / "workspace")
    _install_hooks()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = out / f"{args.model}.jsonl"
    done: set[tuple[str, int, int]] = set()
    spent = 0
    if args.redo_reads and results.exists():
        rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines() if line.strip()]
        old = [row for row in rows if row.get("via") == "other_tool" and row.get("tool") in scorer.READS
               and "model_calls" not in row]
        keep = [row for row in rows if row not in old]
        for row in keep:  # the one-call rows that asked the person: scored from their question
            if row.get("via") == "other_tool" and row.get("tool") == scorer.ASK and "model_calls" not in row:
                item = next(item for item in items_module.generate(seed=args.seed) if item.id == row["item"])
                row.update(scorer.first_action(item, row.get("calls") or [], row.get("text") or ""))
                row["rescored"] = "ask_person"
        aside = results.with_suffix(".one-call-reads.jsonl")
        with aside.open("a", encoding="utf-8") as handle:
            for row in old:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        results.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in keep), encoding="utf-8")
        spent += sum(sum((row.get("tokens") or {}).values()) for row in old)
        print(f"redo: {len(old)} one-call rows that stopped at a read moved to {aside.name}", flush=True)
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            done.add((row["item"], row["condition"], row["run"]))
            spent += sum((row.get("tokens") or {}).values()) if args.model == "main" else 0
    every = items_module.generate(seed=args.seed)
    chosen = every[args.first_item:args.first_item + args.items]
    conditions = [int(part) for part in args.conditions.split(",") if part.strip()]
    gate = GpuGate(Path(args.gpu_booking) if args.gpu_booking and args.model != "main" else None, "lane-B",
                   label=f"LB precedence ({args.model})")
    meta = {"model": args.model, "model_ref": model_ref, "seed": args.seed, "items": [item.id for item in chosen],
            "fingerprint": items_module.fingerprint(every), "conditions": conditions, "runs": args.runs,
            "tools": tools, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    (out / f"{args.model}.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    code = 0
    try:
        for run in range(1, args.runs + 1):
            for start in range(0, len(chosen), args.chunk):
                chunk = chosen[start:start + args.chunk]
                todo = [(item, condition) for item in chunk for condition in conditions
                        if (item.id, condition, run) not in done]
                if not todo:
                    continue
                if args.model != "main":
                    gate.acquire(f"run {run}, items {start + 1}-{start + len(chunk)} ({len(todo)} calls)",
                                 expect="25 min, 20 GB")
                order = random.Random(f"{args.seed}/{args.model}/{run}/{start}")
                for item in chunk:
                    mine = [condition for condition in conditions if (item.id, condition, run) not in done]
                    order.shuffle(mine)
                    for condition in mine:
                        if args.model != "main" and in_quiet_hours(start=QUIET_STOP):
                            raise SystemExit(EXIT_LIMIT)
                        sample = out / "prompts" / f"{args.model}-{item.id}-c{condition}.json"
                        record = one_call(items_module.prompt(item, condition), model_ref=model_ref, tools=tools,
                                          save_prompt=sample if item.id == chosen[0].id and run == 1 else None)
                        record["run"] = run
                        with results.open("a", encoding="utf-8") as handle:
                            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        done.add((item.id, condition, run))
                        if args.model == "main":
                            spent += sum((record.get("tokens") or {}).values())
                            if record.get("error") and _limit_error(record["error"]):
                                print(f"usage or rate limit: {record['error'][:200]}", flush=True)
                                raise SystemExit(EXIT_LIMIT)
                            if args.cap_tokens and spent >= args.cap_tokens:
                                print(f"token cap reached ({spent:,})", flush=True)
                                raise SystemExit(EXIT_CAP)
                print(f"[{time.strftime('%H:%M:%S')}] {args.model} run {run}: items {start + 1}-"
                      f"{start + len(chunk)} done ({len(done)} calls; {spent:,} tokens)", flush=True)
                gate.release()
    except SystemExit as stop:
        code = int(stop.code or 0)
    finally:
        gate.release()
    return code


if __name__ == "__main__":
    sys.exit(main())
