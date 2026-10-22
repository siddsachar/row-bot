"""Learning test helpers: settings on disk, a clock tests move, sample records
and a finder for every Learning file in a data folder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from row_bot.learning import contracts
from row_bot.learning.contracts import (
    Attempt,
    Cost,
    DataSpec,
    Decision,
    Episode,
    Job,
    JobTransition,
    LessonRecord,
    Pin,
    Ref,
    Release,
    ReleaseIdentity,
    Signal,
    TaskFixture,
    TestRun,
)

START = 1_800_000_000.0
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


class FakeClock:
    """Epoch seconds that only move when the test says so."""

    def __init__(self, now: float = START) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def set_learning_preview(monkeypatch: Any, on: bool = True) -> None:
    """Start-up's preview flag (O34), as if the app had been started with or without it."""

    from row_bot import learning

    if on:
        monkeypatch.setenv(learning.PREVIEW_ENV, "1")
    else:
        monkeypatch.delenv(learning.PREVIEW_ENV, raising=False)
    monkeypatch.setattr(learning, "_PREVIEW", on)
    learning.invalidate()


def save_learning_settings(root: Path, **values: Any) -> None:
    """Write ``learning_config.json`` the way Settings saves it (merged)."""

    from row_bot import learning

    path = Path(root) / learning.SETTINGS_FILE
    document = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    sources = values.pop("sources", None)
    document.update(values)
    if sources is not None:
        document.setdefault("sources", {}).update(sources)
    path.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    learning.invalidate()


def learning_files(*roots: Path) -> list[str]:
    """Every Learning file or folder under the given data folders."""

    found: list[str] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if any(part in ("learning", "learning_models") or part.startswith("learning.db")
                   or part == "learning_config.json" for part in relative.parts):
                found.append(relative.as_posix())
    return sorted(found)


class FakeThreads:
    """A scope lookup (``scope.ThreadLookup``) over fixed conversations and channel owners."""

    def __init__(self, threads: dict[str, Any] | None = None, owners: dict[str, str] | None = None) -> None:
        from row_bot.learning.scope import ThreadInfo

        self.threads: dict[str, Any] = dict(threads or {})
        self.owners = dict(owners or {})
        self.reads = 0
        self._info = ThreadInfo

    def chat(self, thread_id: str) -> FakeThreads:
        """Add a chat in the app (its owner's)."""

        self.threads[thread_id] = self._info(exists=True)
        return self

    def child(self, thread_id: str, parent: str) -> FakeThreads:
        """Add a delegated Agent's thread started from ``parent``."""

        self.threads[thread_id] = self._info(exists=True, thread_type="agent_child", parent_thread_id=parent)
        return self

    def channel(self, thread_id: str, channel: str, target: str, *, shared: bool = False) -> FakeThreads:
        self.threads[thread_id] = self._info(exists=True, channel=channel, channel_target=target, shared=shared)
        return self

    def thread(self, thread_id: str) -> Any:
        self.reads += 1
        return self.threads.get(thread_id) or self._info(exists=False)

    def owner_identity(self, channel: str) -> str:
        return self.owners.get(channel, "")


def all_blob_text(root: Path) -> str:
    """Every blob in a data folder's Learning store, as text (to look for what must not be there)."""

    folder = Path(root) / "learning" / "blobs"
    if not folder.is_dir():
        return ""
    return "\n".join(path.read_bytes().decode("utf-8", "replace") for path in sorted(folder.rglob("*"))
                     if path.is_file())


def all_record_text(root: Path) -> str:
    """Every record in a data folder's ``learning.db`` as text."""

    import sqlite3

    path = Path(root) / "learning.db"
    if not path.is_file():
        return ""
    connection = sqlite3.connect(path)
    try:
        return "\n".join(str(row[0]) for row in connection.execute("SELECT body FROM records"))
    finally:
        connection.close()


def read_json_blob(store: Any, sha: str | None) -> Any:
    """A JSON blob's value (None when missing)."""

    data = store.read_blob(sha or "")
    return None if data is None else json.loads(data.decode("utf-8"))


def episode(thread_id: str, record_id: str = "ep_1", **fields: Any) -> contracts.Episode:
    fields.setdefault("source", "my_chat")
    fields.setdefault("started_at", START)
    return contracts.Episode(id=record_id, thread_id=thread_id, **fields)


def decision(thread_id: str, record_id: str = "dec_1", episode_id: str = "ep_1", **fields: Any) -> contracts.Decision:
    fields.setdefault("sequence", 0)
    fields.setdefault("kind", "model_call")
    fields.setdefault("created_at", START)
    return contracts.Decision(id=record_id, episode_id=episode_id, thread_id=thread_id, **fields)


def signal(thread_id: str, record_id: str = "sig_1", **fields: Any) -> contracts.Signal:
    fields.setdefault("kind", "teach")
    fields.setdefault("created_at", START)
    return contracts.Signal(id=record_id, thread_id=thread_id, **fields)


def lesson(record_id: str = "les_1", **fields: Any) -> contracts.LessonRecord:
    fields.setdefault("title", "Pick documents by ID")
    fields.setdefault("text", "When two documents share a title, choose by ID.")
    fields.setdefault("origin", "learning")
    fields.setdefault("created_at", START)
    fields.setdefault("updated_at", START)
    return contracts.LessonRecord(id=record_id, **fields)


def release(record_id: str = "rel_1", **fields: Any) -> contracts.Release:
    fields.setdefault("kind", "model")
    fields.setdefault("version", 1)
    fields.setdefault("created_at", START)
    return contracts.Release(id=record_id, **fields)


def attempt(record_id: str, **fields: Any) -> contracts.Attempt:
    fields.setdefault("change_kind", "lesson")
    fields.setdefault("hypothesis", "Picking by ID fixes wrong-document lookups.")
    fields.setdefault("created_at", START)
    return contracts.Attempt(id=record_id, **fields)


def full_records() -> list[contracts.Record]:
    """One fully populated record of every type."""

    return [
        Episode(
            id="ep_1", thread_id="agent-child-1", source="channel_chat", started_at=1700000000.25,
            conversation_id="tg_42_abc", channel="telegram", parent_episode_id="ep_0",
            root_episode_id="ep_0", agent_run_id="run_1", kind_of_work="Document lookups",
            model_ref="openai:gpt-5.6-sol", status="completed", ended_at=1700000009.5,
            fidelity="redacted", incomplete=True,
        ),
        Decision(
            id="dec_1", episode_id="ep_1", thread_id="agent-child-1", sequence=3, kind="tool_call",
            created_at=1700000001.0, conversation_id="tg_42_abc", fidelity="redacted", untrusted=True,
            status="error", model_ref="openai:gpt-5.6-sol", tool_schema_hash=SHA_D,
            request_blob=SHA_A, raw_output_blob=SHA_B, repaired_output_blob=SHA_C, executed="repaired",
            tool_name="read_file", tool_call_id="call_7", arguments_blob=SHA_A, result_blob=SHA_B,
            result_handle="tool-output:7", retry_of="dec_0", ledger_call_id="ledger-9", incomplete=False,
        ),
        Signal(
            id="sig_1", kind="teach", thread_id="chat-1", created_at=1700000002.0, episode_id="ep_1",
            decision_id="dec_1", category="wrong_tool_or_step", note="Use the document's ID, not its title",
            withdrawn_at=1700000003.0,
        ),
        LessonRecord(
            id="les_1", title="Pick documents by ID", text="When two documents share a title, choose by ID.",
            origin="learning", created_at=10.0, updated_at=20.0, status="in_use",
            kind_of_work="Document lookups", memory_id="mem-1", proposal_id="prop_1",
            evidence=(Ref("signal", "sig_1"), Ref("decision", "dec_1"), Ref("thread", "chat-1")),
            test_run_ids=("run_1",), source_thread_ids=("chat-1", "chat-2"),
        ),
        TaskFixture(
            id="fix_1", origin="replay", input_blob=SHA_A, verifier="exact_tool_call", created_at=5.0,
            kind_of_work="Document lookups", expected_blob=SHA_B, source_decision_id="dec_1",
            thread_id="chat-1", novelty_checked=True,
        ),
        TestRun(
            id="run_1", subject_kind="lesson", suite="pack", started_at=30.0, subject_id="les_1",
            baseline_id="base_1", pack="document_lookups", model_ref="ollama:qwen3:4b", status="completed",
            passed=21, failed=3, total=24, budget_tokens=85000, cost=Cost(tokens=84000, gpu_seconds=0.0),
            comparison_set="cmp_1", results_blob=SHA_C, ended_at=40.0, source_thread_ids=("chat-1",),
        ),
        Attempt(
            id="att_3", change_kind="lesson", hypothesis="Picking by ID fixes the wrong-document lookups.",
            created_at=50.0, parent_ids=("att_1", "att_2"), strategy_release_id="strat_5",
            kind_of_work="Document lookups", evidence=(Ref("signal", "sig_1"),), outcome="improved", kept=True,
            reason="21 of 24 passed against 15 of 24.", cost=Cost(tokens=120000, gpu_seconds=12.5),
            test_run_ids=("run_1",), release_id="rel_2", finished_at=60.0, source_thread_ids=("chat-1",),
        ),
        Release(
            id="rel_2", kind="model", version=3, created_at=70.0, label="Personal model v3",
            parent_ids=("rel_1",), attempt_id="att_3", status="trial",
            identity=ReleaseIdentity(
                base=Pin("Qwen/Qwen3-4B", "rev-1", SHA_A),
                tokenizer=Pin("Qwen/Qwen3-4B tokenizer", "rev-1"),
                template=Pin("chatml-tools", "2"),
                adapter=Pin("lora-r16", "", SHA_B),
                quantization="Q4_K_M",
                runtime=Pin("ollama", "0.12.3"),
                way_of_working=Pin("rel_ww_4", "4"),
                lessons=("les_1",),
                data=DataSpec(
                    dataset_id="ds_1", sha256=SHA_C, examples=412,
                    sources=("main_model_work", "corrections", "practice", "replay", "general"),
                    providers=("openai", "anthropic"),
                ),
                recipe=Pin("sft-8gb", "1"),
                tests=("run_1",),
            ),
            content_blob=None, source_thread_ids=("chat-1", "chat-2"),
        ),
        Job(
            id="job_1", kind="training", created_at=80.0, updated_at=90.0, state="unknown_remote",
            backend="hugging_face", release_id="rel_2", attempt_id="att_3", spec_blob=SHA_D,
            steps_done=340, steps_total=1000, checkpoint_step=300, remote_request_id="hf-job-123",
            error_code="contact_lost", cost=Cost(tokens=0, gpu_seconds=3000.0),
            version=3, trigger="train_now", started_at=81.0, finished_at=None, progress_at=89.5,
            seconds_per_step=3.6, loss=0.42, recovered="previous_checkpoint",
            transitions=(JobTransition(state="planned", at=80.0),
                         JobTransition(state="running", at=81.0, reason="train_now"),
                         JobTransition(state="unknown_remote", at=90.0, reason="contact_lost")),
        ),
    ]


def minimal_records() -> list[contracts.Record]:
    """The fewest fields each record needs."""

    return [
        Episode(id="ep_m", thread_id="chat-1", source="my_chat", started_at=1),
        Decision(id="dec_m", episode_id="ep_m", thread_id="chat-1", sequence=0, kind="model_call", created_at=1),
        Signal(id="sig_m", kind="stop", thread_id="chat-1", created_at=1),
        LessonRecord(id="les_m", title="t", text="x", origin="agent_saved", created_at=1, updated_at=1),
        TaskFixture(id="fix_m", origin="pack", input_blob=SHA_A, verifier="v", created_at=1),
        TestRun(id="run_m", subject_kind="baseline", suite="replay", started_at=1),
        Attempt(id="att_m", change_kind="wait", hypothesis="", created_at=1),
        Release(id="rel_m", kind="strategy", version=1, created_at=1),
        Job(id="job_m", kind="test", created_at=1, updated_at=1),
    ]


# ── Captured work (L2c): real turns through Capture's seams ────────────────


class CapturedWork:
    """Real captured turns (``Capture``'s seams, as the agent calls them): each asks a question,
    calls the calculator, reads its result and answers. Conversations keep their history."""

    SYSTEM = "You are Row-Bot. Use tools when they help."
    TOOLS = [{"type": "function", "function": {
        "name": "calculate", "description": "Evaluate a math expression.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}},
                       "required": ["expression"]}}}]

    def __init__(self, writer, chats, clock) -> None:
        self.writer, self.chats, self.clock = writer, chats, clock
        from collections import defaultdict

        self.history: dict[str, list] = defaultdict(list)
        self.count = 0

    def turn(self, thread_id: str, question: str, answer: str, *, model_ref: str = "gpt-5.6-sol",
             provider_id: str = "openai", end: str = "completed", tool_status: str = "ok",
             args: dict | None = None, tool: bool = True, raw_answer: str | None = None,
             person_message: bool = False) -> list[str]:
        """One turn; returns its model calls' decision ids (the tool step's, then the answer's).
        ``person_message``: the person sent it (capture marks the reply it followed)."""

        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        if thread_id not in self.chats.threads:
            self.chats.chat(thread_id)
        self.count += 1
        number = self.count
        turn = self.writer.begin_turn(thread_id=thread_id, surface="normal_chat", person_message=person_message)
        asked = HumanMessage(content=question, id=f"h{number}")
        sent = [SystemMessage(content=self.SYSTEM, id="sys"), *self.history[thread_id], asked]
        ids: list[str] = []
        added: list = [asked]
        if tool:
            call = {"id": f"call_{number}", "name": "calculate", "args": args or {"expression": "6 * 7"}}
            step = AIMessage(content="", id=f"a{number}", tool_calls=[call])
            ids.append(self.writer.model_call(turn, messages=sent, response=step, tools=self.TOOLS, model_ref=model_ref,
                                              provider_id=provider_id))
            result = ToolMessage(content="6 * 7 = 42", tool_call_id=call["id"], id=f"t{number}",
                                 status="error" if tool_status != "ok" else "success")
            self.writer.tool_call(turn, call=call, result=result, status=tool_status)
            sent = [*sent, step, result]
            added += [step, result]
        reply = AIMessage(content=raw_answer if raw_answer is not None else answer, id=f"r{number}")
        ids.append(self.writer.model_call(turn, messages=sent, response=reply, tools=self.TOOLS, model_ref=model_ref,
                                          provider_id=provider_id))
        added.append(AIMessage(content=answer, id=f"r{number}-kept"))
        self.writer.end_turn(turn, end)
        self.writer.drain()
        self.history[thread_id] += added
        self.clock.advance(60)
        return ids


# ── Training (L2a): the fake trainer, its engine, a dataset and a lease ────────

FAKE_TRAINER = Path(__file__).with_name("learning_trainer.py").resolve()


class FakeEngine:
    """The training engine for tests and the browser fixture: the fake trainer, run by the app's
    Python (``options`` are the fake trainer's, see ``learning_trainer.py``)."""

    def __init__(self, ready: bool = True, **options: Any) -> None:
        self.is_ready = ready
        self.options: dict[str, Any] = dict(options)
        self.commands: list[Any] = []

    def ready(self) -> bool:
        return self.is_ready

    def command(self, job: Any) -> Any:
        import sys

        from row_bot.learning.training.worker import PROTOCOL_PATH, EngineCommand

        # The base interpreter, never a virtual environment's python.exe launcher: on Windows that
        # launcher starts the real interpreter before the worker's job object can own it.
        interpreter = getattr(sys, "_base_executable", "") or sys.executable
        command = EngineCommand(
            argv=(interpreter, "-I", "-B", str(FAKE_TRAINER), str(PROTOCOL_PATH), json.dumps(self.options)),
            env={"FAKE_ENGINE": "1"}, name="fake",
        )
        self.commands.append(command)
        return command


class FakeDataset:
    """Prepares a job's lease: a few synthetic examples (never chat data)."""

    def __init__(self, examples: int = 12, validation: int = 0) -> None:
        self.examples = examples
        self.validation = validation
        self.prepared: list[str] = []
        self.started: list[str] = []  # the jobs that started without a step trained

    def starting(self, job: Any) -> Any:
        self.started.append(job.id)
        return job

    def prepare(self, job: Any, folder: Path) -> int:
        lines = [json.dumps({"messages": [{"role": "user", "content": f"Synthetic task {index}"},
                                          {"role": "assistant", "content": "Done."}]})
                 for index in range(self.examples)]
        (Path(folder) / "examples.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        if self.validation:
            (Path(folder) / "validation.jsonl").write_text("\n".join(lines[:self.validation]) + "\n",
                                                           encoding="utf-8", newline="\n")
        self.prepared.append(job.id)
        return self.examples


class FakeLease:
    """A resource lease whose holders the test sets (``ResourceLease``'s ``read``)."""

    def __init__(self, **state: Any) -> None:
        from row_bot.learning.lease import LeaseState

        self._state_type = LeaseState
        self.state = LeaseState(**state)
        self.reads = 0

    def set(self, **state: Any) -> None:
        self.state = self._state_type(**state)

    def read(self) -> Any:
        self.reads += 1
        return self.state


def wait_for_file(path: Path, timeout: float = 10.0) -> str:
    """A file another process writes, once it has content (no sleeping: polled on an event)."""

    import threading
    import time

    deadline = time.monotonic() + timeout
    tick = threading.Event()
    while time.monotonic() < deadline:
        try:
            text = Path(path).read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
        tick.wait(0.02)
    raise TimeoutError(f"{path} was not written")


def process_alive(pid: int) -> bool:
    """Whether a process id still names a running process (any OS)."""

    import os
    import subprocess

    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel.OpenProcess(0x1000 | 0x00100000, False, int(pid))  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie (exited, not yet reaped) is not alive
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(int(pid))], capture_output=True, text=True,
                               timeout=5).stdout.strip()
        return bool(state) and not state.startswith("Z")
    except (OSError, subprocess.SubprocessError):
        return True
