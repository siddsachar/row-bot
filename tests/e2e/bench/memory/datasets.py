"""Memory benchmarks as tasks: LoCoMo (with the audited key), LongMemEval-S, BEAM (1M) and LongMemEval-V2.

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

BEAM 1M (mohammadtavakoli78/BEAM, ``chats/1M/<n>/``; data CC BY-SA 4.0): 35 conversations of
about a million tokens between a person and an assistant, each in ten dated batches (the
sessions), and 20 probing questions per conversation, two in each of ten abilities, each
with a rubric the judge scores item by item.

LongMemEval-V2 (xiaowu0162/longmemeval-v2, Apache-2.0): 451 questions over web-agent
trajectories (web shops, a forum, a ServiceNow-style portal), not chats. Each trajectory is
read as one saved conversation: the task as the person's message, then each step (page,
thought, action, the page's accessibility tree) as the assistant's, then the outcome. In the
small tier all of a domain's questions share one 100-trajectory haystack, so a domain is one
task. Question screenshots are not sent (turns are text); the report counts them.
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
    rubric: list[str] = field(default_factory=list)  # BEAM: the judge scores each item
    eval_function: str = ""  # LongMemEval-V2: the scorer spec, e.g. "norm_phrase_set_match|lower=true"
    image: str = ""  # LongMemEval-V2: a question screenshot (turns are text: it is not sent, and counted)


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


def audit_review(data_path: Path, errors_path: Path) -> list[dict[str, Any]]:
    """Every gold answer the audited key changes, for a person to read before a number is published:
    the question, the original and the audit's answer, the error type and the audit's reasoning."""
    data = _read_json(data_path)
    rows: list[dict[str, Any]] = []
    for entry in _read_json(errors_path):
        match = re.fullmatch(r"locomo_(\d+)_qa(\d+)", str(entry.get("question_id") or ""))
        if not match or str(entry.get("error_type") or "") == CITATION_ONLY or not str(entry.get("correct_answer") or "").strip():
            continue
        qa = data[int(match.group(1))]["qa"][int(match.group(2))]
        rows.append({"question_id": entry["question_id"], "category": LOCOMO_CATEGORIES.get(int(qa["category"])),
                     "question": qa["question"], "original": str(qa.get("answer", qa.get("adversarial_answer", ""))),
                     "audited": str(entry["correct_answer"]).strip(), "error_type": entry.get("error_type"),
                     "reasoning": str(entry.get("reasoning") or "")[:600], "reviewed": None})
    return rows


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


# ── BEAM (1M) ──────────────────────────────────────────────────────────────

BEAM_1M = "beam-1m"
BEAM_CATEGORIES = ("abstention", "contradiction_resolution", "event_ordering", "information_extraction",
                   "instruction_following", "knowledge_update", "multi_session_reasoning", "preference_following",
                   "summarization", "temporal_reasoning")
_BEAM_GOLD_FIELDS = ("answer", "ideal_answer", "ideal_response", "ideal_summary", "expected_compliance")


def _beam_anchor(text: str) -> datetime:
    """``March-01-2024`` -> 1 March 2024, 09:00 (BEAM dates its sessions, not its messages' times)."""
    return datetime.strptime(str(text).strip(), "%B-%d-%Y").replace(hour=9)


def beam_tasks(chats_dir: Path, *, conversations: Sequence[int] | None = None,
               questions_per_category: int | None = None) -> list[MemoryTask]:
    """BEAM's conversations (``chats/1M/<n>/chat.json`` and ``probing_questions/probing_questions.json``
    in the BEAM repository) as tasks: one per conversation, its ten batches the sessions, dated by
    each batch's time anchor; the probing questions with their rubrics, asked a day after the last."""
    root = Path(chats_dir)
    numbers = sorted(int(item.name) for item in root.iterdir() if item.is_dir() and item.name.isdigit())
    tasks: list[MemoryTask] = []
    for number in (numbers if conversations is None else conversations):
        folder = root / str(number)
        sessions: list[Session] = []
        when = datetime(2024, 1, 1, 9)
        for batch in _read_json(folder / "chat.json"):
            messages = [message for pair in batch.get("turns") or [] for message in pair]
            anchor = next((message.get("time_anchor") for message in messages if message.get("time_anchor")),
                          batch.get("time_anchor"))
            when = _beam_anchor(anchor) if anchor else when
            sessions.append(Session(f"batch_{batch.get('batch_number')}", when, [
                Turn(str(message.get("role") or ""), str(message.get("content") or ""), "",
                     str(message.get("id", ""))) for message in messages
                if str(message.get("role") or "") in {"user", "assistant"}]))
        probing = _read_json(folder / "probing_questions" / "probing_questions.json")
        questions: list[Question] = []
        for category in BEAM_CATEGORIES:
            for index, item in enumerate(probing.get(category) or []):
                if questions_per_category is not None and index >= questions_per_category:
                    break
                gold = next((item[name] for name in _BEAM_GOLD_FIELDS if item.get(name)), "")
                questions.append(Question(
                    question_id=f"beam_{number}_{category}_{index}", question=str(item["question"]),
                    answer=str(gold), category=category, abstention=category == "abstention",
                    rubric=[str(entry) for entry in item.get("rubric") or []]))
        tasks.append(MemoryTask(task_id=f"beam1m_{number}", benchmark=BEAM_1M, sessions=sessions,
                                questions=questions, ask_at=sessions[-1].when + timedelta(days=1)))
    return tasks


# ── LongMemEval-V2 ─────────────────────────────────────────────────────────

LONGMEMEVAL_V2 = "longmemeval-v2"
#: LongMemEval-V2 has no dates: its trajectories are read on a neutral clock, an hour apart, in haystack order.
V2_CLOCK_START = datetime(2026, 1, 1, 9)


def _v2_turns(trajectory: dict[str, Any]) -> list[Turn]:
    """A web-agent trajectory as one saved conversation: the person's task, then each step the agent
    took (where it was, what it thought, what it did, the page it saw), then the outcome."""
    turns = [Turn("user", f"Task: {trajectory.get('goal') or ''}\nStart page: {trajectory.get('start_url') or ''}")]
    for state in trajectory.get("states") or []:
        action = state.get("action")
        turns.append(Turn("assistant", "\n".join([
            f"Step {state.get('step', state.get('state_index'))} at {state.get('url') or ''}",
            f"Thought: {state.get('thought') or ''}",
            f"Action: {action if action is not None else '(initial page)'}",
            "Page (accessibility tree):",
            str(state.get("accessibility_tree") or ""),
        ])))
    turns.append(Turn("assistant", f"Outcome: {trajectory.get('outcome') or ''}"))
    return turns


def longmemeval_v2_tasks(root: Path, *, tier: str = "small", domains: Sequence[str] | None = None,
                         limit: int | None = None) -> list[MemoryTask]:
    """LongMemEval-V2 as tasks: one per distinct haystack (the small tier has one per domain, which all of
    that domain's questions share). Reads ``questions.jsonl``, ``haystacks/lme_v2_<tier>.json`` (or
    ``haystacks__lme_v2_<tier>.json``) and only the needed lines of ``trajectories.jsonl`` (1.2 GB), streamed."""
    root = Path(root)
    questions = [json.loads(line) for line in (root / "questions.jsonl").read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    haystack_file = root / "haystacks" / f"lme_v2_{tier}.json"
    haystacks = _read_json(haystack_file if haystack_file.is_file() else root / f"haystacks__lme_v2_{tier}.json")
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for item in questions:
        if domains and item["domain"] not in domains:
            continue
        groups.setdefault(tuple(haystacks[item["id"]]), []).append(item)
    chosen = list(groups.items())[:limit] if limit is not None else list(groups.items())
    needed = {trajectory_id for ids, _items in chosen for trajectory_id in ids}
    trajectories: dict[str, dict[str, Any]] = {}
    with (root / "trajectories.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r'\s*\{\s*"id"\s*:\s*"([^"]+)"', line)
            if match and match.group(1) not in needed:
                continue  # skipped unparsed: most of the 1.2 GB is never decoded
            if line.strip():
                record = json.loads(line)
                if record.get("id") in needed:
                    trajectories[record["id"]] = record
    tasks: list[MemoryTask] = []
    for index, (ids, items) in enumerate(chosen):
        sessions = [Session(trajectory_id, V2_CLOCK_START + timedelta(hours=position),
                            _v2_turns(trajectories[trajectory_id]))
                    for position, trajectory_id in enumerate(ids) if trajectory_id in trajectories]
        domain = str(items[0]["domain"])
        tasks.append(MemoryTask(
            task_id=f"lmev2_{tier}_{domain}" if tier == "small" else f"lmev2_{tier}_{index}",
            benchmark=LONGMEMEVAL_V2, sessions=sessions,
            ask_at=(sessions[-1].when if sessions else V2_CLOCK_START) + timedelta(hours=1),
            questions=[Question(question_id=str(item["id"]), question=str(item["question"]),
                                answer=str(item["answer"]), category=str(item["question_type"]),
                                abstention=str(item["question_type"]).endswith("-abs"),
                                eval_function=str(item["eval_function"]), image=str(item.get("image") or ""))
                       for item in items]))
    return tasks
