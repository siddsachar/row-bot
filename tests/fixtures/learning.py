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
            if any(part == "learning" or part.startswith("learning.db") or part == "learning_config.json"
                   for part in relative.parts):
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
