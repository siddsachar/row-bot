"""One benchmark task's Row-Bot runtime, inside its worker process.

The worker is started by the orchestrator with a scratch ``ROW_BOT_DATA_DIR`` and the
environment :func:`child_env` builds, before anything from Row-Bot is imported.
:func:`check_environment` refuses to go on otherwise. Then :class:`BenchRuntime`:

- signs in (the ChatGPT lane only) from the eval profile into this process's memory,
  the way ``tests/e2e/learning/seeded/live.SignIn`` does; refreshed tokens go back to
  the profile through its bridge, and nothing is written into the profile;
- pins the model, the tools, the approval level and the reasoning effort;
- checks every model call against the cost ledger before it is sent (the usage-ledger
  callback, as the live eval's budget gate does) and records it after, from the scratch
  folder's own ``usage.db``;
- enforces the benchmark's network policy;
- runs turns as the app does (``agent.stream_agent``) and declines anything that still
  stops for a person.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from tests.e2e.live_eval import ledger as usage_rows
from tests.e2e.live_eval.budget import estimate_call
from tests.e2e.live_eval.orchestrate import child_env as live_eval_child_env

from . import netguard
from .ledger import BudgetStop, CallEntry, CostLedger
from .trajectory import Step

logger = logging.getLogger("row_bot.bench")

HANDS_OFF = "full"  # Row-Bot's Hands-off approval level: nothing stops for a person
MAX_RESUMES = 3
_TOOL_ARGS = 400
#: Reply text that means the ChatGPT plan (or another provider) hit a usage or rate limit.
PROVIDER_LIMIT_MARKERS = ("rate limit", "rate_limit", "ratelimit", "usage limit", "usage_limit", "limit reached",
                          "too many requests", "error code: 429", "status code 429", "http 429", "429 client error")


#: The one local model every benchmark run uses (the owner's instruction, 14 Nov 2026): the stock Ollama
#: library weights, never a personal fine-tune (``ofix-personal-*``, ``row-bot-personal-*``) or gpt-oss:20b.
LOCAL_MODEL = "qwen3.8:27b"


class BenchEnvironmentError(RuntimeError):
    """The worker's environment is not a safe benchmark environment."""


def ollama_identity(model: str, *, host: str = "", client: Any = None) -> dict[str, Any]:
    """The local model's exact tag, digest and details, as Ollama reports them (``/api/tags`` and
    ``/api/show``, the calls behind ``ollama list`` and ``ollama show``). Neither loads the model."""
    import httpx

    if model != LOCAL_MODEL:
        raise BenchEnvironmentError(f"the local model is {LOCAL_MODEL} (stock weights) only, not {model!r}")
    base = host or os.environ.get("OLLAMA_HOST", "") or "127.0.0.1:11434"
    base = base if base.startswith("http") else f"http://{base.replace('0.0.0.0', '127.0.0.1')}"
    http = client or httpx.Client(timeout=20)
    tags = http.get(f"{base}/api/tags").json().get("models") or []
    entry = next((item for item in tags if model in {item.get("name"), item.get("model")}), None)
    if entry is None:
        raise BenchEnvironmentError(f"{model} is not installed in Ollama (ollama pull {model})")
    shown = http.post(f"{base}/api/show", json={"model": model}).json()
    details = shown.get("details") or {}
    return {"tag": str(entry.get("name") or model), "digest": str(entry.get("digest") or ""),
            "size": entry.get("size"), "modified_at": entry.get("modified_at"),
            "family": details.get("family"), "parameter_size": details.get("parameter_size"),
            "quantization_level": details.get("quantization_level"), "format": details.get("format")}


class ProviderLimit(RuntimeError):
    """The provider answered with a usage or rate limit: the run stops cleanly, resumable, with no retry
    loop (the plan's limit is shared with the owner's everyday Row-Bot)."""


def limit_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in PROVIDER_LIMIT_MARKERS)


def reset_hint(text: str) -> str:
    """When the limit resets, as the provider said it ("try again in 2h 5m", "resets at 14:00"), or ''."""
    match = re.search(r"(try again (?:in|after|at)[^.;\n]{1,60}|resets? (?:in|at|after|on)[^.;\n]{1,60}"
                      r"|retry[- ]after[:= ]*[^.;\n]{1,40})", str(text or ""), re.IGNORECASE)
    return match.group(1).strip() if match else ""


class LimitWatch(logging.Handler):
    """Watches Row-Bot's own log lines for a usage or rate limit. Background calls (extraction, Dream)
    swallow their errors and log them, so this is how ingestion learns the plan's limit was reached."""

    def __init__(self) -> None:
        # Warnings and errors only: a failed call is logged at WARNING or above, and INFO lines carry
        # timings and counts that are not limits.
        super().__init__(level=logging.WARNING)
        self.hit = ""

    def emit(self, record: logging.LogRecord) -> None:
        if self.hit:
            return
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        if limit_text(message):
            self.hit = message[:500]

    def check(self) -> None:
        if self.hit:
            raise ProviderLimit(self.hit[:300])


def child_env(data_dir: Path, workspace: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The worker's environment: the live eval's (scratch data folder, keychain closed, no
    notifications), plus the install count off, Learning's preview unset and the graphics
    card hidden (embeddings run on the CPU; a local model runs in Ollama's own process)."""
    env = live_eval_child_env(data_dir, workspace, base)
    for name in ("ROW_BOT_LEARNING_PREVIEW", "ROW_BOT_LEARNING", "ROW_BOT_LIVE_EVAL"):
        env.pop(name, None)
    env["ROW_BOT_INSTALL_COUNT"] = "0"
    env["CUDA_VISIBLE_DEVICES"] = ""
    env["HF_HUB_OFFLINE"] = "1"  # the local embedding model loads from the cache only
    return env


def check_environment(env: Mapping[str, str] | None = None) -> Path:
    """The scratch data folder, after checking the rules every benchmark worker runs under."""
    source = os.environ if env is None else env
    problems: list[str] = []
    data_dir = Path(str(source.get("ROW_BOT_DATA_DIR", "") or "")) if source.get("ROW_BOT_DATA_DIR") else None
    if data_dir is None:
        problems.append("ROW_BOT_DATA_DIR must name the task's scratch data folder")
    elif usage_rows.everyday_data_folder(data_dir):
        problems.append("refusing the everyday Row-Bot data folder")
    profile = str(source.get("ROW_BOT_LIVE_EVAL_CODEX_PROFILE", "") or "").strip()
    if data_dir is not None and profile:
        resolved, root = data_dir.expanduser().resolve(), Path(profile).expanduser().resolve()
        if resolved == root or resolved.is_relative_to(root):
            problems.append("refusing to write a benchmark into the eval profile")
    if not str(source.get("ROW_BOT_TEST_MODE", "") or "").strip():
        problems.append("ROW_BOT_TEST_MODE must be set (it keeps the OS keychain closed)")
    if str(source.get("ROW_BOT_INSTALL_COUNT", "")).strip() != "0":
        problems.append("ROW_BOT_INSTALL_COUNT must be 0")
    if str(source.get("ROW_BOT_LEARNING_PREVIEW", "") or "").strip():
        problems.append("ROW_BOT_LEARNING_PREVIEW must be unset: benchmark data never enters Learning")
    if problems:
        raise BenchEnvironmentError("; ".join(problems))
    assert data_dir is not None
    return data_dir


@dataclass
class BenchConfig:
    """What a task pins. Recorded in full in every trajectory."""

    provider: str
    model: str
    approval_mode: str = HANDS_OFF
    tools: tuple[str, ...] = ("memory", "conversation_search")
    reasoning_effort: str = ""  # "" is the provider's default
    runtime_mode: str = "agent"
    blocked_hosts: tuple[str, ...] = ()
    canaries: tuple[str, ...] = ()

    @property
    def model_ref(self) -> str:
        return f"model:{self.provider}:{self.model}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["canaries"] = len(self.canaries)  # never written out
        data["reasoning_effort"] = self.reasoning_effort or "provider default"
        return data


class SignIn:
    """The eval profile's ChatGPT sign-in in this process's memory only (``seeded/live.SignIn``'s way)."""

    def __init__(self, profile: Path) -> None:
        self.profile = profile
        self.current: Any = None
        self.write_backs: list[str] = []

    def install(self) -> None:
        from tests.e2e.live_eval import eval_profile

        eval_profile.check_profile(self.profile)
        bundle = eval_profile.read_bundle(self.profile)
        if bundle is None:
            raise BenchEnvironmentError(f"the eval profile has no ChatGPT sign-in: {eval_profile.sign_in_hint()}")
        self.current = bundle
        from row_bot import secret_store
        from row_bot.providers import codex

        secret_store.use_memory_backend()
        original = codex.save_codex_oauth_tokens
        original(codex.CodexTokenSet(access_token=bundle.access_token, refresh_token=bundle.refresh_token,
                                     id_token=bundle.id_token, expires_at=bundle.expires_at,
                                     account_id=bundle.account, plan_type=bundle.plan_type))

        def remember(token_set: Any, *args: Any, **kwargs: Any) -> Any:
            saved = original(token_set, *args, **kwargs)
            self._write_back(token_set)
            return saved

        codex.save_codex_oauth_tokens = remember

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


def install_call_gate(ledger: CostLedger) -> Callable[[], None]:
    """Check every model call Row-Bot makes against *ledger* before it is sent (all calls pass
    the usage-ledger callback); a refused call raises :class:`BudgetStop` into the caller."""
    from row_bot.providers import usage_ledger

    callback = usage_ledger.UsageLedgerCallback
    original = callback.on_chat_model_start
    original_raise = callback.raise_error

    def on_chat_model_start(self: Any, serialized: Any, messages: Any, *, run_id: Any, **kwargs: Any) -> None:
        invocation = kwargs.get("invocation_params") or {}
        limit = invocation.get("max_tokens") or invocation.get("max_output_tokens") or invocation.get("num_predict")
        flat = [item for group in (messages or []) for item in (group if isinstance(group, (list, tuple)) else [group])]
        tokens_in, tokens_out = estimate_call(flat, tools=invocation.get("tools"),
                                              max_output=limit if isinstance(limit, int) else None)
        ledger.check(self.provider_id, self.model_name, tokens_in, tokens_out)
        return original(self, serialized, messages, run_id=run_id, **kwargs)

    callback.on_chat_model_start = on_chat_model_start
    callback.raise_error = True

    def undo() -> None:
        callback.on_chat_model_start = original
        callback.raise_error = original_raise

    return undo


class _PinnedDatetime(datetime):
    moment: datetime = datetime(2000, 1, 1)

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        moment = cls.moment
        if tz is None:
            return moment.replace(tzinfo=None) if moment.tzinfo else moment
        return moment.astimezone(tz) if moment.tzinfo else moment.replace(tzinfo=tz)


class _PinnedDate(date):
    moment: date = date(2000, 1, 1)

    @classmethod
    def today(cls) -> date:  # type: ignore[override]
        return cls.moment


@contextlib.contextmanager
def pinned_clock(moment: datetime) -> Iterator[None]:
    """The benchmark's own date, where Row-Bot's memory path reads the clock: the turn context's
    "Current date and time" (``agent._datetime``) and extraction's "Today is" (``memory_extraction.date``).
    Storage timestamps (watermarks, journals, the ledger) keep the real clock."""
    import row_bot.agent as agent
    import row_bot.memory_extraction as extraction

    pinned_dt = type("PinnedDatetime", (_PinnedDatetime,), {"moment": moment})
    pinned_d = type("PinnedDate", (_PinnedDate,), {"moment": moment.date()})
    saved = (agent._datetime, extraction.date)
    agent._datetime, extraction.date = pinned_dt, pinned_d
    try:
        yield
    finally:
        agent._datetime, extraction.date = saved


@dataclass
class BenchRuntime:
    config: BenchConfig
    ledger: CostLedger
    task_id: str = ""
    eval_profile: Path | None = None
    notes: list[str] = field(default_factory=list)
    policy: netguard.NetworkPolicy = field(default_factory=netguard.NetworkPolicy)
    sign_in: SignIn | None = None
    local_model: dict[str, Any] = field(default_factory=dict)
    limits: LimitWatch = field(default_factory=LimitWatch)
    _undo: list[Callable[[], None]] = field(default_factory=list)
    _seen_row: int = 0

    # -- set-up ---------------------------------------------------------------

    def setup(self) -> None:
        data_dir = check_environment()
        from row_bot import data_paths, secret_store

        if Path(data_paths.get_row_bot_data_dir()).resolve() != data_dir.resolve():
            raise BenchEnvironmentError("Row-Bot bound another data folder than ROW_BOT_DATA_DIR")
        if not secret_store._test_mode_active():  # noqa: SLF001
            raise BenchEnvironmentError("test mode is not active")
        from row_bot import learning

        if learning.preview_enabled() or learning.is_enabled():
            raise BenchEnvironmentError("Learning is on in this process; benchmark data never enters Learning")
        import row_bot.agent  # noqa: F401 - binds the runtime before the patches
        from row_bot import models

        self.policy = netguard.NetworkPolicy(blocked_hosts=self.config.blocked_hosts, canaries=self.config.canaries)
        self._undo.append(netguard.install(self.policy))
        watched = logging.getLogger("row_bot")
        watched.addHandler(self.limits)
        self._undo.append(lambda: watched.removeHandler(self.limits))
        if self.config.provider == "codex":
            if self.eval_profile is None:
                raise BenchEnvironmentError("the ChatGPT lane needs the eval profile (ROW_BOT_LIVE_EVAL_CODEX_PROFILE)")
            self.sign_in = SignIn(self.eval_profile)
            self.sign_in.install()
            if self.config.model in {"", "auto"}:
                from row_bot.providers import codex

                from tests.e2e.live_eval.eval_profile import choose_model

                listed = [info.model_id for info in codex.fetch_codex_model_infos()]
                self.config.model = choose_model(listed)
                self.notes.append(f"model {self.config.model} (the live catalog lists {len(listed)} models)")
        if self.config.provider == "ollama":
            from tests.e2e.live_eval.runner import refresh_local_catalog

            self.local_model = ollama_identity(self.config.model)
            self.notes.append(refresh_local_catalog())
        else:
            try:
                result = models.refresh_cloud_provider_models(self.config.provider)
                self.notes.append(f"catalog refresh: {getattr(result, 'status', None) or 'done'}")
            except Exception as exc:  # noqa: BLE001 - the first turn's readiness error says the rest
                self.notes.append(f"catalog refresh failed: {type(exc).__name__}")
        models.adopt_saved_default(self.config.model_ref)
        self._pin_tools()
        self._undo.append(install_call_gate(self.ledger))
        self._seen_row = usage_rows.max_id(self.usage_db())

    def _pin_tools(self) -> None:
        from row_bot.tools import registry

        wanted = set(self.config.tools)
        for tool in registry.get_all_tools():
            if registry.is_enabled(tool.name) != (tool.name in wanted):
                registry.set_enabled(tool.name, tool.name in wanted)
        enabled = {tool.name for tool in registry.get_enabled_tools()}
        missing = wanted - enabled
        if missing:
            raise BenchEnvironmentError(f"pinned tools not available: {sorted(missing)}")

    def close(self) -> None:
        while self._undo:
            try:
                self._undo.pop()()
            except Exception:  # noqa: BLE001
                logger.debug("bench: undo failed", exc_info=True)
        if self.sign_in is not None:
            from tests.e2e.live_eval import eval_profile

            eval_profile.scrub_worker_folder(check_environment())

    # -- calls ----------------------------------------------------------------

    def usage_db(self) -> Path:
        from row_bot import data_paths

        return Path(data_paths.get_usage_db_path(create_parent=False))

    def drain_calls(self, step: Step, *, phase: str = "") -> list[CallEntry]:
        """Record the calls Row-Bot made since the last drain, in the ledger and on *step*."""
        rows = usage_rows.rows_after(self.usage_db(), self._seen_row)
        self._seen_row = max(self._seen_row, usage_rows.max_id(self.usage_db()))
        phase = phase or step.kind
        # A turn's calls use the conversation's reasoning setting; ingestion and Dream's are background
        # calls, at the lowest effort with a bounded reply (the product's own rule, WP C1).
        settings = {"reasoning_effort": self.config.reasoning_effort or "provider default"} if phase == "turn" \
            else {"reasoning_effort": "background: lowest, product default"} if phase in {"ingest", "dream"} else {}
        entries = []
        for row in rows:
            entries.append(self.ledger.record(CallEntry(
                provider=row.provider, model=row.model, purpose=row.purpose, task_id=self.task_id,
                phase=phase, input_tokens=row.input_tokens, cache_read_tokens=row.cache_read_tokens,
                output_tokens=row.output_tokens, reasoning_tokens=row.reasoning_tokens, latency_ms=row.latency_ms,
                ttft_ms=row.ttft_ms, cost_usd=row.cost, settings=dict(settings),
            )))
        step.calls.extend(entries)
        return entries

    # -- threads and turns ----------------------------------------------------

    def new_thread(self, name: str, *, incognito: bool = False, thread_id: str = "") -> str:
        from row_bot import threads

        thread_id = thread_id or f"bench_{uuid.uuid4().hex[:12]}"
        threads.create_thread(name, thread_id=thread_id, approval_mode=self.config.approval_mode,
                              model_override=self.config.model_ref, seed_default_skills=False, incognito=incognito)
        if self.config.reasoning_effort:
            threads.set_thread_reasoning_selection(thread_id, self.config.model_ref,
                                                   {"kind": "effort", "effort": self.config.reasoning_effort})
        return thread_id

    def turn(self, thread_id: str, prompt: str, *, name: str = "") -> Step:
        """One message, sent as the app sends it; whatever still stops for a person is declined."""
        import row_bot.agent as agent

        from tests.e2e.live_eval.runner import _consume
        from tests.e2e.live_eval.tasks import TurnResult

        step = Step(kind="turn", name=name, prompt=prompt)
        result = TurnResult(prompt=prompt, thread_id=thread_id)
        config = {"configurable": {
            "thread_id": thread_id, "model_override": self.config.model_ref,
            "runtime_mode": self.config.runtime_mode, "runtime_surface": "normal_chat",
            "approval_mode": self.config.approval_mode, "generation_id": uuid.uuid4().hex,
        }}
        started = time.monotonic()
        tools = list(self.config.tools)
        try:
            interrupts = _consume(agent.stream_agent(prompt, tools, config), result)
            resumes = 0
            while interrupts and resumes < MAX_RESUMES:
                resumes += 1
                ids = [str(item.get("__interrupt_id")) for item in interrupts if item.get("__interrupt_id")]
                interrupts = _consume(agent.resume_stream_agent(tools, config, False, interrupt_ids=ids or None),
                                      result)
        except BudgetStop as exc:
            result.status = "budget"
            result.errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - recorded as the turn's error
            result.status = "exception"
            result.errors.append(f"{type(exc).__name__}: {exc}"[:500])
        finally:
            agent._set_active_runtime_context()  # noqa: SLF001
        step.seconds = round(time.monotonic() - started, 3)
        if result.status == "unknown":
            result.status = "error"
            result.errors.append("the turn ended without a final answer")
        if any("benchmark budget" in error for error in result.errors):
            result.status = "budget"
        step.status = result.status
        step.answer = result.answer
        step.errors = result.errors[:10]
        step.tool_calls = [{"name": call.name, "args": {key: str(value)[:_TOOL_ARGS] for key, value in
                                                         (call.args or {}).items()}} for call in result.tool_calls]
        step.metrics = {"interrupts_declined": result.interrupts, "tool_results": len(result.tool_results)}
        self.drain_calls(step)
        if step.status != "done" and limit_text(" ".join(step.errors)):
            raise ProviderLimit(step.errors[0][:300])
        self.limits.check()  # a limit a background call met during the turn (recall, a title)
        return step
