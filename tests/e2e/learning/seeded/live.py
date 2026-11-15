"""One seeded person's run, in their own scratch profile, through Row-Bot's real runtime (S1).

The release check (``release_check.py``) starts this module once per person as its own process,
because Row-Bot binds its data folder when it is imported. Every stage saves where it is in
``<run>/state.json`` and continues from there when started again, so a crash (planned or not), a
stop or a full token cap never loses work.

Stages:

- ``profile``: the person's scratch profile: Learning's preview settings, the person's own tools
  and approval level, the fake apps behind their calendar, mail and tracker, their workspace, the
  main model (GPT-6.1 Sol on the ChatGPT lane, signed in from the eval profile into this process's
  memory only) and the training engine with its starting model (hard links to a folder that has
  them; nothing in it is written).
- ``past``: the 30-day past written as the person's conversations, then Learning turned on with
  "the last 30 days" ticked, so Row-Bot's own past-chat import reads them (partial fidelity).
- ``days``: each simulated day's requests as real Agent turns (or turns routed to a personal model
  in trial or use), checked by the product's own verifiers, with the simulated person correcting,
  teaching, approving or stopping; then the night: Dream, the vault's refresh, and Learning's own
  runner (training, a version made ready, the comparison), on the simulated clock. The first
  training is crashed at a checkpoint (this process exits at once; the release check starts it
  again and the person presses Continue) and paused by use (a request sent while it trains).
- ``generations`` (L4a, O109): how Row-Bot learns, over three inherited generations in simulated time. The
  person's days go on past the run; every night the product's own look, tests and **strategy trial** run (a
  trial whenever 30 fresh hidden problems have built up: the method in use, a challenger and the frozen
  control at equal budget, the winner inherited by itself). After the third trial, once 30 more fresh problems
  have built up, the **final set** judges every generation and the frozen control. Counts only are kept.
- ``checks``: the survival checks that need the whole run and not the card (a negative result, a source
  deletion, app/engine version skew).
- ``benchmark``: the card's work that needs the whole run, last (O116: the release check runs every person's
  work first and these after): trial, use and Undo of the newest version (when the product never offered
  it, a trial asked for by the person), then the run's own numbers: Compare now on the newest version, on
  every held-out problem gathered.
- ``tidy``: the models it made in Ollama go; every result stays.

``result.json`` holds counts, rates, times and tokens only: no request, reply or file text.
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.e2e.learning.seeded import clock as clock_module
from tests.e2e.learning.seeded import generator
from tests.e2e.learning.seeded.person import SimulatedPerson
from tests.e2e.learning.seeded.scratch import GpuGate, ScratchError, check_scratch

logger = logging.getLogger("row_bot.seeded")

EXIT_PLANNED_CRASH = 86
EXIT_TOKEN_CAP = 75
#: The ChatGPT lane answered with a usage or rate limit: the run stops at once (the person's own use comes first).
EXIT_PROVIDER_LIMIT = 76
_PROVIDER_LIMIT = ("rate limit", "rate_limit", "usage limit", "usage_limit", "429", "too many requests")
STAGES = ("profile", "past", "days", "generations", "checks", "benchmark", "tidy")
#: Extra simulated days generated beyond the run, for requests asked outside the stream (a night
#: request while training, the asked-for trial).
EXTRA_DAYS = 6
#: The person's monthly Learning limit in these runs (the user's own choice on their preview, PLAN.md).
MONTHLY_LIMIT = 10_000_000
#: How long the pause-on-use check waits for a run to pause after the person's request (it pauses at a step).
PAUSE_PATIENCE_SECONDS = 600
#: A training job's states once its training finished (a version is made of it, or was).
TRAINED = ("evaluating", "ready", "trial", "in_use", "not_used")


# ── State ────────────────────────────────────────────────────────────────────


class State:
    """``state.json``: what is done, saved after every step."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("stages", {})
        self.data.setdefault("requests", [])
        self.data.setdefault("nights", {})
        self.data.setdefault("versions", {})
        self.data.setdefault("uses", [])
        self.data.setdefault("survival", {})
        self.data.setdefault("gpu", {"seconds": 0.0, "phases": []})
        self.data.setdefault("notes", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=1, sort_keys=True, default=str), encoding="utf-8")
        temporary.replace(self.path)

    def done(self, stage: str) -> bool:
        return bool(self.data["stages"].get(stage))

    def finish(self, stage: str) -> None:
        self.data["stages"][stage] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    def request_done(self, request_id: str) -> bool:
        return any(item.get("id") == request_id for item in self.data["requests"])


# ── The ChatGPT lane's sign-in (memory only; refreshed tokens go back to the eval profile) ──


class SignIn:
    """The eval profile's ChatGPT sign-in in this process's memory (L0c's lane, O16)."""

    def __init__(self, profile: Path) -> None:
        self.profile = profile
        self.current: Any = None
        self.write_backs: list[str] = []

    def install(self) -> None:
        from tests.e2e.live_eval import eval_profile

        eval_profile.check_profile(self.profile)
        bundle = eval_profile.read_bundle(self.profile)
        if bundle is None:
            raise ScratchError("The eval profile has no ChatGPT sign-in: run the eval-profile sign-in script first.")
        self.current = bundle
        from row_bot import secret_store
        from row_bot.providers import codex

        secret_store.use_memory_backend()  # the sign-in lives in this process's memory only
        original = codex.save_codex_oauth_tokens
        original(codex.CodexTokenSet(access_token=bundle.access_token, refresh_token=bundle.refresh_token,
                                     id_token=bundle.id_token, expires_at=bundle.expires_at,
                                     account_id=bundle.account, plan_type=bundle.plan_type))

        def remember(token_set: Any, *args: Any, **kwargs: Any) -> Any:
            saved = original(token_set, *args, **kwargs)
            self._write_back(token_set)
            return saved

        codex.save_codex_oauth_tokens = remember
        atexit.register(self.scrub)

    def _write_back(self, token_set: Any) -> None:
        """A refreshed sign-in goes back to the eval profile at once (a crash must never lose it)."""

        from tests.e2e.live_eval import eval_profile

        before = self.current
        after = eval_profile.CodexBundle(
            token_set.access_token, token_set.refresh_token or before.refresh_token,
            token_set.id_token or before.id_token, token_set.account_id or before.account,
            token_set.expires_at or before.expires_at, token_set.plan_type or before.plan_type)
        try:
            outcome = eval_profile.write_back(self.profile, before, after)
        except Exception as error:  # noqa: BLE001 - recorded; the run goes on with the tokens it holds
            outcome = f"failed: {type(error).__name__}"
        self.write_backs.append(str(outcome))
        try:
            self.current = eval_profile.read_bundle(self.profile) or after
        except Exception:  # noqa: BLE001
            self.current = after

    def scrub(self) -> None:
        from tests.e2e.live_eval import eval_profile

        try:
            from row_bot import learning

            eval_profile.scrub_worker_folder(learning.data_dir())
        except Exception:  # noqa: BLE001
            logger.debug("couldn't scrub the scratch profile's codex entry", exc_info=True)


# ── Turns ────────────────────────────────────────────────────────────────────


@dataclass
class TurnResult:
    answer: str = ""
    tool_calls: int = 0
    seconds: float = 0.0
    stopped: bool = False
    error: str | None = None
    approvals: list[dict[str, Any]] = field(default_factory=list)
    routed: dict[str, Any] | None = None
    message_id: str = ""


class PersonRun:
    """The run of one seeded person (see the module's docstring)."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.data = Path(args.data).resolve()
        self.run_dir = Path(args.run_dir).resolve()
        self.workspace = self.data.parent / "workspace"
        self.person_id = args.person
        self.person = generator.load_person(args.person)
        self.state = State(self.run_dir / "state.json")
        self.history = generator.generate(args.person, seed=args.seed,
                                          days=args.days + EXTRA_DAYS + int(getattr(args, "generation_days", 0) or 0))
        self.clock = clock_module.SimClock(generator.day_date(1))
        self.clock.restore(self.state.data.get("clock"))
        self.sim = SimulatedPerson(self.person, args.seed)
        saved = self.state.data.get("sim_tokens") or {}
        for key in ("calls", "input_tokens", "output_tokens", "failures", "seconds"):
            setattr(self.sim.tokens, key, saved.get(key, 0))
        self.gpu = GpuGate(Path(args.gpu_booking) if args.gpu_booking else None, args.lane,
                           label=f"S1 release check, {self.person_id}")
        self.sign_in: SignIn | None = None
        self.main_ref = ""
        self.tools: list[str] = []
        self.apps: Any = None
        self._runner: Any = None
        self._comparing = False
        self.build = _build()

    # ── Shared ──────────────────────────────────────────────────────────

    def save(self) -> None:
        self.state.data["clock"] = self.clock.to_dict()
        self.state.data["sim_tokens"] = self.sim.tokens.to_dict()
        self.state.save()

    def note(self, text: str) -> None:
        logger.info("%s: %s", self.person_id, text)
        print(f"[{time.strftime('%H:%M:%S')}] {self.person_id}: {text}", flush=True)

    def ledger_tokens(self) -> dict[str, dict[str, int]]:
        """Tokens in the person's own usage ledger, by purpose and provider (counts only)."""

        path = self.data / "usage.db"
        found: dict[str, dict[str, int]] = {}
        if not path.is_file():
            return found
        with closing(sqlite3.connect(str(path))) as connection:
            rows = connection.execute("SELECT purpose, provider, COALESCE(SUM(input_tokens),0), "
                                      "COALESCE(SUM(output_tokens),0), COUNT(*) FROM model_usage "
                                      "GROUP BY purpose, provider").fetchall()
        for purpose, provider, tokens_in, tokens_out, calls in rows:
            found[f"{purpose}:{provider}"] = {"input": int(tokens_in), "output": int(tokens_out), "calls": int(calls)}
        return found

    def cloud_tokens(self) -> int:
        """Everything this person spent on the ChatGPT lane so far: the ledger's cloud rows and the
        simulated person's own calls."""

        local = ("ollama", "llama_cpp", "lmstudio", "vllm")
        ledger = sum(item["input"] + item["output"] for key, item in self.ledger_tokens().items()
                     if not key.split(":", 1)[1].startswith(local) and not key.split(":", 1)[1].startswith("custom"))
        return ledger + self.sim.tokens.total

    def check_cap(self) -> None:
        spent = int(self.args.spent_before) + self.cloud_tokens()
        if spent >= int(self.args.cap_tokens):
            self.save()
            self.note(f"token cap reached ({spent:,} of {int(self.args.cap_tokens):,}); stopping cleanly")
            raise SystemExit(EXIT_TOKEN_CAP)

    # ── Process set-up (every start) ────────────────────────────────────

    def boot(self) -> None:
        os.environ["ROW_BOT_DESKTOP_NOTIFICATIONS"] = "off"
        os.environ.setdefault("ROW_BOT_AUTO_START_OLLAMA", "0")
        from row_bot import learning

        learning.invalidate()
        if learning.data_dir().resolve() != self.data:
            raise ScratchError(f"Row-Bot's data folder is {learning.data_dir()}, not this person's {self.data}")
        if not learning.preview_enabled():
            raise ScratchError("Start the run with ROW_BOT_LEARNING_PREVIEW=1.")
        self.sign_in = SignIn(Path(self.args.eval_profile))
        self.sign_in.install()
        from row_bot import models
        from row_bot.providers import codex
        from tests.e2e.live_eval import eval_profile

        model = self.state.data.get("model")
        if not model:
            model = eval_profile.choose_model([item.model_id for item in codex.fetch_codex_model_infos()])
            self.state.data["model"] = model
        self.main_ref = f"model:codex:{model}"
        models.adopt_saved_default(self.main_ref)
        self._tools()
        # Which build of Row-Bot ran which days (a run that goes on after an update says so).
        builds = self.state.data.setdefault("builds", [])
        if not builds or builds[-1].get("build") != self.build:
            builds.append({"build": self.build, "from_day": self.clock.day, "at": time.strftime("%Y-%m-%d %H:%M")})
            self.save()

    def _tools(self) -> None:
        """The person's tools (and the fake apps behind their calendar, mail and tracker). Tools that
        would act outside the workspace or reach the network stay bound but answer that they aren't
        available in this run (counted), as in L2w's capture."""

        from row_bot.learning import eval_worker
        from row_bot.learning.packs.fake_apps import FakeApps
        from row_bot.tools import registry

        apps = eval_worker._register_fake_apps()  # noqa: SLF001 - the packs' apps, as the eval worker registers them
        wanted = set(self.person["tools"]) | set(apps)
        for tool in registry.get_all_tools():
            if registry.is_enabled(tool.name) != (tool.name in wanted):
                registry.set_enabled(tool.name, tool.name in wanted)
        self.workspace.mkdir(parents=True, exist_ok=True)
        registry.set_tool_config("filesystem", "workspace_root", str(self.workspace))
        self.tools = [tool.name for tool in registry.get_enabled_tools()]
        inert = set(eval_worker.UNSAFE_TOOLS) - {"task"}

        def refuse(*_a: Any, **_k: Any) -> str:
            self.state.data["refused_tool_calls"] = int(self.state.data.get("refused_tool_calls", 0)) + 1
            return "This tool isn't available in this run."

        async def refuse_async(*_a: Any, **_k: Any) -> str:
            return refuse()

        for name in inert & wanted:
            tool = registry.get_tool(name)
            if tool is None:
                continue
            build = tool.as_langchain_tools

            def inert_tools(build: Any = build) -> list:
                items = build()
                for item in items:
                    item.func = refuse
                    item.coroutine = refuse_async
                return items

            try:
                object.__setattr__(tool, "as_langchain_tools", inert_tools)
            except Exception:  # noqa: BLE001
                tool.as_langchain_tools = inert_tools
        saved = self.state.data.get("apps")
        self.apps = FakeApps(saved if saved else self.history.start_apps)
        eval_worker._CURRENT.apps = self.apps  # noqa: SLF001

    # ── Stage: profile ──────────────────────────────────────────────────

    def stage_profile(self) -> None:
        from tests.fixtures.learning import save_learning_settings

        save_learning_settings(self.data, enabled=False, sources={"my_chats": True, "workflows_and_goals": True,
                                                                  "past_chats": True},
                               apply_mode="ask_first", monthly_token_limit=MONTHLY_LIMIT, train_in_quiet_hours=True,
                               train_where="this_computer", serve_runtime="ollama", model_download="waiting")
        for path, text in sorted(self.history.start_files.items()):
            target = self.workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        engine = self._link_engine()
        from row_bot.learning.sandbox import container_runtime

        container = container_runtime()
        self.state.data["profile"] = {
            "tools": sorted(self.tools), "approval_mode": self.person.get("approval_mode"), "main_model": self.main_ref,
            "workspace_files": len(self.history.start_files), "engine": engine,
            "container": bool(container), "monthly_limit": MONTHLY_LIMIT,
        }
        self.save()

    def _link_engine(self) -> dict[str, Any]:
        """The training engine and the starting model from ``--runtimes-from``: hard links (nothing
        in the source is ever written; a file this run changes is copied first)."""

        from row_bot import learning
        from row_bot.learning.training import engine, weights

        target = self.data / "runtimes" / "learning"
        if not target.exists() and self.args.runtimes_from:
            source = Path(self.args.runtimes_from).resolve() / "runtimes" / "learning"
            if not source.is_dir():
                raise ScratchError(f"{source} holds no training engine")
            for item in source.rglob("*"):
                relative = item.relative_to(source)
                if relative.parts and relative.parts[0] == "cache":
                    continue  # the engine's compile caches are its own per folder
                destination = target / relative
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                if item.suffix == ".json":
                    shutil.copy2(item, destination)  # small records a check may rewrite: real copies
                else:
                    try:
                        os.link(item, destination)
                    except OSError:
                        shutil.copy2(item, destination)
        status = engine.status(self.data, refresh=True)
        model = weights.MODELS.get(learning.read_settings(self.data).starting_model) or weights.QWEN35_9B
        return {"installed": status.installed, "state": status.state,
                "weights": weights.status(model, self.data).state}

    # ── Stage: past ─────────────────────────────────────────────────────

    def stage_past(self) -> None:
        from row_bot import learning, threads
        from row_bot.learning import past_chats
        from row_bot.learning.store import LearningStore
        from tests.fixtures.learning import save_learning_settings

        now = time.time()
        written = 0
        for number, conversation in enumerate(self.history.past):
            thread_id = _thread_id(self.person_id, conversation.id)
            threads.create_thread(conversation.title, thread_id=thread_id, seed_default_skills=False,
                                  approval_mode=str(self.person.get("approval_mode") or ""))
            threads.append_checkpoint_messages(thread_id, _past_messages(conversation))
            # The past happened on its day: day -k is k days before the import starts, between 08:00 and
            # 19:00 of it (in the window: the import reads the 30 days before it starts).
            created = now - (-conversation.day) * 86_400 + (8 + number % 12) * 3_600
            _set_thread_times(thread_id, created)
            written += 1
        save_learning_settings(self.data, enabled=True)
        learning.invalidate()
        LearningStore(self.data).create()
        started = time.monotonic()
        progress = past_chats.run_import(root=self.data, busy=lambda: False)
        store = LearningStore(self.data)
        episodes = [item for item in store.find("episode", limit=100_000)]
        self.state.data["past"] = {
            "conversations": written, "turns": sum(len(item.requests) for item in self.history.past),
            "mistakes": sum(1 for item in self.history.past for request in item.requests if request.mistake),
            "import": {"total": progress.total if progress else None, "done": progress.done if progress else None,
                       "state": progress.state if progress else None},
            "episodes": len(episodes), "historical": sum(1 for item in episodes if getattr(item, "historical", False)),
            "seconds": round(time.monotonic() - started, 1),
        }
        self.save()
        self.note(f"past: {written} conversations imported ({self.state.data['past']['import']})")

    # ── Asking ──────────────────────────────────────────────────────────

    def _place(self, request: Any) -> None:
        from row_bot.learning.packs.fake_apps import FakeApps
        from row_bot.learning import eval_worker

        for path, text in request.inputs.items():
            target = self.workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        self.apps = FakeApps(request.task.apps)
        eval_worker._CURRENT.apps = self.apps  # noqa: SLF001

    def ask(self, request: Any, *, label: str = "stream") -> dict[str, Any]:
        """One request: the person's message, the turn, the check, and the person's reaction."""

        from row_bot import threads
        from row_bot.learning import kinds, teach
        from row_bot.learning.packs.verify import Outcome, verify, workspace_files

        self.check_cap()
        self._place(request)
        thread_id = _thread_id(self.person_id, f"live-{request.id}-{uuid.uuid4().hex[:4]}")
        threads.create_thread(request.prompt[:48], thread_id=thread_id, seed_default_skills=False,
                              approval_mode=str(self.person.get("approval_mode") or ""))
        tokens_before = self.cloud_tokens()
        record: dict[str, Any] = {
            "id": request.id, "day": request.day, "label": label, "template": request.template,
            "intended": request.intended, "kind": kinds.classify(kinds.Request(text=request.prompt)).kind,
            "thread": thread_id, "attempts": [], "reaction": [], "build": self.build,
        }
        text = request.prompt
        for attempt in range(1, 4):
            self.clock.used()
            result = self.turn(thread_id, text, request)
            outcome = Outcome(result.answer, workspace_files(self.workspace), self.apps.state())
            verdict = verify(request.task, outcome, backend="auto")
            record["attempts"].append({
                "passed": verdict.passed, "check": verdict.kind, "tool_calls": result.tool_calls,
                "seconds": round(result.seconds, 1), "stopped": result.stopped, "error": result.error,
                "approvals": result.approvals, "routed": result.routed,
            })
            if verdict.passed:
                break
            reaction = self.sim.react(request, result.answer, verdict.detail or "the outcome was wrong", attempt,
                                      stopped=result.stopped)
            if result.stopped and reaction.action == "teach":
                reaction = type(reaction)("correct", reaction.text, None, reaction.source)
            record["reaction"].append({"action": reaction.action, "source": reaction.source,
                                       "category": reaction.category})
            if reaction.action == "teach":
                try:
                    taught = teach.teach(thread_id, message_id=result.message_id, category=reaction.category,
                                         note=reaction.text)
                    record["reaction"][-1]["linked"] = taught.linked
                except Exception as error:  # noqa: BLE001 - recorded
                    record["reaction"][-1]["error"] = getattr(error, "code", type(error).__name__)
                break
            if reaction.action != "correct":
                break
            text = reaction.text
            self.clock.advance(minutes=3)
        record["passed_first"] = bool(record["attempts"] and record["attempts"][0]["passed"])
        record["passed_final"] = bool(record["attempts"] and record["attempts"][-1]["passed"])
        record["tokens"] = self.cloud_tokens() - tokens_before
        self.state.data["apps"] = self.apps.state()
        self.clock.advance(minutes=2)
        return record

    def turn(self, thread_id: str, text: str, request: Any) -> TurnResult:
        """One message as the app sends it: routed to a personal model when one is used for its kind,
        under the generation registry (so the person's Stop is the app's Stop, and use is use)."""

        from row_bot import agent
        from row_bot.learning import routing
        from row_bot.runtime.executions import generation_registry

        result = TurnResult()
        generation = uuid.uuid4().hex
        config = {"configurable": {"thread_id": thread_id, "model_override": self.main_ref, "runtime_mode": "agent",
                                   "runtime_surface": "normal_chat",
                                   "approval_mode": str(self.person.get("approval_mode") or "approve"),
                                   "generation_id": generation}}
        route = routing.route(conversation_id=thread_id, text=text, model_ref=self.main_ref, root=self.data,
                              main_ref=self.main_ref)
        started = time.monotonic()
        stop = threading.Event()

        def run(events_factory: Any) -> list[Any]:
            handle = generation_registry.register(thread_id, generation_id=generation, stop_event=stop)
            watching = threading.Event()

            def watch() -> None:
                while not watching.wait(1.0):
                    if self.sim.stop_now(result.tool_calls, time.monotonic() - started):
                        result.stopped = True
                        generation_registry.cancel(handle, reason="user")  # the person's Stop
                        return

            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            interrupts: list[Any] = []
            try:
                with generation_registry.ownership(handle):
                    for event in events_factory():
                        kind = event[0]
                        payload = event[1] if len(event) > 1 else None
                        if kind == "tool_call":
                            result.tool_calls += 1
                        elif kind == "done":
                            result.answer = str(payload or "")
                        elif kind == "interrupt":
                            interrupts = list(payload or [])
                        elif kind == routing.FALLBACK_EVENT and result.routed is not None:
                            result.routed["fallback"] = str((payload or {}).get("reason") or "")
                        elif kind in ("error", "terminal"):
                            result.error = kind
            except InterruptedError:
                result.stopped = True
            except Exception as error:  # noqa: BLE001 - recorded as the turn's error
                result.error = type(error).__name__
            finally:
                watching.set()
                agent._set_active_runtime_context()  # noqa: SLF001
            return interrupts

        # A turn on a personal model uses the graphics card: it is booked like the rest (O105/O116).
        booked_here = route is not None and self.gpu.held is None
        if booked_here:
            self.gpu.acquire(f"turns on personal model v{route.version}", expect="5 min, 4 GB")
            started = time.monotonic()  # the turn's time (and the person's patience) start once it runs
        try:
            return self._turn(route, run, result, text, config, request, stop, thread_id, started)
        finally:
            if booked_here:
                self.gpu.release()

    def _turn(self, route: Any, run: Any, result: TurnResult, text: str, config: dict[str, Any], request: Any,
              stop: threading.Event, thread_id: str, started: float) -> TurnResult:
        from row_bot import agent, threads
        from row_bot.learning import routing

        if route is not None:
            result.routed = {"kind": route.kind, "version": route.version, "state": route.state, "fallback": None}
            routed = route.with_configurable({**config["configurable"], "model_override": route.personal_ref})
            interrupts = run(lambda: routing.stream(routed, agent.stream_agent, text, self.tools, config,
                                                    stop_event=stop, root=self.data))
        else:
            interrupts = run(lambda: agent.stream_agent(text, self.tools, config, stop_event=stop))
        rounds = 0
        while interrupts and not result.stopped and rounds < 6:
            rounds += 1
            decisions = [self.sim.approve(item, request.approvals) for item in interrupts]
            approved = all(decisions)
            result.approvals.extend({"tool": str(item.get("tool") or ""), "approved": decision}
                                    for item, decision in zip(interrupts, decisions))
            ids = [str(item.get("__interrupt_id")) for item in interrupts if item.get("__interrupt_id")]
            self.clock.advance(minutes=1)
            interrupts = run(lambda: agent.resume_stream_agent(self.tools, config, approved, interrupt_ids=ids or None,
                                                               stop_event=stop))
        result.seconds = time.monotonic() - started
        try:
            messages = threads.get_latest_checkpoint_messages(thread_id)
            last = next((message for message in reversed(messages) if getattr(message, "type", "") == "ai"), None)
            result.message_id = str(getattr(last, "id", "") or "")
        except Exception:  # noqa: BLE001
            result.message_id = ""
        return result

    # ── Stage: days ─────────────────────────────────────────────────────

    def stage_days(self) -> None:
        by_day = self.history.by_day()
        self._recover_crash()
        for day in range(1, int(self.args.days) + 1):
            for request in by_day.get(day, []):
                if self.state.request_done(request.id):
                    continue
                self.clock.request_time(day, request.order)
                record = self.ask(request)
                self.state.data["requests"].append(record)
                self.save()
                self.note(f"day {day} {request.template}: first {'ok' if record['passed_first'] else 'wrong'}, "
                          f"final {'ok' if record['passed_final'] else 'wrong'} ({record['tokens']:,} tokens)")
            if str(day) not in self.state.data["nights"] or not self.state.data["nights"][str(day)].get("done"):
                self.night(day)

    def night(self, day: int) -> None:
        """Quiet hours after ``day``, in order: Dream, the vault's refresh, Learning's runner."""

        saved = self.state.data["nights"].setdefault(str(day), {"steps": {}})
        if not saved["steps"]:
            self.clock.set(day + 1, clock_module.NIGHT_HOUR)
        steps = {"dream": self._dream, "vault": self._vault, "look": self._look,
                 "learning": lambda: self._learning(day)}
        for name in clock_module.NIGHT_STEPS:
            if name in saved["steps"]:
                if name == "dream":
                    self.clock.dreamed = generator.day_date(day + 1)
                continue
            started = time.monotonic()
            try:
                outcome, error = steps[name](), None
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001 - recorded; the night goes on
                logger.exception("night step %s failed", name)
                outcome, error = None, f"{type(exc).__name__}: {str(exc)[:200]}"
            if name == "dream":
                self.clock.dreamed = generator.day_date(day + 1)
            saved["steps"][name] = {"outcome": outcome, "error": error, "seconds": round(time.monotonic() - started, 1),
                                    "at": self.clock.now().isoformat()}
            self.clock.advance(minutes=5)
            self.save()
        saved["done"] = True
        saved["build"] = self.build
        self.gpu.release()
        self.save()

    def _dream(self) -> Any:
        from row_bot import dream_cycle

        if not dream_cycle.is_enabled():
            return {"ran": False, "why": "off"}
        found = dream_cycle.run_dream_cycle()
        return {key: value for key, value in (found or {}).items() if isinstance(value, (int, float, bool))}

    def _vault(self) -> Any:
        from row_bot.learning import evaluation

        added = evaluation.refresh_audit(asked_by="the release check's night (fresh hidden problems)")
        summary = evaluation.audit_summary(asked_by="the release check's night (counts)")
        return {"refresh": dict(added), "summary": dict(summary)}

    def night_look(self) -> Any:
        """The product's night's look (L3a), its tests (L3b) and its strategy trial (L4a): quiet hours and the
        person's last message on the simulated clock; Learning's own records on the real one, like the work
        capture stamps (the look's 30 days of evidence are then the person's real recent work)."""

        from row_bot.learning.lease import ResourceLease
        from row_bot.learning.runner import NightLook

        return NightLook(lease=ResourceLease(since_activity=self.clock.idle_seconds), root=self.data,
                         hours=clock_module.sim_quiet_hours(self.clock, self.data))

    def _look(self) -> Any:
        """Tonight's look for things to improve (L3a) and its tests (L3b): counts only (drafts by kind of
        change, E9 refusals, tokens; drafts tested, ready, not better). The simulated person then uses each
        change that came out ready and Undoes it again (use → Undo survives, counted)."""

        look = self.night_look()
        report = look.tick()
        found = report.to_dict() if report is not None else None
        result: dict[str, Any] = {"ran": False} if found is None else {
            key: found[key] for key in ("state", "found", "diagnosed", "drafted", "refused", "needs_examples",
                                        "train", "waits", "bugs", "unreadable", "tokens", "drafts", "refusals",
                                        "skipped")}
        result["tests"] = self._tests(look)
        return result

    def _tests(self, look: Any) -> dict[str, Any]:
        """Tonight's tests of drafted changes (L3b), and use → Undo of each that came out ready."""

        from row_bot.learning import changes
        from row_bot.learning.store import LearningStore

        tested = changes.last_tests(LearningStore(self.data)) or {}
        if tested.get("night") != look.tonight():
            return {"ran": False}
        survived = failed = 0
        for proposal in changes.learning_changes():
            if changes.view_state(proposal) != "ready":
                continue
            try:
                used = changes.use(str(proposal["id"]))
                ok = changes.view_state(used) == "in_use"
                undone = changes.undo(str(proposal["id"]))
                ok = ok and changes.view_state(undone) == "not_used"
            except changes.ChangeError:
                ok = False
            survived += ok
            failed += not ok
        return {"ran": True, **{key: tested.get(key) for key in ("state", "tested", "ready", "not_better", "unclear",
                                                                 "used", "tokens")},
                "use_undo_survived": survived, "use_undo_failed": failed}

    # ── Stage: generations (L4a) ────────────────────────────────────────

    def stage_generations(self) -> None:
        """Three inherited generations of how Row-Bot learns, in simulated time (O109): the person's days go on,
        and the product's own nights run its strategy trials (``NightLook``); then the final set. A resumed run
        goes on from its last night."""

        if not int(self.args.generations or 0):
            self.state.data["generations"] = {"state": "not_run", "why": "not asked for (--generations 0)"}
            self.save()
            return
        from tests.fixtures.learning import save_learning_settings

        # The person's monthly limit for these nights: trials together may use 20% of it.
        save_learning_settings(self.data, monthly_token_limit=int(self.args.monthly_limit))
        found = self.state.data.setdefault("generations", {"state": "running", "nights": {}})
        by_day = self.history.by_day()
        first = int(self.args.days) + 1
        last = int(self.args.days) + int(self.args.generation_days)
        for day in range(first, last + 1):
            if found.get("final", {}).get("state") == "done":
                break
            for request in by_day.get(day, []):
                if self.state.request_done(request.id):
                    continue
                self.clock.request_time(day, request.order)
                record = self.ask(request, label="generations")
                self.state.data["requests"].append(record)
                self.save()
                errors = " ".join(str(item.get("error") or "") for item in record["attempts"]).lower()
                if any(marker in errors for marker in _PROVIDER_LIMIT):
                    self.note("the ChatGPT lane answered with a usage or rate limit: stopping at once")
                    raise SystemExit(EXIT_PROVIDER_LIMIT)
            # The computer is on for quiet hours every ``night_every`` nights (fewer nights, the same work).
            if (day - first + 1) % max(1, int(self.args.night_every)):
                continue
            self.check_cap()
            if str(day) not in self.state.data["nights"] or not self.state.data["nights"][str(day)].get("done"):
                self.night(day)
            found["nights"][str(day)] = self._trial_night()
            if self._strategy_trials() >= int(self.args.generations) and "final" not in found:
                final = self._final_set(day)
                if final.get("state") in ("done", "failed"):
                    found["final"] = final
            self._record_generations(found)
            self.save()
            self.note(f"generations: day {day}, {self._strategy_trials()} trials, method v{found.get('method_version')}"
                      + (", final set done" if found.get("final", {}).get("state") == "done" else ""))
        found["state"] = "done" if found.get("final", {}).get("state") == "done" else "incomplete"
        self._record_generations(found)
        self.save()

    def _store(self) -> Any:
        from row_bot.learning.store import LearningStore

        return LearningStore(self.data)

    def _strategy_trials(self) -> int:
        from row_bot.learning.improver import archive

        return sum(1 for item in archive.trials(self._store()) if not item.get("final"))

    def _trial_night(self) -> dict[str, Any]:
        """Tonight's trial in counts (``meta.last_night``) and the fresh problems now."""

        from row_bot.learning import evaluation
        from row_bot.learning.improver import meta

        store = self._store()
        night = meta.last_night(store) or {}
        fresh = evaluation.fresh_problems(asked_by="the release check's generations", store=store)
        return {"state": night.get("state"), "trial": night.get("trial"), "problems": night.get("problems"),
                "switched": night.get("switched"), "tokens": night.get("tokens"), "fresh": fresh["fresh"]}

    def _final_set(self, day: int) -> dict[str, Any]:
        """The final set once 30 fresh problems have built up after the last trial: every generation and the
        frozen control at the trials' budget, judged on them (no switch)."""

        from row_bot.learning import evaluation
        from row_bot.learning.calls import LedgerModelClient
        from row_bot.learning.improver import archive, meta
        from row_bot.learning.spending import TokenMeter

        store = self._store()
        fresh = evaluation.fresh_problems(asked_by="the release check's final set", store=store)["fresh"]
        if fresh < evaluation.MIN_FRESH:
            return {"state": "waiting", "fresh": fresh}
        done = [item for item in archive.trials(store) if not item.get("final")]
        budget = int(done[-1].get("arm_tokens") or meta.ARM_TOKENS_MAX)
        look = self.night_look()
        self.check_cap()
        report = meta.run_final(store, label=f"release check, night {day}", client=LedgerModelClient(),
                                tester_factory=lambda method: look._tester(method, meta.PURPOSE),  # noqa: SLF001
                                arm_tokens=budget, meter=TokenMeter(10**12))
        return {"state": "done" if report.state == "done" else "failed" if report.state == "stopped" else report.state,
                "trial": report.trial, "problems": report.problems, "day": day, "tokens": report.tokens}

    def _record_generations(self, found: dict[str, Any]) -> None:
        """The trials, the chain and the H3 estimate (§7) in counts: what the report reads."""

        from row_bot.learning import evaluation
        from row_bot.learning.improver import archive

        store = self._store()
        trials = archive.trials(store)
        current = archive.current(store, create=False)
        found["method_version"] = current.version if current is not None else 1
        found["trials"] = [{key: item.get(key) for key in ("id", "problems", "arm_tokens", "switched", "final",
                                                           "judging_tokens")}
                           | {"arms": [{key: arm.get(key) for key in ("role", "version", "drafted", "tested", "accepted",
                                                                      "better", "tokens", "cloud_tokens", "yield")}
                                       for arm in item.get("arms") or ()],
                              "judged": bool(item.get("judged"))}
                           for item in trials]
        found["chain"] = [{key: step.get(key) for key in ("version", "by")} for step in archive.chain(store)]
        found["h3"] = evaluation.h3_estimate(archive.generations(store), store=store)

    # ── Learning's runner at night ──────────────────────────────────────

    def runner(self) -> Any:
        if self._runner is not None:
            return self._runner
        from row_bot.learning import model_compare
        from row_bot.learning.lease import ResourceLease
        from row_bot.learning.runner import LearningRunner
        from row_bot.learning.training import engine, planner as planner_module, releases
        from row_bot.learning.training.supervisor import TrainingSupervisor

        lease = ResourceLease(since_activity=self.clock.idle_seconds)
        supervisor = TrainingSupervisor(root=self.data, engine=engine.LocalEngine(self.data), lease=lease)
        recipe = self.recipe()
        planner = planner_module.Planner(self.data, jobs=supervisor.jobs, engine=supervisor.engine,
                                         clock=self.clock.time, recipe_for=lambda: recipe)
        supervisor.dataset = planner_module.LearningDataset(planner)
        maker = releases.ReleaseMaker(self.data, jobs=supervisor.jobs, recipe_for=lambda: recipe, start_threads=False)
        # The product's comparison as it is; a pilot (--pilot-compare) only runs one visible task a pack.
        plan = (model_compare.Plan(visible_per_pack=1, hidden=False, replay=False) if self.args.pilot_compare
                else model_compare.Plan())
        comparer = model_compare.Comparer(self.data, main_ref=lambda: self.main_ref, plan=plan)
        comparisons = model_compare.Comparisons(self.data, comparer=comparer, lease=lease,
                                                training=supervisor.training, start_threads=False)
        for owner, name, phase in ((supervisor, "start", "training"), (supervisor, "train_now", "training"),
                                   (maker, "start", "export and serve"), (comparisons, "start", "comparison")):
            setattr(owner, name, self._booked(getattr(owner, name), phase))
        self._runner = LearningRunner(supervisor, root=self.data, hours=clock_module.sim_quiet_hours(self.clock, self.data),
                                      planner=planner, releases=maker, comparisons=comparisons, look=self.night_look())
        return self._runner

    def _booked(self, original: Any, phase: str) -> Any:
        """``original`` with the shared card booked first (waiting for other lanes and the person's own
        training); a comparison also shares the main model's kept pack results between the seeded people."""

        def booked(*args: Any, **kwargs: Any) -> Any:
            self.gpu.acquire(f"{phase}, simulated day {self.clock.day}", expect=self._gpu_expect(phase))
            started = time.monotonic()
            if phase == "comparison":
                self._share_cache(into_profile=True)
                self._comparing = True
            try:
                return original(*args, **kwargs)
            finally:
                if phase == "comparison":
                    self._comparing = False
                    self._share_cache(into_profile=False)
                if phase != "training":
                    self._gpu_time(phase, time.monotonic() - started)
                    # O116: the card goes back between a night's training (and its version) and the
                    # comparison, and after it; each books again.
                    self.gpu.release()

        return booked

    def _gpu_expect(self, phase: str) -> str:
        """The booking's honest size (the tier's graphics-memory cap plus the engine's own): a training holds
        the card through its version; a comparison books it again (O116)."""

        cap = {"16gb": 17, "24gb": 25, "32gb": 33}.get(self.args.tier, 17)
        if phase == "comparison":
            return f"{10 if self.args.pilot_compare else 70} min, {cap} GB"
        if phase == "export and serve":
            return f"5 min, {cap} GB"
        return f"25 min, {cap} GB"

    def _share_cache(self, *, into_profile: bool) -> None:
        """The main model's kept pack results (O31's 30-day cache: model, task, metric version, budget and
        runner; counts only) are the same for every seeded person, so the people of a run share them:
        merged into the profile before a comparison and back after it. Per-problem replay is never shared."""

        from row_bot.learning import evaluation
        from row_bot import learning

        shared = self.run_dir.parent / "shared_arm_cache.json"
        mine = learning.folder_path(self.data) / evaluation.CACHE_FILE
        source, target = (shared, mine) if into_profile else (mine, shared)

        def entries(path: Path) -> dict[str, Any]:
            try:
                found = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            return dict(found.get("entries") or {}) if isinstance(found, dict) and found.get("version") == 1 else {}

        merged = entries(target)
        for key, value in entries(source).items():
            if key not in merged or float(value.get("at") or 0) > float(merged[key].get("at") or 0):
                merged[key] = value
        if merged:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"version": 1, "entries": merged}, sort_keys=True), encoding="utf-8")

    def _yield_watch(self) -> threading.Event:
        """While the night's work runs: when the person's own Row-Bot starts training, or their quiet hours
        come (O116: running work stops at 00:43), ours yields at once (training pauses at its checkpoint, a
        comparison stops, the booking ends; both continue once the card is free again)."""

        from tests.e2e.learning.seeded.scratch import QUIET_STOP, in_quiet_hours, person_is_training

        done = threading.Event()

        def loop() -> None:
            while not done.wait(10.0):
                if not (person_is_training() or in_quiet_hours(start=QUIET_STOP)):
                    continue
                runner = self._runner
                if runner is not None and runner.supervisor.training():
                    runner.supervisor.stop_now("local_gpu")
                    self.state.data["notes"].append({"yielded": "training", "at": time.strftime("%H:%M:%S")})
                if runner is not None and self._comparing:
                    runner.comparisons.stop()
                    self.state.data["notes"].append({"yielded": "comparison", "at": time.strftime("%H:%M:%S")})
                if runner is not None and not runner.supervisor.training() and not self._comparing:
                    self.gpu.release()

        threading.Thread(target=loop, name="s1-yield", daemon=True).start()
        return done

    def _wait_for_person(self) -> None:
        """Nothing starts while the person's own Row-Bot trains or in their quiet hours (O116)."""

        from tests.e2e.learning.seeded.scratch import person_is_training, quiet_reason

        said = ""
        while True:
            why = ("the person's own Row-Bot is training" if person_is_training() else quiet_reason())
            if not why:
                return
            if why != said:
                self.note(f"{why}: waiting")
                said = why
            self.gpu.release()
            time.sleep(60.0)

    def recipe(self) -> Any:
        """The tier's certified recipe, its graphics memory capped to the tier (``--tier``)."""

        from dataclasses import replace

        from row_bot.learning.training import hardware, recipes

        recipe = recipes.load(self.args.tier)
        found = hardware.detect()
        total = int(found.gpu.memory_mib) if found.gpu is not None else 0
        cap_gb = {"16gb": 16.0, "24gb": 24.0, "32gb": 32.0}.get(self.args.tier, 16.0)
        if total:
            share = round(min(0.95, cap_gb * 1024 * 0.95 / total), 4)
            recipe = replace(recipe, settings={**recipe.settings, "vram_cap": share})
        return recipe

    def _gpu_time(self, phase: str, seconds: float) -> None:
        gpu = self.state.data["gpu"]
        gpu["seconds"] = round(float(gpu.get("seconds", 0.0)) + seconds, 1)
        gpu["phases"].append({"phase": phase, "seconds": round(seconds, 1), "day": self.clock.day})

    def _learning(self, day: int) -> Any:
        if not self.args.gpu:
            return {"skipped": "a pilot without the graphics card (--no-gpu)"}
        runner = self.runner()
        supervisor = runner.supervisor
        events: list[dict[str, Any]] = []
        last = None
        watching = self._yield_watch()
        try:
            for _ in range(24):
                self.check_cap()
                self._wait_for_person()
                status = runner.check()
                job = supervisor.jobs.current() if supervisor.jobs.store.exists() else None
                events.append({"state": status.state, "reason": status.reason,
                               "job": job.state if job is not None else None, "version": job.version if job else None})
                if supervisor.training() and job is not None:
                    self._watch_training(job, day)
                    continue
                self._after_comparisons()
                marker = (status.state, status.reason, job.state if job else None)
                if marker == last:
                    break
                last = marker
        finally:
            watching.set()
        self._trials(day)
        return {"runner": events[-6:], "checks": len(events)}

    def _watch_training(self, job: Any, day: int) -> None:
        """Wait for a training run to finish; on the first, crash at a checkpoint and pause it by use. A run
        paused because the person used Row-Bot continues once nobody has used it for a few (simulated)
        minutes, by the runner's own check."""

        supervisor = self.runner().supervisor
        survival = self.state.data["survival"]
        version = job.version
        started = time.monotonic()
        entry = self.state.data["versions"].setdefault(str(version), {"version": version})
        entry.setdefault("trained_after_day", day)
        entry.setdefault("job", job.id)
        waited_for_use = 0
        # The step this worker started from (a resumed run starts at its checkpoint): the pause-on-use check
        # waits until this run has taken a step of its own, so it isn't still loading the model.
        first_step = int(job.steps_done or 0)
        while True:
            current = supervisor.jobs.get(job.id)
            if current is None:
                break
            if current.state == "paused" and current.pause_reason == "in_use" and waited_for_use < 10:
                waited_for_use += 1
                self._continue_after_use()
                continue
            if current.state not in ("preparing", "running", "pausing", "waiting"):
                break
            crash = survival.get("crash_mid_training")
            if (self.args.crash and crash is None and current.state == "running"
                    and current.checkpoint_step is not None):
                survival["crash_mid_training"] = {"job": job.id, "version": version, "steps_done": current.steps_done,
                                                  "checkpoint": current.checkpoint_step, "state": "crashed",
                                                  "day": day}
                self._gpu_time("training", time.monotonic() - started)
                self.save()
                self.note(f"planned crash at step {current.steps_done} (checkpoint {current.checkpoint_step})")
                from row_bot.learning import capture

                capture.flush(10.0)
                os._exit(EXIT_PLANNED_CRASH)  # no clean-up: the job object ends the training worker
            pause = survival.get("pause_on_use")
            if (pause is None and (crash is not None or not self.args.crash) and current.state == "running"
                    and current.steps_done >= max(2, first_step + 1)):
                self._pause_on_use(current, day)
            time.sleep(2.0)
        supervisor.wait_idle(120)
        finished = supervisor.jobs.get(job.id)
        self._gpu_time("training", time.monotonic() - started)
        entry.update({"state": finished.state if finished else None, "steps": finished.steps_done if finished else None,
                      "steps_total": finished.steps_total if finished else None,
                      "loss": finished.loss if finished else None,
                      "seconds_per_step": finished.seconds_per_step if finished else None,
                      "error": finished.error_code if finished else None,
                      "training_seconds": round(float(entry.get("training_seconds", 0.0)) + time.monotonic() - started, 1)})
        self.save()

    def _recover_crash(self) -> None:
        """After the planned crash: Row-Bot's own recovery, then the person's Continue."""

        crash = self.state.data["survival"].get("crash_mid_training")
        if not crash or crash.get("state") != "crashed":
            return
        from row_bot.learning.training import jobs as jobs_module

        supervisor = self.runner().supervisor
        found = jobs_module.recover(supervisor.jobs, live=lambda job_id: False)
        job = supervisor.jobs.get(crash["job"])
        crash.update({"recovered_state": job.state if job else None, "pause_reason": job.pause_reason if job else None,
                      "resumes_from": job.checkpoint_step if job else None, "recovered": len(found),
                      "recovery": job.recovered if job else None})
        self.note(f"after the crash: {crash['recovered_state']} ({crash['pause_reason']}), "
                  f"checkpoint {crash['resumes_from']}; the person presses Continue")
        if job is not None and job.state == "paused":
            supervisor.train_now(job.id)  # the recovery prompt's Continue
            crash["continued"] = True
            self._watch_training(job, int(crash.get("day") or self.clock.day))
            finished = supervisor.jobs.get(job.id)
            crash["final_state"] = finished.state if finished else None
            crash["steps_done"] = finished.steps_done if finished else None
            crash["steps_total"] = finished.steps_total if finished else None
        # Passed: it continued from its checkpoint and trained to the end (a version is being made of it).
        crash["state"] = ("passed" if crash.get("resumes_from") and crash.get("final_state") in TRAINED
                          and crash.get("steps_done") == crash.get("steps_total") else "failed")
        self.save()

    def _pause_on_use(self, job: Any, day: int) -> None:
        """The person sends a request while the run trains: training pauses at its next step and
        continues once Row-Bot has been free for a few (simulated) minutes."""

        supervisor = self.runner().supervisor
        request = self._extra_request("pause")
        check: dict[str, Any] = {"job": job.id, "version": job.version, "steps_before": job.steps_done}
        self.state.data["survival"]["pause_on_use"] = check
        self.note(f"pause on use: asking while v{job.version} trains (step {job.steps_done})")
        asked_at = time.time()
        record = self.ask(request, label="night")
        self.state.data["requests"].append(record)
        asked = time.monotonic()
        deadline = asked + PAUSE_PATIENCE_SECONDS
        paused = supervisor.jobs.get(job.id)
        while paused is not None and paused.state in ("running", "pausing") and time.monotonic() < deadline:
            time.sleep(1.0)
            paused = supervisor.jobs.get(job.id)
        check["seconds_to_pause"] = round(time.monotonic() - asked, 1)  # after the request's turn ended
        # From the person's message to the run's pause (the job's own record of it, the real clock).
        moved = [item for item in (getattr(paused, "transitions", None) or ())
                 if getattr(item, "state", None) == "paused" and float(getattr(item, "at", 0)) >= asked_at]
        if moved:
            check["seconds_from_message_to_pause"] = round(float(moved[0].at) - asked_at, 1)
        check.update({"state_after_use": paused.state if paused else None,
                      "pause_reason": paused.pause_reason if paused else None,
                      "paused_at_step": paused.steps_done if paused else None})
        status = self._continue_after_use()
        resumed = supervisor.jobs.get(job.id)
        check.update({"runner_after": f"{status.state}/{status.reason}" if status else None,
                      "resumed": resumed.state if resumed else None,
                      "resumed_from_step": resumed.steps_done if resumed else None})
        check["state"] = ("passed" if check["state_after_use"] == "paused" and check["pause_reason"] == "in_use"
                          and check["resumed"] in ("preparing", "running", *TRAINED) else "failed")
        self.save()

    def _continue_after_use(self) -> Any:
        """Nobody uses Row-Bot for a while (simulated): once the paused run's worker has ended, the runner's
        own check continues it. Returns the runner's last status."""

        supervisor = self.runner().supervisor
        supervisor.wait_idle(120)  # the paused run's worker exits after its checkpoint
        self.clock.advance(minutes=6)
        status = None
        for _ in range(10):
            status = self.runner().check()
            job = supervisor.jobs.current() if supervisor.jobs.store.exists() else None
            if job is None or job.state != "paused" or job.pause_reason != "in_use":
                break
            time.sleep(3.0)
        return status

    def _extra_request(self, purpose: str, kind: str | None = None) -> Any:
        """A request from the days after the run (the person's work goes on), not yet asked."""

        used = {item["id"] for item in self.state.data["requests"]}
        for request in self.history.stream:
            if request.day <= int(self.args.days) or request.id in used:
                continue
            if kind is None or _classified(request) == kind:
                return request
        raise RuntimeError(f"no extra request left for {purpose}")

    def _after_comparisons(self) -> None:
        """A finished comparison: the person reads it. Use when the product recommends a kind; otherwise
        Not now (a negative result is never used by itself, D24)."""

        from row_bot.learning import model_compare, use
        from row_bot.learning.training import releases

        for version_dir in sorted(releases.versions_dir(self.data).glob("v*")):
            try:
                version = int(version_dir.name[1:])
            except ValueError:
                continue
            entry = self.state.data["versions"].setdefault(str(version), {"version": version})
            state = releases.read_state(version_dir)
            if state is not None:
                entry.update({"phase": state.phase, "gguf_bytes": state.gguf_bytes, "model_ref": state.model_ref,
                              "failure": state.failure,
                              # O119: with several passes, which pass the version kept and which couldn't start.
                              "kept_pass": dict(getattr(state, "kept_pass", None) or {}) or None})
            result = model_compare.read_comparison(version, self.data)
            if result is None or entry.get("reviewed"):
                continue
            entry["comparison"] = _comparison_summary(result)
            entry["compared_on_day"] = self.clock.day - 1
            overall = result.get("overall") or {}
            before = dict(use.read(self.data).assignments)
            if overall.get("verdict") == "better" and overall.get("recommend") and state is not None and state.model_ref:
                kind = str(overall["recommend"])
                use.start_trial(version, kind, state.model_ref, root=self.data, clock=self.clock.time)
                self.state.data["uses"].append({"event": "trial", "version": version, "kind": kind,
                                                "day": self.clock.day, "asked_for": False})
                self.note(f"v{version}: {overall.get('line')} The person uses it for {kind}.")
            else:
                model_compare.mark_reviewed(version, root=self.data, clock=self.clock.time)
                after = dict(use.read(self.data).assignments)
                negative = self.state.data["survival"].setdefault("negative_result", {"observed": []})
                negative["observed"].append({"version": version, "verdict": overall.get("verdict"),
                                             "used_by_itself": any(item.version == version for item in after.values())
                                             and not any(item.version == version for item in before.values())})
                self.note(f"v{version}: {overall.get('line')} Not used.")
            entry["reviewed"] = True
            self.save()

    def _trials(self, day: int) -> None:
        """A trial due for its keep question: the person keeps it when its routed answers did about as
        well as the main model's first tries on that kind, and undoes it otherwise."""

        from row_bot.learning import use

        state = use.read(self.data)
        for kind, item in sorted(state.assignments.items()):
            if item.state != "trial" or not use.keep_due(item, now=self.clock.time()):
                continue
            routed = [record for record in self.state.data["requests"]
                      if (record["attempts"][0].get("routed") or {}).get("kind") == kind and record["attempts"]]
            main = [record for record in self.state.data["requests"] if record.get("kind") == kind
                    and not record["attempts"][0].get("routed")]
            routed_rate = sum(record["passed_first"] for record in routed) / len(routed) if routed else 0.0
            main_rate = sum(record["passed_first"] for record in main) / len(main) if main else 0.0
            if routed and routed_rate >= main_rate - 0.10:
                use.keep(kind, root=self.data, clock=self.clock.time)
                event = "keep"
            else:
                use.undo(kind, root=self.data, clock=self.clock.time)
                event = "undo"
            self.state.data["uses"].append({"event": event, "version": item.version, "kind": kind, "day": day,
                                            "routed": len(routed), "routed_rate": round(routed_rate, 3),
                                            "main_rate": round(main_rate, 3)})
            self.note(f"trial of v{item.version} for {kind}: {event} (routed {routed_rate:.0%} of {len(routed)}, "
                      f"main {main_rate:.0%})")
            self.save()

    # ── Stage: checks ───────────────────────────────────────────────────

    def stage_checks(self) -> None:
        survival = self.state.data["survival"]
        for name, check in (("source_deletion", self._source_deletion), ("version_skew", self._version_skew)):
            if survival.get(name, {}).get("state") in ("passed", "failed", "not_run"):
                continue
            try:
                survival[name] = check()
            except SystemExit:
                raise
            except Exception as error:  # noqa: BLE001 - a check that couldn't run says so
                logger.exception("check %s failed to run", name)
                survival[name] = {"state": "failed", "error": f"{type(error).__name__}: {str(error)[:200]}"}
            self.save()
        negative = survival.setdefault("negative_result", {"observed": []})
        observed = negative.get("observed") or []
        negative["state"] = ("not_observed" if not observed else
                             "passed" if not any(item.get("used_by_itself") for item in observed) else "failed")
        for name in ("crash_mid_training", "pause_on_use"):
            survival.setdefault(name, {"state": "not_run", "why": "no training ran in this run"})
        self.save()

    def stage_benchmark(self) -> None:
        """The work on the card that needs the whole run, after every person's work (O116): trial, use and
        Undo of the newest version, then the benchmark comparison."""

        survival = self.state.data["survival"]
        if survival.get("trial_use_undo", {}).get("state") not in ("passed", "failed", "not_run"):
            try:
                survival["trial_use_undo"] = self._trial_use_undo()
            except SystemExit:
                raise
            except Exception as error:  # noqa: BLE001 - a check that couldn't run says so
                logger.exception("check trial_use_undo failed to run")
                survival["trial_use_undo"] = {"state": "failed", "error": f"{type(error).__name__}: {str(error)[:200]}"}
            self.save()
        if (self.state.data.get("benchmark") or {}).get("state") in ("done", "not_run"):
            return
        self.state.data["benchmark"] = (self._benchmark() if self.args.gpu else
                                        {"state": "not_run", "why": "a run without the graphics card (--no-gpu)"})
        self.save()

    def _benchmark(self) -> dict[str, Any]:
        """The run's own numbers: after the last day the person presses Compare now on the newest version,
        so it is compared on every held-out problem the vault gathered over the run (the product's
        comparison, unchanged: 3 runs per arm on hidden problems)."""

        newest = self._newest_ready()
        if newest is None:
            return {"state": "not_run", "why": "no version was made"}
        version, _state = newest
        entry = self.state.data["versions"].get(str(version)) or {}
        if entry.get("comparison") and int(entry.get("compared_on_day") or 0) >= int(self.args.days):
            return {"state": "done", "version": version, "day": entry["compared_on_day"], "fresh": False,
                    "comparison": entry["comparison"]}
        from row_bot.learning import model_compare

        self._wait_for_person()
        runner = self.runner()
        self._clock_at_least(int(self.args.days) + 1, 2)  # the night after the last day (or later)
        watching = self._yield_watch()
        try:
            for _ in range(4):
                self._wait_for_person()
                try:
                    self._booked(lambda: runner.comparisons.comparer.run(version), "comparison")()
                except model_compare.CompareError as error:
                    self.note(f"the benchmark comparison stopped ({error.code}); again")
                    continue
                break
        finally:
            watching.set()
            self.gpu.release()
        result = model_compare.read_comparison(version, self.data)
        if result is None:
            return {"state": "failed", "version": version, "why": "the comparison didn't finish"}
        self.note(f"benchmark of v{version}: {(result.get('overall') or {}).get('line')}")
        return {"state": "done", "version": version, "day": int(self.args.days), "fresh": True,
                "comparison": _comparison_summary(result)}

    def _clock_at_least(self, day: int, hour: int) -> None:
        """Move the clock to ``day`` at ``hour`` unless it is already later (the checks after the run come in
        either order, and a resumed run may have moved on)."""

        from datetime import datetime, timedelta

        target = datetime.combine(self.clock.first_day, datetime.min.time()) + timedelta(days=day - 1, hours=hour)
        if target > self.clock.now():
            self.clock.set(day, hour)

    def _newest_ready(self) -> tuple[int, Any] | None:
        from row_bot.learning.training import releases

        found = None
        for version_dir in sorted(releases.versions_dir(self.data).glob("v*")):
            state = releases.read_state(version_dir)
            if state is not None and state.phase == releases.READY and state.model_ref:
                if found is None or state.version > found[0]:
                    found = (state.version, state)
        return found

    def _source_deletion(self) -> dict[str, Any]:
        """Delete a chat the newest version was trained on: it asks to retrain without it, and Learning's
        records of the chat are gone."""

        from row_bot.learning import contracts, versions
        from row_bot.learning.store import LearningStore
        from row_bot.thread_cleanup import delete_thread

        newest = self._newest_ready()
        if newest is None:
            return {"state": "not_run", "why": "no version was made"}
        version, state = newest
        store = LearningStore(self.data)
        release = store.get("release", state.release_id) if state.release_id else None
        sources = list(getattr(release, "source_thread_ids", ()) or ()) if isinstance(release, contracts.Release) else []
        live = [thread for thread in sources if "-live-" in thread]
        target = (live or sources or [None])[0]
        if target is None:
            return {"state": "not_run", "why": "the version names no source chat"}
        before = len(store.find("decision", thread_id=target, limit=10_000))
        deleted = delete_thread(target)
        after = len(LearningStore(self.data).find("decision", thread_id=target, limit=10_000))
        needs = versions.needs_you(self.data)
        asks = any(item.get("type") == "retrain" and item.get("version") == version for item in needs)
        planned = None
        if asks:
            runner = self.runner()
            try:
                outcome = versions.retrain_without(version, planner=runner.planner, root=self.data,
                                                   clock=self.clock.time)
                planned = f"v{outcome.job.version} planned" if outcome.job is not None else f"not planned ({outcome.reason})"
                if outcome.job is not None:
                    runner.supervisor.discard(outcome.job.id)  # the check stops here: no second training
            except Exception as error:  # noqa: BLE001
                planned = f"error: {type(error).__name__}"
        gone = bool(getattr(deleted, "deleted", deleted))
        return {"state": "passed" if gone and before and after == 0 and asks else "failed", "version": version,
                "decisions_before": before, "decisions_after": after, "asks_to_retrain": asks,
                "retrain_planned": planned, "deleted": gone}

    def _version_skew(self) -> dict[str, Any]:
        """The engine was built from another lock than this app's (an app update): it reads as outdated,
        nothing trains, and putting it back makes it ready again."""

        from row_bot.learning.training import engine

        status = engine.status(self.data, refresh=True)
        if not status.installed or status.folder is None:
            return {"state": "not_run", "why": f"engine {status.state}"}
        record = Path(status.folder) / engine.RECORD_FILE
        original = record.read_bytes()
        data = json.loads(original.decode("utf-8"))
        data["plan"] = "an-older-app-" + str(data.get("plan") or "")[:12]
        record.unlink()  # a hard link: never write through it
        record.write_text(json.dumps(data), encoding="utf-8")
        engine.forget_status()
        try:
            skewed = engine.status(self.data, refresh=True)
            ready = engine.LocalEngine(self.data).ready()
            runner_reason = self.runner().status().reason
        finally:
            record.unlink()
            record.write_bytes(original)
            engine.forget_status()
        back = engine.status(self.data, refresh=True)
        return {"state": "passed" if skewed.state == "outdated" and not ready and back.installed else "failed",
                "skewed_state": skewed.state, "engine_ready": ready, "runner_reason": runner_reason,
                "restored_state": back.state}

    def _trial_use_undo(self) -> dict[str, Any]:
        """Trial, use and Undo of the newest version, on requests of the days after the run. A kind the
        product put in trial is used when such requests are left; otherwise the person asks for a trial
        (as L2c2's e2e did) on the kind of work, other than "other", that the most requests left are
        routed as (ties: the kind they asked most during the run), so the trial has requests to route."""

        from collections import Counter

        from row_bot.learning import routing, use

        newest = self._newest_ready()
        if newest is None:
            return {"state": "not_run", "why": "no version was made"}
        version, state = newest
        events = self.state.data["uses"]
        used = {item["id"] for item in self.state.data["requests"]}
        left = Counter(_classified(request) for request in self.history.stream
                       if request.day > int(self.args.days) and request.id not in used)
        left.pop("other", None)
        assigned = [kind for kind in sorted(use.read(self.data).assignments) if left.get(kind)]
        offered = {item.get("kind") for item in events if item.get("event") == "trial" and not item.get("asked_for")}
        if assigned:
            kind = assigned[0]
            # In trial without the product offering it: this check started it before it was interrupted.
            asked_for = kind not in offered
            if asked_for and not any(item.get("event") == "trial" and item.get("kind") == kind for item in events):
                events.append({"event": "trial", "version": version, "kind": kind, "day": self.clock.day,
                               "asked_for": True})
        else:
            if not left:
                return {"state": "not_run", "why": "no request left that is routed to a kind of work"}
            asked = Counter(record.get("kind") for record in self.state.data["requests"])
            kind = max(sorted(left), key=lambda item: (left[item], asked.get(item, 0)))
            use.start_trial(version, kind, state.model_ref, root=self.data, clock=self.clock.time)
            events.append({"event": "trial", "version": version, "kind": kind, "day": self.clock.day,
                           "asked_for": True})
            asked_for = True
        self._clock_at_least(int(self.args.days) + 1, 9)
        asked, prompts = [], []
        # One booking for the trial's turns (each would book on its own, and another lane could take the card
        # between them).
        self.gpu.acquire(f"trial: turns on personal model v{version}", expect="10 min, 4 GB")
        try:
            for _ in range(3):
                try:
                    request = self._extra_request("trial", kind)
                except RuntimeError:
                    break
                record = self.ask(request, label="trial")
                self.state.data["requests"].append(record)
                asked.append(record)
                prompts.append(request.prompt)
                self.save()
        finally:
            self.gpu.release()
        routed = [record for record in asked if record["attempts"][0].get("routed")]
        answered = [record for record in routed if not record["attempts"][0]["routed"].get("fallback")]
        back = use.undo(kind, root=self.data, clock=self.clock.time)
        events.append({"event": "undo", "version": version, "kind": kind, "day": self.clock.day})
        # The same words after Undo: the main model answers again (no route).
        after = routing.route(conversation_id=f"after-undo-{uuid.uuid4().hex[:6]}", text=prompts[0],
                              model_ref=self.main_ref, root=self.data, main_ref=self.main_ref) if prompts else None
        return {"state": "passed" if routed and after is None and back is None else "failed", "version": version,
                "kind": kind, "asked_for": asked_for, "asked": len(asked), "routed": len(routed),
                "answered_by_personal_model": len(answered), "fallbacks": len(routed) - len(answered),
                "passed": sum(record["passed_first"] for record in routed), "routes_after_undo": after is not None}

    # ── Stage: tidy ─────────────────────────────────────────────────────

    def stage_tidy(self) -> None:
        from row_bot.learning import eval_worker
        from row_bot.learning.training import base_model, releases, serving, weights

        eval_worker.stop_all()
        removed = []
        for version_dir in sorted(releases.versions_dir(self.data).glob("v*")):
            try:
                version = int(version_dir.name[1:])
                serving.withdraw("ollama", version, root=self.data)
                removed.append(f"v{version}")
            except Exception:  # noqa: BLE001
                logger.debug("couldn't withdraw a version", exc_info=True)
        try:
            base_model.withdraw(weights.QWEN35_2B.id, root=self.data)
            removed.append("starting model")
        except Exception:  # noqa: BLE001
            logger.debug("couldn't withdraw the starting model", exc_info=True)
        self.state.data["tidy"] = {"removed_from_ollama": removed}
        self.save()

    # ── Result ──────────────────────────────────────────────────────────

    def result(self) -> dict[str, Any]:
        data = self.state.data
        return {
            "format": "row_bot.learning.seeded_person_result", "version": 1, "person": self.person_id,
            "name": self.person["name"], "tier": self.args.tier, "seed": self.args.seed, "days": self.args.days,
            "history": self.history.fingerprint(), "model": data.get("model"), "profile": data.get("profile"),
            "stages": data["stages"], "past": data.get("past"), "requests": data["requests"],
            "nights": data["nights"], "versions": data["versions"], "uses": data["uses"],
            "survival": data["survival"], "gpu": data["gpu"], "benchmark": data.get("benchmark"),
            "generations": data.get("generations"),
            "tokens": {"ledger": self.ledger_tokens(), "sim_person": self.sim.tokens.to_dict(),
                       "cloud_total": self.cloud_tokens()},
            "refused_tool_calls": data.get("refused_tool_calls", 0), "builds": data.get("builds", []),
            "write_backs": list(self.sign_in.write_backs) if self.sign_in else [],
        }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build() -> str:
    """The checkout's commit this process runs (short), or "" when git can't say."""

    import subprocess

    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).resolve().parents[4],
                              capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _thread_id(person: str, key: str) -> str:
    from row_bot.learning.contracts import valid_thread_id

    text = f"s1-{person}-{key}".replace("_", "-")
    if not valid_thread_id(text):
        import hashlib

        text = f"s1-{person}-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return text


def _classified(request: Any) -> str:
    from row_bot.learning import kinds

    return kinds.classify(kinds.Request(text=request.prompt)).kind


def _past_messages(conversation: Any) -> list[Any]:
    """A past conversation as the messages a checkpoint keeps."""

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    messages: list[Any] = []
    counter = 0

    def steps_and_answer(steps: tuple, answer: str) -> None:
        nonlocal counter
        for step in steps:
            counter += 1
            call_id = f"call_{conversation.id}_{counter}".replace("-", "_")
            messages.append(AIMessage(content="", tool_calls=[{"name": step.name, "args": dict(step.args),
                                                               "id": call_id, "type": "tool_call"}]))
            messages.append(ToolMessage(content=step.result, tool_call_id=call_id, name=step.name))
        messages.append(AIMessage(content=answer))

    for request in conversation.requests:
        messages.append(HumanMessage(content=request.prompt))
        if request.mistake is not None:
            steps, answer, correction = request.mistake
            steps_and_answer(steps, answer)
            messages.append(HumanMessage(content=correction))
        steps_and_answer(request.steps, request.answer)
    return messages


def _set_thread_times(thread_id: str, created: float) -> None:
    from datetime import datetime

    from row_bot import threads

    stamp = datetime.fromtimestamp(created).isoformat()
    with closing(sqlite3.connect(threads.DB_PATH)) as connection, connection:
        connection.execute("UPDATE thread_meta SET created_at = ?, updated_at = ? WHERE thread_id = ?",
                           (stamp, stamp, thread_id))


def _comparison_summary(result: dict[str, Any]) -> dict[str, Any]:
    """What the report needs of a comparison: counts, verdicts, the paired analyses (aggregates only)."""

    return {key: result.get(key) for key in ("arms", "rows", "overall", "note", "details", "analysis", "repeats",
                                              "left_out", "baseline_unavailable", "main_unavailable", "seconds",
                                              "metric_version", "stopped")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One seeded person's release-check run (started by release_check.py)")
    parser.add_argument("--person", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--tier", default="16gb")
    parser.add_argument("--stages", default=",".join(STAGES))
    parser.add_argument("--eval-profile", required=True)
    parser.add_argument("--runtimes-from", default="")
    parser.add_argument("--cap-tokens", type=int, default=25_000_000)
    parser.add_argument("--spent-before", type=int, default=0)
    parser.add_argument("--gpu-booking", default="")
    parser.add_argument("--lane", default="lane-B")
    parser.add_argument("--crash", action="store_true", help="crash the first training at a checkpoint")
    parser.add_argument("--no-gpu", dest="gpu", action="store_false", help="nights skip Learning's runner (a pilot)")
    parser.add_argument("--pilot-compare", action="store_true", help="a pilot's comparison: one visible task a pack")
    parser.add_argument("--generations", type=int, default=0,
                        help="L4a: inherited generations of how Row-Bot learns (trials), then the final set")
    parser.add_argument("--generation-days", type=int, default=0,
                        help="L4a: at most this many simulated days after the run for the generations")
    parser.add_argument("--night-every", type=int, default=1,
                        help="L4a: during the generations, quiet hours run every this many nights")
    parser.add_argument("--monthly-limit", type=int, default=MONTHLY_LIMIT,
                        help="L4a: the person's monthly Learning limit during the generations (trials get 20%%)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    check_scratch(Path(args.data), Path(args.eval_profile))
    run = PersonRun(args)
    run.boot()
    wanted = [stage.strip() for stage in args.stages.split(",") if stage.strip()]
    code = 0
    try:
        for stage in STAGES:
            if stage not in wanted or run.state.done(stage):
                continue
            run.note(f"stage {stage}")
            getattr(run, f"stage_{stage}")()
            run.state.finish(stage)
    except SystemExit as stop:
        if stop.code != EXIT_TOKEN_CAP:
            raise
        code = EXIT_TOKEN_CAP  # stopped cleanly at the cap: what was done is still reported
    (run.run_dir / "result.json").write_text(json.dumps(run.result(), indent=1, sort_keys=True, default=str),
                                             encoding="utf-8")
    run.gpu.release()
    return code


if __name__ == "__main__":
    sys.exit(main())
