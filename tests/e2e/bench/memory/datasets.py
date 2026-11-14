"""Memory benchmarks as tasks: LoCoMo (with the audited key) and LongMemEval-S.

Downloaded files are untrusted data: they are read here with ``json`` only, never run,
and they stay in their own folder under ``.local/`` (git ignores it).

LoCoMo (snap-research/locomo, ``locomo10.json``; the copy in dial481/locomo-audit is the
same file, SHA-256 checked there):

- 10 conversations between two people, 19 to 32 dated sessions each, and 1,986 questions in
  five categories: 1 multi-hop, 2 temporal, 3 open-domain, 4 single-hop, 5 adversarial.
  Published memory-system numbers (Mem0, Zep, Letta, MemOS) score categories 1-4 (1,540
  questions) with an LLM judge; category 5 is left out by default here too.
- The audited key is dial481/locomo-audit's ``errors.json``: an entry per flawed question
  (``locomo_<conversation>_qa<index>``, both counted from 0). Entries of type
  ``WRONG_CITATION`` (only the evidence is wrong) keep the original answer; every other
  type replaces the gold answer with the audit's ``correct_answer``. Both keys are kept, so
  one set of answers is scored on each.
- Row-Bot's memory is one person's. A LoCoMo conversation is read once per speaker
  ("perspective"), the way Mem0's protocol keeps a memory per speaker: in perspective A the
  person is speaker A (their messages are the person's, speaker B's are the other side), in
  its own scratch data folder. Each question goes to the perspective of the speaker it names
  first (word match), or speaker A when it names neither.
- A shared image is kept as its caption, as text readers of LoCoMo get it.

LongMemEval-S (xiaowu0162/longmemeval-cleaned, ``longmemeval_s_cleaned.json``): 500
questions, each with its own haystack of about 50 dated user/assistant sessions and its own
``question_date``. One question is one task with its own scratch memory. Abstention
questions are those whose id has ``_abs``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

LOCOMO = "locomo"
LONGMEMEVAL_S = "longmemeval-s"
LOCOMO_CATEGORIES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}
LOCOMO_SCORED = (1, 2, 3, 4)
CITATION_ONLY = "WRONG_CITATION"
_LOCOMO_DATE = "%I:%M %p on %d %B, %Y"
_LONGMEMEVAL_DATE = re.compile(r"^\s*(\d{4})/(\d{2})/(\d{2})(?:\s*\([A-Za-z]+\))?\s*(\d{1,2}):(\d{2})")


@dataclass
class Turn:
    role: str  # "user" (the person) or "assistant" (the other side)
    text: str
    speaker: str = ""
    turn_id: str = ""


@dataclass
class Session:
    session_id: str
    when: datetime
    turns: list[Turn] = field(default_factory=list)


@dataclass
class Question:
    question_id: str
    question: str
    answer: str
    category: str
    audited_answer: str | None = None
    abstention: bool = False
    evidence: list[str] = field(default_factory=list)


@dataclass
class MemoryTask:
    """One scratch memory: sessions to ingest, in order, then questions asked at ``ask_at``."""

    task_id: str
    benchmark: str
    sessions: list[Session]
    questions: list[Question]
    ask_at: datetime
    person: str = ""  # who the person is in this perspective (LoCoMo); "" for LongMemEval

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "benchmark": self.benchmark, "person": self.person,
            "ask_at": self.ask_at.isoformat(),
            "sessions": [{"session_id": item.session_id, "when": item.when.isoformat(),
                          "turns": [vars(turn) for turn in item.turns]} for item in self.sessions],
            "questions": [vars(question) for question in self.questions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryTask:
        return cls(
            task_id=str(data["task_id"]), benchmark=str(data["benchmark"]), person=str(data.get("person") or ""),
            ask_at=datetime.fromisoformat(data["ask_at"]),
            sessions=[Session(str(item["session_id"]), datetime.fromisoformat(item["when"]),
                              [Turn(**turn) for turn in item["turns"]]) for item in data["sessions"]],
            questions=[Question(**question) for question in data["questions"]],
        )


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── LoCoMo ─────────────────────────────────────────────────────────────────


def audited_answers(errors: Iterable[dict[str, Any]]) -> dict[tuple[int, int], str]:
    """(conversation, question index) -> the audit's answer, for the entries that change the answer."""
    found: dict[tuple[int, int], str] = {}
    for entry in errors:
        match = re.fullmatch(r"locomo_(\d+)_qa(\d+)", str(entry.get("question_id") or ""))
        if not match or str(entry.get("error_type") or "") == CITATION_ONLY:
            continue
        answer = str(entry.get("correct_answer") or "").strip()
        if answer:
            found[(int(match.group(1)), int(match.group(2)))] = answer
    return found


def _names_first(question: str, names: Sequence[str]) -> int:
    """The index in *names* of the name the question mentions first, or 0."""
    positions = []
    for index, name in enumerate(names):
        match = re.search(rf"\b{re.escape(name)}\b", question)
        if match:
            positions.append((match.start(), index))
    return min(positions)[1] if positions else 0


def _locomo_text(turn: dict[str, Any]) -> str:
    text = str(turn.get("text") or "").strip()
    caption = str(turn.get("blip_caption") or "").strip()
    return f"{text} [shared an image: {caption}]".strip() if caption else text


def locomo_tasks(
    data_path: Path,
    errors_path: Path | None = None,
    *,
    conversations: Sequence[int] | None = None,
    categories: Sequence[int] = LOCOMO_SCORED,
    perspectives: str = "both",
    questions_per_conversation: int | None = None,
) -> list[MemoryTask]:
    """LoCoMo's conversations as tasks (one per conversation and perspective)."""
    if perspectives not in {"both", "a"}:
        raise ValueError("perspectives is 'both' (one memory per speaker) or 'a' (speaker A only)")
    data = _read_json(data_path)
    audit = audited_answers(_read_json(errors_path)) if errors_path else {}
    chosen = range(len(data)) if conversations is None else conversations
    tasks: list[MemoryTask] = []
    for conversation_index in chosen:
        sample = data[conversation_index]
        conversation = sample["conversation"]
        names = (str(conversation["speaker_a"]), str(conversation["speaker_b"]))
        keys = sorted((key for key, value in conversation.items()
                       if re.fullmatch(r"session_\d+", key) and isinstance(value, list) and value),
                      key=lambda key: int(key.split("_")[1]))
        raw_sessions = [(key, datetime.strptime(conversation[f"{key}_date_time"], _LOCOMO_DATE), conversation[key])
                        for key in keys]
        questions: list[list[Question]] = [[], []]
        kept = 0
        for index, qa in enumerate(sample["qa"]):
            if int(qa["category"]) not in categories:
                continue
            if questions_per_conversation is not None and kept >= questions_per_conversation:
                break
            kept += 1
            answer = qa.get("answer", qa.get("adversarial_answer", ""))
            question = Question(
                question_id=f"locomo_{conversation_index}_qa{index}", question=str(qa["question"]),
                answer=str(answer), category=LOCOMO_CATEGORIES.get(int(qa["category"]), str(qa["category"])),
                audited_answer=audit.get((conversation_index, index), str(answer)),
                abstention=int(qa["category"]) == 5, evidence=[str(item) for item in qa.get("evidence") or []],
            )
            side = _names_first(question.question, names) if perspectives == "both" else 0
            questions[side].append(question)
        last = raw_sessions[-1][1] if raw_sessions else datetime(2023, 1, 1)
        for side in ((0, 1) if perspectives == "both" else (0,)):
            person = names[side]
            sessions = [Session(key, when, [Turn("user" if turn.get("speaker") == person else "assistant",
                                                 _locomo_text(turn), str(turn.get("speaker") or ""),
                                                 str(turn.get("dia_id") or "")) for turn in turns])
                        for key, when, turns in raw_sessions]
            tasks.append(MemoryTask(
                task_id=f"locomo_{conversation_index}_{'ab'[side]}", benchmark=LOCOMO, sessions=sessions,
                questions=questions[side], ask_at=last + timedelta(days=1), person=person,
            ))
    return tasks


# ── LongMemEval-S ──────────────────────────────────────────────────────────


def parse_longmemeval_date(text: str) -> datetime:
    """``2023/05/20 (Sat) 02:21`` -> datetime."""
    match = _LONGMEMEVAL_DATE.match(str(text or ""))
    if not match:
        raise ValueError(f"not a LongMemEval date: {text!r}")
    year, month, day, hour, minute = (int(part) for part in match.groups())
    return datetime(year, month, day, hour, minute)


def longmemeval_tasks(data_path: Path, *, question_ids: Sequence[str] | None = None,
                      limit: int | None = None) -> list[MemoryTask]:
    """LongMemEval instances as tasks, one question (and its own haystack) each, in file order."""
    wanted = set(question_ids or ())
    tasks: list[MemoryTask] = []
    for instance in _read_json(data_path):
        question_id = str(instance["question_id"])
        if wanted and question_id not in wanted:
            continue
        ids = list(instance.get("haystack_session_ids") or [])
        sessions = []
        for index, (when, turns) in enumerate(zip(instance["haystack_dates"], instance["haystack_sessions"])):
            sessions.append(Session(str(ids[index]) if index < len(ids) else f"session_{index}",
                                    parse_longmemeval_date(when),
                                    [Turn(str(turn.get("role") or ""), str(turn.get("content") or ""))
                                     for turn in turns if str(turn.get("role") or "") in {"user", "assistant"}]))
        sessions.sort(key=lambda item: item.when)
        answer = instance.get("answer", "")
        tasks.append(MemoryTask(
            task_id=f"lme_{question_id}", benchmark=LONGMEMEVAL_S, sessions=sessions,
            questions=[Question(question_id=question_id, question=str(instance["question"]), answer=str(answer),
                                category=str(instance.get("question_type") or ""), abstention="_abs" in question_id,
                                evidence=[str(item) for item in instance.get("answer_session_ids") or []])],
            ask_at=parse_longmemeval_date(instance["question_date"]),
        ))
        if limit is not None and len(tasks) >= limit:
            break
    return tasks
