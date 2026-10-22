"""Finding things to improve (L3a): issues from the person's own work, a diagnosis each, a drafted change.

Once a night in quiet hours (after Dream, under the lease, before training; ``runner``) Learning:

1. **Groups issues by kind of work** (``kinds``) and what went wrong: the person corrected an answer
   (Teach, or the next message, ``corrections``), a step (Teach on a step), stopped a turn, retried it,
   turned down an action, or steps kept failing. Each issue keeps its evidence: which decisions, how
   many chats, corrections against failures. Only the person's own work is captured at all (D27);
   imported past chats (partial) count as evidence, never as hidden problems (O89).
2. **Leaves out what is held for testing** (E10): any turn holding a held-out decision
   (``LearningStore.held_out_ids``) and the redo after a held-out correction. This module never
   imports the audit vault (``sealing`` refuses it).
3. **Deduplicates against changes already open, used or turned down** (evolution's proposals and
   this archive's attempts): nothing turned down is proposed again without new evidence.
4. **Diagnoses each issue with one call** (``learning_diagnosis`` in the usage ledger, the default
   model at the lowest effort, E13), stopped at the monthly limit with Learning's own words. A default
   model on the person's Claude plan makes no call at all (O99): the night waits and says why.
5. **Drafts the change it picks**: a **lesson** or a **way of working** (evolution's ``lesson`` and
   ``behaviour`` proposals, E6), a **skill** to create or patch (the existing proposal types),
   **training** on these examples, **needs more examples**, or **wait**. It may also say the issue
   **looks like a bug in Row-Bot** (no code-fix draft here, O110). Drafts wait to be tested (L3b);
   nothing is used.

**E9 at draft time**: a draft's evidence must include the person's own feedback (a verifier result
counts too, once L3b has them); its text may not add recipients, links, credentials or permissions
(``evolution.injection_problems``); a draft that rests only on tool or web content is marked never
automatic. A refused draft is kept in the archive as a failed attempt with its reason.

Every diagnosis is an :class:`~row_bot.learning.contracts.Attempt` (the archive, D15) naming its issue.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from row_bot import learning as gate
from row_bot.learning import corrections, kinds
from row_bot.learning.calls import ClaudePlanRefused, ModelClient, NoModel, estimate_tokens, text_of
from row_bot.learning.contracts import Attempt, Cost, Decision, Episode, Ref, Signal, new_id
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import LearningStore

logger = logging.getLogger(__name__)

PURPOSE = "learning_diagnosis"
# How far back evidence is grouped, how many issues a night diagnoses, how much each shows the model.
LOOKBACK_DAYS = 30
MAX_ISSUES_PER_NIGHT = 6
EVIDENCE_SHOWN = 6
EXCERPT = 500
# An issue is diagnosed with at least this much of the person's own feedback (a Teach with a note
# alone is enough), or this many failed steps across two chats or more.
MIN_FEEDBACK = 2
MIN_FAILURES = 3
CLAUDE_PLAN_WORDS = ("Learning's improvement work needs a model other than your Claude plan. Choose another "
                     "default model to let Learning look for things to improve.")
NO_MODEL_WORDS = "Learning needs a default model to look for things to improve."

Facet = Literal["corrected", "wrong_step", "ignored", "stopped", "retried", "denied", "failed"]
FACETS: tuple[str, ...] = ("corrected", "wrong_step", "ignored", "stopped", "retried", "denied", "failed")
# The person's own feedback (E9); a failed step is tool output.
PERSON_KINDS = frozenset({"teach", "next_message_correction", "stop", "retry", "approval_denied"})
Change = Literal["lesson", "way_of_working", "create_skill", "patch_skill", "train", "needs_examples", "wait"]
CHANGES: tuple[str, ...] = ("lesson", "way_of_working", "create_skill", "patch_skill", "train", "needs_examples",
                            "wait")
DRAFTS = frozenset({"lesson", "way_of_working", "create_skill", "patch_skill"})
_CHANGE_KIND = {"lesson": "lesson", "way_of_working": "way_of_working", "create_skill": "skill",
                "patch_skill": "skill", "train": "training", "needs_examples": "wait", "wait": "wait"}
_PROPOSAL_TYPE = {"lesson": "lesson", "way_of_working": "behaviour", "create_skill": "create_skill",
                  "patch_skill": "patch_skill"}
NIGHT_KEY = "diagnosis.night"


# ── Evidence and issues ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Evidence:
    """One thing that went wrong: a signal from the person, or a failed step."""

    source: str  # a signal kind, or "failed"
    facet: str
    decision_id: str
    episode_id: str
    thread_id: str
    conversation_id: str
    at: float
    signal_id: str | None = None
    imported: bool = False
    untrusted: bool = False
    note: str = ""
    category: str | None = None

    @property
    def from_person(self) -> bool:
        return self.source in PERSON_KINDS


@dataclass
class Issue:
    """Things that went wrong in one kind of work, the same way."""

    kind: str
    facet: str
    evidence: list[Evidence] = field(default_factory=list)
    single: str | None = None  # the one signal it holds (the ablation's single-signal arm)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.facet}" + (f":{self.single}" if self.single else "")

    def counts(self) -> dict[str, int]:
        sources = Counter(item.source for item in self.evidence)
        return {
            "chats": len({item.conversation_id for item in self.evidence}),
            "corrections": sources["teach"] + sources["next_message_correction"],
            "taught": sources["teach"],
            "stops": sources["stop"],
            "retries": sources["retry"],
            "denials": sources["approval_denied"],
            "failures": sources["failed"],
            "imported": sum(1 for item in self.evidence if item.imported),
        }

    @property
    def feedback(self) -> int:
        return sum(1 for item in self.evidence if item.from_person)

    def enough(self) -> bool:
        if self.single:
            return True
        counts = self.counts()
        if any(item.source == "teach" and item.note for item in self.evidence):
            return True
        if self.feedback >= MIN_FEEDBACK:
            return True
        return counts["failures"] >= MIN_FAILURES and counts["chats"] >= 2

    def weight(self) -> float:
        counts = self.counts()
        return (3 * counts["corrections"] + 2 * (counts["stops"] + counts["retries"] + counts["denials"])
                + counts["failures"] + counts["chats"])

    def newest(self) -> float:
        return max((item.at for item in self.evidence), default=0.0)


def _facet_of(signal: Signal) -> str:
    if signal.kind == "teach":
        return {"wrong_tool_or_step": "wrong_step", "ignored_instructions": "ignored"}.get(str(signal.category or ""),
                                                                                            "corrected")
    return {"next_message_correction": "corrected", "stop": "stopped", "retry": "retried",
            "approval_denied": "denied"}.get(signal.kind, "corrected")


def _left_out(book: corrections.Book, held: set[str]) -> set[str]:
    """Episodes drafting may not look at (E10): any holding a held-out decision, and each turn that
    redid a held-out correction (its corrected action)."""

    out = {decision.episode_id for decision_id in held if (decision := book.decisions.get(decision_id))}
    for decision_id in held:
        out |= {episode.id for episode in book.followers.get(decision_id, ())}
    return out


def kind_of(book: corrections.Book, episode: Episode | None) -> str:
    """An episode's kind of work: its own record's, else what the person asked (``kinds``, no call)."""

    if episode is None:
        return kinds.OTHER
    if episode.kind_of_work:
        return kinds.from_label(episode.kind_of_work)
    return kinds.classify(kinds.Request(book.text_of(episode))).kind


def collect(store: LearningStore, *, now: float, book: corrections.Book | None = None,
            single: bool = False) -> tuple[list[Issue], dict[str, int]]:
    """The issues in the last :data:`LOOKBACK_DAYS` of the person's work, and what was left out (counts).

    ``single``: every piece of evidence is its own issue (the paper's single-signal arm)."""

    book = book or corrections.Book(store)
    held = store.held_out_ids()
    skipped_episodes = _left_out(book, held)
    since = now - LOOKBACK_DAYS * 86_400
    left: Counter[str] = Counter()
    found: list[Evidence] = []
    for signal in book.signals:
        if not since <= signal.created_at <= now or not signal.decision_id:
            continue
        decision = book.decisions.get(signal.decision_id)
        if decision is None:
            left["gone"] += 1
            continue
        if decision.id in held or decision.episode_id in skipped_episodes:
            left["held_out"] += 1
            continue
        episode = book.episodes.get(decision.episode_id)
        found.append(Evidence(
            signal.kind, _facet_of(signal), decision.id, decision.episode_id, decision.thread_id,
            decision.conversation_id or decision.thread_id, signal.created_at, signal.id,
            bool(episode is not None and episode.historical), decision.untrusted, str(signal.note or ""),
            signal.category,
        ))
    signalled = {item.decision_id for item in found}
    for decision in book.decisions.values():
        if decision.kind != "tool_call" or decision.status not in ("error", "timeout"):
            continue
        if not since <= decision.created_at <= now or decision.id in signalled or decision.id in held:
            continue
        if decision.episode_id in skipped_episodes:
            left["held_out"] += 1
            continue
        episode = book.episodes.get(decision.episode_id)
        found.append(Evidence("failed", "failed", decision.id, decision.episode_id, decision.thread_id,
                              decision.conversation_id or decision.thread_id, decision.created_at,
                              imported=bool(episode is not None and episode.historical), untrusted=decision.untrusted))
    groups: dict[tuple[str, ...], Issue] = {}
    for item in sorted(found, key=lambda value: (value.at, value.decision_id)):
        kind = kind_of(book, book.episodes.get(item.episode_id))
        if single:
            if item.source == "failed":
                continue
            key: tuple[str, ...] = (kind, item.facet, str(item.signal_id))
            groups[key] = Issue(kind, item.facet, [item], single=str(item.signal_id))
            continue
        key = (kind, item.facet)
        groups.setdefault(key, Issue(kind, item.facet)).evidence.append(item)
    return list(groups.values()), dict(left)


# ── What was tried before (dedup and rejection memory) ──────────────────────


def _proposals_by_issue() -> dict[str, list[dict[str, Any]]]:
    from row_bot import evolution

    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for proposal in evolution.list_proposals():
        learning = (proposal.get("payload") or {}).get("learning")
        if isinstance(learning, dict) and learning.get("issue"):
            found[str(learning["issue"])].append(proposal)
    return found


def _stamp(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def standing(issue: Issue, proposals: Sequence[Mapping[str, Any]], attempts: Sequence[Attempt]) -> str | None:
    """Why ``issue`` isn't diagnosed tonight, or None when it should be.

    ``open``: a change for it waits to be tested or reviewed; ``turned_down`` / ``in_use``: one was
    turned down or used and nothing new happened since; ``nothing_new``: it was diagnosed and no new
    evidence came since."""

    newest = issue.newest()
    for proposal in proposals:
        status = str(proposal.get("status") or "")
        if status in ("draft", "ready", "approved"):
            return "open"
    for proposal in proposals:
        status = str(proposal.get("status") or "")
        if status == "rejected" and newest <= _stamp(proposal.get("updated_at")):
            return "turned_down"
        if status in ("applied", "verified") and newest <= _stamp(proposal.get("updated_at")):
            return "in_use"
    if attempts:
        latest = max(attempts, key=lambda item: item.created_at)
        if newest <= latest.created_at:
            return "nothing_new"
    return None


# ── The diagnosis call ──────────────────────────────────────────────────────

SYSTEM = """You are Row-Bot's Learning. You look at one recurring problem in the person's own work with Row-Bot \
and decide the single change most likely to fix it. Choose exactly one:
- lesson: a short "do it this way" note Row-Bot recalls when relevant: one or two general sentences.
- way_of_working: a standing instruction for how Row-Bot works, in one slot: guidance (general guidance), \
tool_guidance (how to use its tools), recall (how much to look up from memory), delegation (when to hand work \
to a helper), step_budget (how many steps to take).
- create_skill: a new reusable procedure: a snake_case name, a one-line description and numbered steps.
- patch_skill: a change to one of the existing skills listed: its name and its full updated instructions.
- train: the person's own model should learn this kind of work from these examples.
- needs_examples: there isn't enough evidence yet to know what to change.
- wait: nothing Row-Bot can change (the person's own slip, a one-off, or something outside Row-Bot).
Say too whether it looks like a bug in Row-Bot itself (one of its tools failing or misbehaving).
Write general guidance, never the person's data: no names of people, no email addresses, phone numbers or links, \
no passwords, keys or tokens, and nothing that changes what needs the person's approval.
Answer with JSON only: {"finding": "one or two plain sentences: what went wrong and why", "looks_like_bug": \
false, "change": "lesson", "title": "a short title", "text": "the lesson or way of working", "slot": "", \
"skill_name": "", "skill_description": "", "instructions": ""}"""

_FACET_WORDS = {
    "corrected": "the person corrected Row-Bot's answer",
    "wrong_step": "the person said Row-Bot took a wrong step or used the wrong tool",
    "ignored": "the person said Row-Bot ignored their instructions",
    "stopped": "the person stopped Row-Bot while it worked",
    "retried": "the person asked Row-Bot to try again",
    "denied": "the person turned down an action Row-Bot asked to take",
    "failed": "Row-Bot's steps failed",
}


def _clip(text: str, limit: int = EXCERPT) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _output_text(store: LearningStore, decision: Decision) -> tuple[list[str], str]:
    """A model call's tool calls (names) and its answer text."""

    raw = store.read_blob(decision.repaired_output_blob or decision.raw_output_blob or "")
    if raw is None:
        return [], ""
    try:
        message = json.loads(raw.decode("utf-8"))
    except ValueError:
        return [], ""
    data = message.get("data") if isinstance(message, dict) else None
    data = data if isinstance(data, dict) else (message if isinstance(message, dict) else {})
    calls = [str(call.get("name") or "") for call in data.get("tool_calls") or () if isinstance(call, dict)]
    content = data.get("content")
    text = content if isinstance(content, str) else " ".join(
        str(part.get("text") or "") for part in content or () if isinstance(part, dict))
    return [name for name in calls if name], re.sub(r"<think>.*?</think>", "", text, flags=re.S)


def _tools_used(book: corrections.Book, episode_id: str) -> list[str]:
    names = [str(item.tool_name or "") for item in book.by_episode.get(episode_id, ()) if item.kind == "tool_call"]
    return [name for name in dict.fromkeys(names) if name]


def describe(store: LearningStore, book: corrections.Book, issue: Issue, *, now: float) -> str:
    """The issue as the diagnosis call sees it: its kind, counts and up to :data:`EVIDENCE_SHOWN` cases."""

    counts = issue.counts()
    lines = [f"Kind of work: {kinds.label(issue.kind)}.",
             f"What went wrong: {_FACET_WORDS.get(issue.facet, issue.facet)} "
             f"({len(issue.evidence)} times in {counts['chats']} chats).", ""]
    shown = sorted(issue.evidence, key=lambda item: (not item.from_person, -item.at))[:EVIDENCE_SHOWN]
    for number, item in enumerate(shown, 1):
        episode = book.episodes.get(item.episode_id)
        decision = book.decisions.get(item.decision_id)
        lines.append(f"{number}. Request: {_clip(book.text_of(episode)) if episode else '(not kept)'}")
        tools = _tools_used(book, item.episode_id)
        if tools:
            lines.append(f"   Row-Bot used: {', '.join(tools[:8])}")
        if decision is not None and decision.kind == "model_call":
            _calls, answer = _output_text(store, decision)
            if answer:
                lines.append(f"   Row-Bot answered: {_clip(answer)}")
        elif decision is not None:
            lines.append(f"   The step that went wrong: {decision.tool_name or 'a tool'} ({decision.status})")
        if item.source == "teach":
            what = item.category.replace("_", " ") if item.category else "something was wrong"
            lines.append(f"   The person taught Row-Bot ({what}): {_clip(item.note) or '(no note)'}")
        elif item.source == "next_message_correction":
            follower = book.next_episode(episode) if episode else None
            lines.append(f"   The person's next message: {_clip(book.text_of(follower)) if follower else '(not kept)'}")
        elif item.source == "stop":
            lines.append("   The person pressed Stop.")
        elif item.source == "retry":
            lines.append("   The person pressed Retry.")
        elif item.source == "approval_denied":
            lines.append(f"   The person turned down: {decision.tool_name if decision else 'an action'}")
        elif item.source == "failed":
            lines.append("   No feedback from the person: the step failed.")
        if decision is not None and item.source in ("teach", "next_message_correction", "retry"):
            redo = corrections.accepted_redo(store, decision, clock=lambda: now, book=book)
            first = corrections.first_call(book, redo) if redo is not None else None
            if first is not None:
                calls, answer = _output_text(store, first)
                done = f"called {', '.join(calls)}" if calls else f"answered: {_clip(answer, 300)}"
                lines.append(f"   After that Row-Bot {done} (the person accepted it).")
        lines.append("")
    return "\n".join(lines).strip()


def _context() -> str:
    """The skills Row-Bot has (names and descriptions) and the lessons it already holds (titles)."""

    lines: list[str] = []
    try:
        from row_bot import skills as skill_module

        if not skill_module.skills_loaded():
            skill_module.load_skills()
        found = [item for item in skill_module.get_all_skills() if not skill_module.is_tool_guide(item)][:20]
        if found:
            lines.append("Existing skills:")
            lines += [f"- {item.name}: {_clip(getattr(item, 'description', ''), 160)}" for item in found]
    except Exception:
        logger.debug("Learning couldn't list the skills for a diagnosis", exc_info=True)
    try:
        from row_bot import knowledge_graph

        rows = knowledge_graph.list_entity_summaries("self_knowledge", limit=20, description_chars=0)
        titles = [_clip(str(row.get("subject") or ""), 120) for row in rows if row.get("subject")]
        if titles:
            lines.append("Lessons Row-Bot already holds:")
            lines += [f"- {title}" for title in titles]
    except Exception:
        logger.debug("Learning couldn't list the lessons for a diagnosis", exc_info=True)
    return "\n".join(lines)


@dataclass(frozen=True)
class Diagnosis:
    """What the model picked, read strictly."""

    finding: str
    change: str
    looks_like_bug: bool = False
    title: str = ""
    text: str = ""
    slot: str = ""
    skill_name: str = ""
    skill_description: str = ""
    instructions: str = ""


def parse(reply: str) -> Diagnosis | None:
    """The diagnosis call's JSON answer, or None when it isn't one we can use."""

    text = str(reply or "").strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or str(data.get("change") or "") not in CHANGES:
        return None

    def field_text(name: str, limit: int) -> str:
        return str(data.get(name) or "").strip()[:limit]

    return Diagnosis(field_text("finding", 600), str(data["change"]), bool(data.get("looks_like_bug")),
                     field_text("title", 120), field_text("text", 1200), field_text("slot", 32),
                     field_text("skill_name", 64), field_text("skill_description", 200),
                     field_text("instructions", 4000))


# ── Drafting ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outcome:
    """What one issue came to tonight."""

    issue: str
    kind: str
    change: str  # one of CHANGES, or "refused" (E9) / "unreadable"
    attempt_id: str | None = None
    proposal_id: str | None = None
    refused: tuple[str, ...] = ()
    never_automatic: bool = False
    looks_like_bug: bool = False
    tokens: int = 0


def draft_text(found: Diagnosis) -> str:
    """Everything of a draft that would reach the model's context."""

    return "\n".join(part for part in (found.title, found.text, found.skill_name, found.skill_description,
                                       found.instructions) if part)


def e9_problems(issue: Issue, found: Diagnosis) -> list[str]:
    """Why E9 refuses this draft (empty: it may be drafted). ``needs_your_feedback``: its evidence holds no
    feedback from the person; the others are what its text would add."""

    from row_bot import evolution

    problems: list[str] = []
    if not issue.feedback:
        problems.append("needs_your_feedback")
    problems += evolution.injection_problems(draft_text(found))
    if found.change == "way_of_working" and found.slot not in evolution.BEHAVIOUR_SLOTS:
        problems.append("unknown_slot")
    return problems


def _why(issue: Issue) -> dict[str, int]:
    counts = issue.counts()
    return {key: counts[key] for key in ("chats", "corrections", "stops", "retries", "denials", "failures",
                                         "imported")}


def _propose(issue: Issue, found: Diagnosis, attempt_id: str, *, never_automatic: bool) -> dict[str, Any]:
    from row_bot import evolution

    learning = {"issue": issue.key, "attempt_id": attempt_id, "kind_of_work": issue.kind, "state": "waiting_to_test",
                "never_automatic": never_automatic, "why": _why(issue), "facet": issue.facet}
    proposal_type = _PROPOSAL_TYPE[found.change]
    title = found.title or found.skill_name or kinds.label(issue.kind)
    if found.change in ("lesson", "way_of_working"):
        payload: dict[str, Any] = {"text": found.text, "kind_of_work": issue.kind, "learning": learning}
        if found.change == "way_of_working":
            payload["slot"] = found.slot
    elif found.change == "create_skill":
        payload = {"name": evolution.normalize_skill_name(found.skill_name or title),
                   "description": found.skill_description, "instructions": found.instructions,
                   "learning": learning}
    else:
        payload = {"target_skill": found.skill_name, "updated_instructions": found.instructions,
                   "learning": learning}
    return evolution.create_proposal(insight_ids=[], proposal_type=proposal_type, title=title,
                                     rationale=found.finding, risk="low", confidence=0.5, payload=payload,
                                     verification_plan="Learning tests it on work it hasn't seen (L3b).",
                                     status="draft", dedupe=True)


def _refs(issue: Issue) -> tuple[Ref, ...]:
    refs: list[Ref] = []
    for item in issue.evidence[:500]:
        if item.signal_id:
            refs.append(Ref("signal", item.signal_id))
        refs.append(Ref("decision", item.decision_id))
    return tuple(refs[:1024])


def _complete(found: Diagnosis) -> bool:
    if found.change in ("lesson", "way_of_working"):
        return bool(found.text)
    return bool(found.skill_name and found.instructions)


def record(store: LearningStore, issue: Issue, found: Diagnosis | None, *, now: float, tokens: int,
           strategy_release_id: str | None = None) -> Outcome:
    """Draft (or refuse) the change the diagnosis picked, and keep the attempt in the archive."""

    attempt_id = new_id("att")
    threads = tuple(sorted({item.conversation_id for item in issue.evidence}))[:50_000]
    base = Attempt(id=attempt_id, change_kind="wait", hypothesis="", created_at=now, kind_of_work=issue.kind,
                   evidence=_refs(issue), cost=Cost(tokens=max(0, int(tokens))), source_thread_ids=threads,
                   issue=issue.key, strategy_release_id=strategy_release_id)
    if found is None:
        store.put(replace(base, hypothesis="The diagnosis couldn't be read.", outcome="failed",
                          reason="unreadable", finished_at=now))
        return Outcome(issue.key, issue.kind, "unreadable", attempt_id, tokens=tokens)
    reason = found.change + (" · looks like a bug in Row-Bot" if found.looks_like_bug else "")
    attempt = replace(base, change_kind=_CHANGE_KIND[found.change],  # type: ignore[arg-type]
                      hypothesis=found.finding or reason, reason=reason)
    if found.change in DRAFTS and not _complete(found):
        store.put(replace(attempt, outcome="failed", reason=f"{found.change}: incomplete", finished_at=now))
        return Outcome(issue.key, issue.kind, "unreadable", attempt_id, tokens=tokens)
    if found.change not in DRAFTS:
        store.put(attempt)
        return Outcome(issue.key, issue.kind, found.change, attempt_id, looks_like_bug=found.looks_like_bug,
                       tokens=tokens)
    problems = e9_problems(issue, found)
    if problems:
        store.put(replace(attempt, outcome="failed", reason=("e9:" + ",".join(problems))[:2000], finished_at=now))
        return Outcome(issue.key, issue.kind, "refused", attempt_id, refused=tuple(problems),
                       looks_like_bug=found.looks_like_bug, tokens=tokens)
    never_automatic = all(item.untrusted for item in issue.evidence)
    store.put(attempt)
    proposal = _propose(issue, found, attempt_id, never_automatic=never_automatic)
    return Outcome(issue.key, issue.kind, found.change, attempt_id, str(proposal.get("id") or ""),
                   never_automatic=never_automatic, looks_like_bug=found.looks_like_bug, tokens=tokens)


# ── A night ─────────────────────────────────────────────────────────────────


@dataclass
class NightReport:
    """One night's look, in counts (the Overview's Overnight line reads it)."""

    night: str
    # done · nothing · claude_plan · no_model · limit · unavailable · failed · off; ``stopped`` (the person came
    # back or Learning was paused) is the only one that goes on later the same night.
    state: str = "done"
    found: int = 0  # issues with enough evidence that were new or had new evidence
    diagnosed: int = 0
    drafted: int = 0
    refused: int = 0
    needs_examples: int = 0
    train: int = 0
    waits: int = 0
    bugs: int = 0
    unreadable: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    left_out: dict[str, int] = field(default_factory=dict)
    tokens: int = 0
    note: str = ""
    at: float = 0.0
    outcomes: list[Outcome] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        found = {key: getattr(self, key) for key in ("night", "state", "found", "diagnosed", "drafted", "refused",
                                                     "needs_examples", "train", "waits", "bugs", "unreadable",
                                                     "tokens", "note", "at")}
        found["skipped"] = dict(self.skipped)
        found["left_out"] = dict(self.left_out)
        found["drafts"] = Counter(item.change for item in self.outcomes if item.proposal_id)
        found["refusals"] = Counter(code for item in self.outcomes for code in item.refused)
        return found


def last_night(store: LearningStore) -> dict[str, Any] | None:
    """The last night's report (counts only), or None before the first."""

    if not store.exists():
        return None
    try:
        found = json.loads(store.meta_value(NIGHT_KEY, "") or "null")
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


_SUMMED = ("diagnosed", "drafted", "refused", "needs_examples", "train", "waits", "bugs", "unreadable", "tokens")


def _save(store: LearningStore, report: NightReport) -> None:
    found = report.to_dict()
    earlier = last_night(store)
    if earlier and earlier.get("night") == report.night and earlier.get("state") == "stopped":
        # The same night going on after the person came back: its counts add up.
        # Issues diagnosed before the stop aren't found again (they're open or have nothing new).
        found["found"] = int(found.get("found") or 0) + int(earlier.get("diagnosed") or 0)
        for key in _SUMMED:
            found[key] = int(found.get(key) or 0) + int(earlier.get(key) or 0)
        for key in ("drafts", "refusals"):
            found[key] = dict(Counter(earlier.get(key) or {}) + Counter(found.get(key) or {}))
    store.set_meta_values({NIGHT_KEY: json.dumps(found, sort_keys=True)})


def note_night(store: LearningStore, night: str, state: str, note: str = "") -> None:
    """Record that tonight's look ended without drafting anything (``failed``: it couldn't start)."""

    _save(store, NightReport(night, state=state, note=note, at=store.now()))


def _messages(issue_text: str, context: str) -> list[Any]:
    from langchain_core.messages import HumanMessage, SystemMessage

    body = issue_text + (("\n\n" + context) if context else "")
    return [SystemMessage(content=SYSTEM), HumanMessage(content=body)]


def run(store: LearningStore, *, client: ModelClient, night: str, now: float | None = None,
        meter: TokenMeter | None = None, should_stop: Callable[[], bool] = lambda: False,
        limit: int = MAX_ISSUES_PER_NIGHT, single: bool = False, save: bool = True,
        context: str | None = None, check_model: Callable[[], None] | None = None,
        strategy_release_id: str | None = None) -> NightReport:
    """One night's look: issues → (dedup) → one diagnosis call each → a drafted change or a note.

    ``should_stop`` is asked before every call (the person came back, Learning was paused): the night
    stops there and goes on later. ``check_model`` raises :class:`ClaudePlanRefused` or :class:`NoModel`
    before any call when the default model can't be used. ``single`` drafts from single signals (the
    paper's ablation arm, never the product's)."""

    moment = float(now if now is not None else store.now())
    report = NightReport(night, at=moment)
    if not store.exists() or not gate.is_enabled(store.root):
        report.state = "off"
        return report
    forget_gone(store)
    corrections.confirm(store, clock=lambda: moment)
    book = corrections.Book(store)
    issues, report.left_out = collect(store, now=moment, book=book, single=single)
    proposals = _proposals_by_issue()
    attempts: dict[str, list[Attempt]] = defaultdict(list)
    for item in store.find("attempt", limit=100_000):
        if isinstance(item, Attempt) and item.issue:
            attempts[item.issue].append(item)
    skipped: Counter[str] = Counter()
    due: list[Issue] = []
    for issue in issues:
        if not issue.enough():
            skipped["needs_more"] += 1
            continue
        why = standing(issue, proposals.get(issue.key, ()), attempts.get(issue.key, ()))
        if why is not None:
            skipped[why] += 1
            continue
        due.append(issue)
    due.sort(key=lambda item: (-(item.feedback > 0), -item.weight(), -item.newest(), item.key))
    report.found = len(due)
    report.skipped = dict(skipped)
    if not due:
        report.state = "nothing"
        if save:
            _save(store, report)
        return report
    try:
        if check_model is not None:
            check_model()
    except ClaudePlanRefused:
        report.state, report.note = "claude_plan", CLAUDE_PLAN_WORDS
    except NoModel:
        report.state, report.note = "no_model", NO_MODEL_WORDS
    if report.state in ("claude_plan", "no_model"):
        if save:
            _save(store, report)
        return report
    meter = meter or TokenMeter.for_month()
    extra = _context() if context is None else context
    for issue in due[: max(0, int(limit))]:
        if should_stop():
            report.state = "stopped"
            break
        messages = _messages(describe(store, book, issue, now=moment), extra)
        estimate = estimate_tokens(messages)
        local = client.is_local(None)
        if not meter.allows(estimate, local=local):
            report.state, report.note = "limit", meter.note()
            break
        try:
            reply = client.invoke(messages, tools=None, model_ref=None, purpose=PURPOSE)
        except ClaudePlanRefused:
            report.state, report.note = "claude_plan", CLAUDE_PLAN_WORDS
            break
        except NoModel:
            report.state, report.note = "no_model", NO_MODEL_WORDS
            break
        except Exception as error:  # noqa: BLE001 - a failed call ends tonight's look, said plainly
            logger.warning("Learning's diagnosis call failed (%s)", type(error).__name__)
            report.state, report.note = "unavailable", "The model wasn't available. Learning looks again tomorrow night."
            break
        meter.charge(reply.tokens, local=reply.local)
        report.tokens += reply.tokens
        outcome = record(store, issue, parse(text_of(reply.message)), now=moment, tokens=reply.tokens,
                         strategy_release_id=strategy_release_id)
        report.outcomes.append(outcome)
        report.diagnosed += 1
        report.drafted += bool(outcome.proposal_id)
        report.refused += outcome.change == "refused"
        report.needs_examples += outcome.change == "needs_examples"
        report.train += outcome.change == "train"
        report.waits += outcome.change == "wait"
        report.bugs += outcome.looks_like_bug
        report.unreadable += outcome.change == "unreadable"
    if save:
        _save(store, report)
    return report


def _gone_attempts(store: LearningStore) -> set[str]:
    from row_bot.learning.store import MARK_CHAT_DELETED, MARK_CHAT_EXCLUDED

    if not store.exists():
        return set()
    return {mark.id for mark in store.marks("attempt") if mark.mark in (MARK_CHAT_DELETED, MARK_CHAT_EXCLUDED)}


def forget_gone(store: LearningStore) -> int:
    """Remove drafts whose source chat was deleted or switched off since (their text came from it)."""

    gone = _gone_attempts(store)
    if not gone:
        return 0
    from row_bot import evolution

    return evolution.remove_learning_drafts(gone)


def drafts(store: LearningStore | None = None) -> list[dict[str, Any]]:
    """Learning's drafted changes (evolution proposals with a Learning block), newest first, with the
    attempt each came from; drafts whose source chats were deleted or switched off are left out."""

    from row_bot import evolution

    saved = store if store is not None else LearningStore()
    gone = _gone_attempts(saved)
    found = []
    for proposal in evolution.list_proposals():
        learning = (proposal.get("payload") or {}).get("learning")
        if not isinstance(learning, dict) or learning.get("attempt_id") in gone:
            continue
        found.append(proposal)
    return found


def check_default_model() -> None:
    """Raise :class:`ClaudePlanRefused` when the default model is the person's Claude plan (O99) and
    :class:`NoModel` when none is chosen, before anything is sent."""

    from row_bot.learning.calls import NEVER_CALLED, provider_of

    _ref, provider = provider_of(None, PURPOSE)
    if provider in NEVER_CALLED:
        raise ClaudePlanRefused()


__all__ = [
    "CHANGES",
    "CLAUDE_PLAN_WORDS",
    "Diagnosis",
    "Evidence",
    "FACETS",
    "Issue",
    "MAX_ISSUES_PER_NIGHT",
    "NightReport",
    "Outcome",
    "PURPOSE",
    "check_default_model",
    "collect",
    "describe",
    "drafts",
    "e9_problems",
    "forget_gone",
    "kind_of",
    "last_night",
    "note_night",
    "parse",
    "record",
    "run",
    "standing",
]
