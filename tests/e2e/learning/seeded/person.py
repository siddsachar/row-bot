"""The simulated person: a seeded person using Row-Bot, played by GPT-6.1 Sol where words are needed (S1).

What the person *does* is fixed and seeded, so a rerun with the same seed makes the same choices:

- the first message of a request is the request's own words (the generator's);
- when the outcome is wrong (the product's own verifier says so), the person either **corrects it in
  their next message** or **uses Teach**, by a seeded draw per request at the person's ``teach_share``;
  they correct at most twice, then give up;
- an **approval** is approved when it acts only on what the request named (a move of the named file),
  and denied otherwise;
- a turn that runs away (more tool calls or seconds than the person's patience) is **stopped** with
  the app's own Stop.

Only the person's **words** come from the model: a correction in their own voice, or the note they
type in Teach. The model is told who the person is, what they wanted (the request's hidden intent) and
what went wrong; never the checker's details. If the model can't be reached the words come from a
fixed template, so a run never stalls on it. Its calls go through Row-Bot's own ChatGPT transport but
**not** through the person's usage ledger: they are counted here, apart from everything Row-Bot and
Learning spend (``tokens``).
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_CORRECTIONS = 2
_REPLY_LIMIT = 1_500
_TEACH_CATEGORIES = {"answer": "wrong_answer", "json_answer": "wrong_answer", "file": "wrong_tool_or_step",
                     "app_state": "wrong_tool_or_step", "code_tests": "wrong_answer"}
_GUARDED = frozenset({"workspace_move_file", "workspace_file_delete", "delete_memory", "task_delete",
                      "tracker_delete", "delete_calendar_event", "move_calendar_event"})


@dataclass(frozen=True)
class Reaction:
    """What the person does after a wrong outcome: ``correct`` (next message), ``teach`` or ``give_up``."""

    action: str
    text: str = ""
    category: str | None = None
    source: str = "model"  # model | template


@dataclass
class SimTokens:
    """The simulated person's own model use (never in the person's ledger)."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failures: int = 0
    seconds: float = 0.0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "total": self.total, "failures": self.failures, "seconds": round(self.seconds, 1)}


def draw(seed: int, *parts: object) -> float:
    """A fixed number in [0, 1) for these parts (the same every run)."""

    digest = hashlib.sha256("\x00".join(str(part) for part in (seed, *parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def approval_decision(item: dict[str, Any], allowed: tuple[str, ...]) -> bool:
    """Approve only an action on what the request named; a guarded action on anything else is denied."""

    tool = str(item.get("tool") or "")
    args = item.get("args") if isinstance(item.get("args"), dict) else {}
    if tool not in _GUARDED:
        return True  # the person approves what they asked for in plain terms (nothing destructive)
    mentioned = {str(value).replace("\\", "/").strip("./") for key, value in args.items()
                 if key in ("source_path", "file_path", "path")}
    wanted = {path.replace("\\", "/").strip("./") for path in allowed}
    return bool(mentioned) and mentioned <= wanted


@dataclass
class SimulatedPerson:
    """One seeded person's behaviour (see the module's docstring)."""

    person: dict[str, Any]
    seed: int
    model_ref: str | None = None
    tokens: SimTokens = field(default_factory=SimTokens)
    _llm: Any = None

    @property
    def policy(self) -> dict[str, Any]:
        return self.person.get("policy") or {}

    # ── Fixed rules ─────────────────────────────────────────────────────

    def stop_now(self, tool_calls: int, seconds: float) -> bool:
        return (tool_calls > int(self.policy.get("patience_tool_calls", 16))
                or seconds > float(self.policy.get("patience_seconds", 300)))

    def choose(self, request_id: str, attempt: int) -> str:
        """``teach`` or ``correct`` for a wrong outcome (seeded); ``give_up`` after the last correction."""

        if attempt > MAX_CORRECTIONS:
            return "give_up"
        if attempt > 1:
            return "correct"  # once they're correcting, they keep correcting
        return "teach" if draw(self.seed, self.person["id"], request_id, "teach") < float(
            self.policy.get("teach_share", 0.3)) else "correct"

    @staticmethod
    def approve(item: dict[str, Any], allowed: tuple[str, ...]) -> bool:
        return approval_decision(item, allowed)

    # ── Words ───────────────────────────────────────────────────────────

    def react(self, request: Any, reply: str, problem: str, attempt: int, *, stopped: bool = False) -> Reaction:
        """What the person does after a wrong outcome of ``request`` (``problem``: what was wrong)."""

        action = self.choose(request.id, attempt)
        if action == "give_up":
            return Reaction("give_up", source="rule")
        category = _TEACH_CATEGORIES.get(request.task.verifier.kind, "other") if not stopped else "ignored_instructions"
        prompt = self._prompt(request, reply, problem, action, stopped=stopped)
        text = self._ask(prompt)
        if text:
            return Reaction(action, text[:600], category if action == "teach" else None, "model")
        fallback = (f"That's not what I needed. {request.intent}" if action == "correct"
                    else f"Wrong: {problem}. I wanted: {request.intent}")
        return Reaction(action, fallback, category if action == "teach" else None, "template")

    def _prompt(self, request: Any, reply: str, problem: str, action: str, *, stopped: bool) -> str:
        person = self.person
        what = ("Write your next chat message to Row-Bot, correcting it." if action == "correct"
                else "Write the short note you type into Row-Bot's Teach box about this reply (what was wrong "
                     "and what you wanted).")
        happened = "You stopped it because it was taking too long." if stopped else f"What was wrong: {problem}."
        return (
            f"You are role-playing {person['name']}. {person['about']} {person.get('uses', '')}\n"
            f"How you write: {person.get('asks', '')} How you correct: {person.get('corrects', '')}\n\n"
            f"You asked your assistant, Row-Bot: {request.prompt}\n"
            f"What you actually wanted: {request.intent}\n"
            f"Row-Bot's reply (shortened): {reply[:_REPLY_LIMIT] or '(no reply)'}\n"
            f"{happened}\n\n"
            f"{what} One to three sentences, in your own voice. Say what you need, don't explain how to check it, "
            f"and don't mention tests, checkers or role-play. Reply with the message only."
        )

    def _model(self) -> Any:
        if self._llm is not None:
            return self._llm
        from row_bot import models
        from row_bot.providers import background
        from row_bot.providers.reasoning import canonical_reasoning_model_ref
        from row_bot.providers.resolution import resolve_provider_config
        from row_bot.providers.usage_ledger import UsageLedgerCallback

        ref = self.model_ref or str(models.get_current_model() or "")
        resolved = resolve_provider_config(ref, allow_legacy_local=True)
        canonical = canonical_reasoning_model_ref(resolved.provider_id, resolved.runtime_model)
        llm = models.get_llm_for(resolved.selection_ref, reasoning_plan=background.background_reasoning(canonical).plan)
        kept = [handler for handler in (getattr(llm, "callbacks", None) or [])
                if not isinstance(handler, UsageLedgerCallback)]
        try:
            self._llm = llm.model_copy(update={"callbacks": kept})
        except Exception:  # noqa: BLE001 - not a pydantic model: a shallow copy
            import copy

            self._llm = copy.copy(llm)
            self._llm.callbacks = kept
        return self._llm

    def _ask(self, prompt: str) -> str:
        from langchain_core.messages import HumanMessage

        started = time.monotonic()
        for attempt in range(2):
            try:
                message = self._model().invoke([HumanMessage(content=prompt)])
            except Exception as error:  # noqa: BLE001 - the template stands in
                self.tokens.failures += 1
                logger.info("The simulated person's model call failed (%s)", type(error).__name__)
                if attempt:
                    break
                continue
            usage = getattr(message, "usage_metadata", None) or {}
            self.tokens.calls += 1
            self.tokens.input_tokens += int(usage.get("input_tokens") or len(prompt) // 4)
            self.tokens.output_tokens += int(usage.get("output_tokens") or 0)
            self.tokens.seconds += time.monotonic() - started
            content = message.content if isinstance(message.content, str) else " ".join(
                str(block.get("text", "")) for block in message.content if isinstance(block, dict))
            text = " ".join(content.split()).strip().strip('"')
            if text:
                return text
        self.tokens.seconds += time.monotonic() - started
        return ""


def rng_for(seed: int, *parts: object) -> random.Random:
    return random.Random(int(draw(seed, *parts) * 2**53))


__all__ = ["MAX_CORRECTIONS", "Reaction", "SimTokens", "SimulatedPerson", "approval_decision", "draw", "rng_for"]
