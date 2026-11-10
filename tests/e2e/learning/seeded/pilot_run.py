"""The stale-skills pilot's run of one arm, on S1's harness and the released Row-Bot (Lane B, MEMORY-SKILLS §4).

One process per arm (Row-Bot binds its data folder when imported), in its own scratch profile under
``D:\\RowBotScratch\\learning\\pilot\\``:

- ``--arm main``: the main model on the ChatGPT lane (the eval profile's sign-in, in this process's memory
  only; **a token refresh is refused**, so the eval profile is only ever read);
- ``--arm local``: ``model:ollama:qwen3.5:9b`` as the default model, so chat turns, memory extraction and
  Dream all run on it; the simulated person's words still come from the ChatGPT lane (both arms the same).

What runs is S1's :class:`~tests.e2e.learning.seeded.live.PersonRun` (the person's profile, their 30-day past,
the simulated person, the simulated clock, the nights' Dream) with the pilot's own pieces:

- the live days are :mod:`pilot_timeline`'s: the two announcements and the two recurring tasks;
- each morning the world's own change lands (the rota policy rewritten; Sandgate's letter is in the apps);
- each evening Row-Bot's **background memory extraction** runs once (the app runs it when idle), and the past is
  extracted once after it is written, as the app would have done over those 30 days;
- each run records what its first attempt did (the emails and events it made), which skill it loaded, what
  auto-recall put in the turn and what memory tools returned; :mod:`pilot_scorer` scores it;
- **the skill**: on the task's ``consent_run``-th passed run, the person consents to Row-Bot's offer to save it
  as a skill, or (no offer) asks Row-Bot to save it; Row-Bot drafts and applies it through its own approval
  gate (the person approves). If no skill naming the fact exists after that, the pilot seeds
  :data:`pilot_timeline.SEEDED_SKILLS` that day (and disables a skill of Row-Bot's that doesn't name it).
- Learning stays on (the preview's capture), but its nightly training is not run: the pilot is about skills
  and memory, and the card and the cap are kept for the arms.

``pilot.json`` (in ``--run-dir``) holds the scored runs, the skills, tokens and GPU time: synthetic data only.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from tests.e2e.learning.seeded import live, pilot_scorer, pilot_timeline
from tests.e2e.learning.seeded.live import EXIT_TOKEN_CAP, PersonRun, _thread_id
from tests.e2e.learning.seeded.scratch import ScratchError, check_scratch

logger = logging.getLogger("row_bot.seeded.pilot")

LOCAL_MODEL = "qwen3.5:9b"
PRODUCT_COMMIT = "c2ad4a2ba"
STAGES = ("profile", "past", "days")
#: Row-Bot's real calendar and mail tools, which the fake apps stand in for (see PilotRun._tools).
STAND_INS = ("calendar", "gmail", "outlook_calendar", "outlook_mail")
#: The person's answers around the skill (fixed words: they carry no fact).
CONSENT_YES = "Yes please, save it as a skill."
CONSENT_ASK = ("I'll be asking you this a lot - can you save how you just did it as a skill, so it's done the "
               "same way every time?")
CONSENT_APPLY = "Yes, go ahead and save it."
_OFFER = re.compile(r"\bskill\b", re.IGNORECASE)
_ICONS = {"bread_order": "🍞", "weekend_shift": "📅"}


class RefreshRefused(RuntimeError):
    """The pilot never refreshes the eval profile's sign-in (it reads it, nothing more)."""


def _guard_sign_in() -> None:
    """Refuse any ChatGPT token refresh and any write-back to the eval profile, in this process."""

    from row_bot.providers import codex

    def refused(*_args: Any, **_kwargs: Any) -> Any:
        raise RefreshRefused("the pilot never refreshes the eval profile's sign-in")

    codex.refresh_codex_token = refused

    def no_write_back(self: Any, token_set: Any) -> None:
        self.write_backs.append("refused: the pilot never writes to the eval profile")
        raise RefreshRefused("the pilot never writes to the eval profile")

    live.SignIn._write_back = no_write_back  # noqa: SLF001 - harness only


class _Asked:
    """What :meth:`PersonRun.turn` needs of a request for its approvals."""

    approvals: tuple[str, ...] = ()


class PilotRun(PersonRun):
    """One arm of the pilot (see the module's docstring)."""

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)
        self.arm = args.arm
        self.timeline = pilot_timeline.build(seed=args.seed, days=args.days)
        self.history = replace(self.timeline.history, stream=self.timeline.requests)
        # Row-Bot's own settings tools (default-on in the app) carry row_bot_create_skill, row_bot_patch_skill
        # and row_bot_apply_proposal: without them Row-Bot can neither save nor patch a skill.
        self.person["tools"] = [*self.person["tools"], "row_bot_status"]
        self.gpu.label = f"LB pilot stale skills ({self.arm} arm)"
        self._recalled: list[list[str]] = []
        for key in ("pilot_runs", "tells", "evenings"):
            self.state.data.setdefault(key, [])
        self.state.data.setdefault("skills", {})
        self.state.data.setdefault("mornings", [])

    # ── Set-up ──────────────────────────────────────────────────────────

    def boot(self) -> None:
        _guard_sign_in()
        super().boot()
        codex_ref = self.main_ref
        if self.arm == "local":
            from row_bot import models

            self.main_ref = f"model:ollama:{LOCAL_MODEL}"
            models.adopt_saved_default(self.main_ref)
        self.sim.model_ref = codex_ref  # the person's words: the same lane in both arms
        self.state.data["arm"] = {"arm": self.arm, "main_ref": self.main_ref, "person_words": codex_ref,
                                  "product": PRODUCT_COMMIT, "timeline": self.timeline.fingerprint()}
        self._record_recall()
        self.save()

    def _tools(self) -> None:
        """S1's tools, with the real calendar and mail tools taken out of this process's registry: the fake
        apps stand in for them. Left in, they are off, and with ``row_bot_status`` bound the turn context lists
        them as off, so the 9B said the calendar was off and didn't use the fake one (probe, 25 Oct 10:19)."""

        from row_bot.tools import registry

        for name in STAND_INS:
            registry._tools.pop(name, None)  # noqa: SLF001 - harness only, this process only
        super()._tools()

    def _record_recall(self) -> None:
        """Auto-recall's memories for each turn, as the agent selected them (read only)."""

        from row_bot import agent

        original = agent._turn_recall  # noqa: SLF001 - observed, not changed

        def recording(messages: list, max_tokens: int) -> tuple[list[dict], Any]:
            memories, block = original(messages, max_tokens)
            self._recalled.append([_memory_text(item) for item in memories or ()])
            return memories, block

        agent._turn_recall = recording  # noqa: SLF001

    # ── Stage: past (S1's, then the app's background extraction over it) ──

    def stage_past(self) -> None:
        if not self.state.data.get("past"):
            super().stage_past()
        if not (self.state.data.get("past") or {}).get("extraction"):
            self._book_card("memory extraction over the 30-day past", "40 min, 9 GB")
            started = time.monotonic()
            saved = self._extract()
            self._card_time("past extraction", time.monotonic() - started)
            self.state.data["past"]["extraction"] = saved
            self.save()
            self.gpu.release()
            self.note(f"past extracted: {saved}")

    def _extract(self) -> dict[str, Any]:
        from row_bot import memory_extraction

        before = self.cloud_tokens()
        started = time.monotonic()
        saved = memory_extraction.run_extraction()
        return {"saved": int(saved or 0), "seconds": round(time.monotonic() - started, 1),
                "cloud_tokens": self.cloud_tokens() - before, "memories": _memory_count()}

    def _book_card(self, what: str, expect: str) -> None:
        if self.arm != "local":
            return
        self._wait_for_person()
        self.gpu.acquire(what, expect=expect)

    def _card_time(self, phase: str, seconds: float) -> None:
        if self.arm == "local":
            self._gpu_time(phase, seconds)

    # ── Stage: days ─────────────────────────────────────────────────────

    def stage_days(self) -> None:
        by_day = self.timeline.by_day()
        done = {item["id"] for item in (*self.state.data["pilot_runs"], *self.state.data["tells"])}
        for day in range(1, int(self.args.days) + 1):
            night = self.state.data["nights"].get(str(day)) or {}
            if night.get("done"):
                continue
            self._book_card(f"local 9B arm, simulated day {day}", "20 min, 12 GB")
            started = time.monotonic()
            self._morning(day)
            for item in by_day.get(day, []):
                if item.id in done:
                    continue
                self.clock.request_time(day, item.order)
                if isinstance(item, pilot_timeline.Tell):
                    record = self.tell(item)
                    self.state.data["tells"].append(record)
                else:
                    record = self.run_request(item)
                    self.state.data["pilot_runs"].append(record)
                    self.note(f"day {day} {pilot_scorer.task_of(item.id)}: {record['score']['verdict']}"
                              f" (first {'ok' if record['passed_first'] else 'wrong'}, skill "
                              f"{'loaded' if record['skill_loaded'] else 'not loaded'}, recall new "
                              f"{record['recalled_new']}; {record['tokens']:,} tokens)")
                self.save()
            if not any(item.get("day") == day for item in self.state.data["evenings"]):
                self._clock_at_least(day, 21)
                evening = {"day": day, **self._extract()}
                self.state.data["evenings"].append(evening)
                self.save()
            self.night(day)  # S1's: Dream, the vault's refresh (Learning's runner is off: --no-gpu)
            self._card_time(f"simulated day {day}", time.monotonic() - started)
            self.save()
            self.gpu.release()
            self.check_cap()

    def _morning(self, day: int) -> None:
        if day in self.state.data["mornings"]:
            return
        for path, text in self.timeline.files_changed_on(day).items():
            target = self.workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        self.state.data["mornings"].append(day)
        self.save()

    # ── A turn and what it did ──────────────────────────────────────────

    def _observed_turn(self, thread_id: str, text: str, request: Any) -> tuple[Any, dict[str, Any]]:
        from row_bot import threads

        before_messages = len(_messages(threads, thread_id))
        before_apps = self.apps.state()
        self._recalled = []
        result = self.turn(thread_id, text, request)
        messages = _messages(threads, thread_id)[before_messages:]
        calls, results = [], []
        for message in messages:
            for call in getattr(message, "tool_calls", None) or ():
                calls.append({"name": str(call.get("name") or ""), "args": call.get("args") or {}})
            if getattr(message, "type", "") == "tool":
                results.append({"name": str(getattr(message, "name", "") or ""),
                                "content": _content_text(getattr(message, "content", ""))})
        seen = {
            "actions": pilot_scorer.actions_between(before_apps, self.apps.state()),
            "tools": [call["name"] for call in calls],
            "calls": calls,
            "skill_loads": [_skill_ref(call) for call in calls if _invoked(call) == "skill_load"],
            "memory_results": [item["content"][:4000] for item in results if "memory" in item["name"].lower()
                               or "recall" in item["name"].lower() or _bridged_memory(item, calls)],
            "recalled": [text for batch in self._recalled for text in batch],
            "answer": result.answer[:2000],
        }
        return result, seen

    def tell(self, item: pilot_timeline.Tell) -> dict[str, Any]:
        """The person's announcement, in a chat of its own (no check: it is news, not a task)."""

        from row_bot import threads

        self.check_cap()
        thread_id = _thread_id(self.person_id, f"tell-{item.id}-{uuid.uuid4().hex[:4]}")
        threads.create_thread(item.prompt[:48], thread_id=thread_id, seed_default_skills=False,
                              approval_mode=str(self.person.get("approval_mode") or ""))
        tokens = self.cloud_tokens()
        self.clock.used()
        result, seen = self._observed_turn(thread_id, item.prompt, _Asked())
        self.clock.advance(minutes=2)
        saved_memory = [call for call in seen["calls"] if _invoked(call) in ("save_memory", "memory_save",
                                                                             "remember", "update_memory")]
        record = {"id": item.id, "day": item.day, "fact": item.fact, "thread": thread_id,
                  "tools": seen["tools"], "saved_memory": len(saved_memory),
                  "saved_new_value": any(pilot_timeline.mentions(item.fact,
                                                                 self.timeline.change(item.fact).new,
                                                                 json.dumps(call.get("args"), ensure_ascii=False))
                                         for call in seen["calls"]),
                  "error": result.error, "seconds": round(result.seconds, 1), "answer": seen["answer"][:600],
                  "tokens": self.cloud_tokens() - tokens}
        self.note(f"day {item.day} told about {item.fact}: tools {seen['tools']}")
        return record

    def run_request(self, request: Any) -> dict[str, Any]:
        """One recurring request: S1's ask (the turn, the check, the person's reaction), with what each
        attempt did, then the skill's consent on its day."""

        from row_bot import threads
        from row_bot.learning import teach
        from row_bot.learning.packs.verify import Outcome, verify, workspace_files

        self.check_cap()
        self._place(request)
        task = pilot_scorer.task_of(request.id)
        thread_id = _thread_id(self.person_id, f"live-{request.id}-{uuid.uuid4().hex[:4]}")
        threads.create_thread(request.prompt[:48], thread_id=thread_id, seed_default_skills=False,
                              approval_mode=str(self.person.get("approval_mode") or ""))
        tokens_before = self.cloud_tokens()
        skill = self._skill_state(task)
        record: dict[str, Any] = {"id": request.id, "day": request.day, "task": task, "thread": thread_id,
                                  "attempts": [], "reaction": [], "skill": skill, "build": self.build}
        text = request.prompt
        for attempt in range(1, 4):
            self.clock.used()
            result, seen = self._observed_turn(thread_id, text, request)
            outcome = Outcome(result.answer, workspace_files(self.workspace), self.apps.state())
            verdict = verify(request.task, outcome, backend="auto")
            record["attempts"].append({"passed": verdict.passed, "tool_calls": result.tool_calls,
                                       "seconds": round(result.seconds, 1), "stopped": result.stopped,
                                       "error": result.error, "approvals": result.approvals, **seen})
            if verdict.passed:
                break
            reaction = self.sim.react(request, result.answer, verdict.detail or "the outcome was wrong", attempt,
                                      stopped=result.stopped)
            if result.stopped and reaction.action == "teach":
                reaction = type(reaction)("correct", reaction.text, None, reaction.source)
            record["reaction"].append({"action": reaction.action, "source": reaction.source,
                                       "category": reaction.category, "text": reaction.text[:600]})
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
        first = record["attempts"][0]
        record["passed_first"] = bool(first["passed"])
        record["passed_final"] = bool(record["attempts"][-1]["passed"])
        scored = pilot_scorer.score(self.timeline, request.id, request.day, first["actions"])
        record["score"] = scored.to_dict()
        record["after_change"] = scored.after_change
        record["verdict"] = scored.verdict
        texts = [*first["recalled"], *first["memory_results"]]
        record["recalled_new"] = pilot_scorer.surfaced(self.timeline, task, request.day, texts)
        record["recalled_old"] = pilot_scorer.surfaced(self.timeline, task, request.day, texts, which="old")
        record["auto_recall_new"] = pilot_scorer.surfaced(self.timeline, task, request.day, first["recalled"])
        record["skill_loaded"] = bool(skill.get("name")) and any(
            skill["name"] in ref for ref in first["skill_loads"])
        record["read_world_source"] = _read_world_source(task, first["calls"])
        self.state.data["apps"] = self.apps.state()
        self.clock.advance(minutes=2)
        self._consent(request, task, thread_id, record)
        record["tokens"] = self.cloud_tokens() - tokens_before
        return record

    # ── The skill ───────────────────────────────────────────────────────

    def _skill_state(self, task: str) -> dict[str, Any]:
        """The task's skill as it is now: which, how it was made, and whether it names the old or new value."""

        from row_bot import skills

        entry = dict(self.state.data["skills"].get(task) or {})
        if not entry.get("name"):
            return {}
        found = skills.get_skill(entry["name"])
        text = _skill_text(found)
        fact = pilot_timeline.TASK_FACT[task]
        change = self.timeline.change(fact)
        entry.update({"exists": found is not None, "enabled": bool(found and skills.is_enabled(found.name)),
                      "names_old": pilot_timeline.mentions(fact, change.old, text),
                      "names_new": pilot_timeline.mentions(fact, change.new, text),
                      "sha": _sha(text)})
        return entry

    def _user_skills(self) -> dict[str, str]:
        from row_bot import skills

        skills.load_skills()
        return {item.name: _skill_text(item) for item in skills.get_manual_skills() if item.source == "user"}

    def _consent(self, request: Any, task: str, thread_id: str, record: dict[str, Any]) -> None:
        """On the task's consent run, the person says yes to Row-Bot's offer (or asks for the skill); if that
        leaves no skill naming the fact, the pilot seeds one that day (see the module's docstring)."""

        if self.state.data["skills"].get(task, {}).get("name") or not record["passed_final"]:
            return
        passed = sum(1 for item in self.state.data["pilot_runs"] if item["task"] == task and item["passed_final"])
        if passed + 1 < self.timeline.consent_run[task]:
            return
        answer = record["attempts"][-1]["answer"]
        offered = bool(_OFFER.search(answer))
        before = self._user_skills()
        proposals_before = {item["id"] for item in _proposals()}
        consent: dict[str, Any] = {"day": request.day, "run": request.id, "offered": offered, "turns": []}
        message = CONSENT_YES if offered else CONSENT_ASK
        made: dict[str, str] = {}
        for _ in range(3):
            self.clock.used()
            result, seen = self._observed_turn(thread_id, message, _Asked())
            consent["turns"].append({"said": message, "tools": seen["tools"], "calls": seen["calls"],
                                     "approvals": result.approvals, "error": result.error,
                                     "answer": seen["answer"][:600]})
            made = {name: text for name, text in self._user_skills().items() if name not in before}
            self.clock.advance(minutes=2)
            if made:
                break
            message = CONSENT_APPLY
        fact = pilot_timeline.TASK_FACT[task]
        now = self.timeline.value(fact, request.day)
        naming = {name: text for name, text in made.items() if pilot_timeline.mentions(fact, now, text)}
        consent["proposals"] = _proposals(since=proposals_before)
        consent["made"] = sorted(made)
        consent["made_text"] = {name: text[:3000] for name, text in made.items()}
        if naming:
            name = sorted(naming)[0]
            self.state.data["skills"][task] = {"name": name, "source": "offer" if offered else "asked",
                                               "day": request.day, "consent": consent}
            self.note(f"skill {name!r} made by Row-Bot ({'offer' if offered else 'asked'}), day {request.day}")
            return
        from row_bot import skills

        for name in made:  # Row-Bot's skill didn't name the fact: off, so only one skill does the task
            try:
                skills.set_enabled(name, False)
            except Exception:  # noqa: BLE001 - recorded below
                logger.debug("couldn't disable %s", name, exc_info=True)
        spec = pilot_timeline.seeded_skill(task, request.day, self.timeline)
        skills.create_skill(name=spec["name"], display_name=spec["display_name"], icon=_ICONS[task],
                            description=spec["description"], instructions=spec["instructions"],
                            tags=["kitchen", "pilot"], enabled=True)
        why = "Row-Bot made no skill" if not made else "Row-Bot's skill didn't name the fact"
        self.state.data["skills"][task] = {"name": spec["name"], "source": "seeded", "day": request.day,
                                           "why": why, "disabled": sorted(made), "consent": consent}
        self.note(f"skill {spec['name']!r} seeded on day {request.day} ({why})")

    # ── Result ──────────────────────────────────────────────────────────

    def pilot_result(self) -> dict[str, Any]:
        data = self.state.data
        runs = [{**item["score"], "recalled_new": item["recalled_new"], "skill_loaded": item["skill_loaded"]}
                for item in data["pilot_runs"]]
        return {
            "format": "row_bot.learning.pilot_stale_skills_arm", "version": 1, "arm": data.get("arm"),
            "person": self.person_id, "seed": self.args.seed, "days": self.args.days,
            "timeline": self.timeline.fingerprint(), "changes": [item.to_dict() for item in self.timeline.changes],
            "consent_run": self.timeline.consent_run, "builds": data.get("builds"),
            "rates": pilot_scorer.rates(runs), "skills": data["skills"], "tells": data["tells"],
            "runs": [{key: item.get(key) for key in ("id", "day", "task", "verdict", "after_change", "passed_first",
                                                     "passed_final", "recalled_new", "recalled_old",
                                                     "auto_recall_new", "skill_loaded", "read_world_source",
                                                     "tokens")}
                     | {"skill": {key: (item.get("skill") or {}).get(key)
                                  for key in ("name", "source", "names_old", "names_new", "enabled")},
                        "actions": item["attempts"][0]["actions"], "tools": item["attempts"][0]["tools"],
                        "reactions": [reaction["action"] for reaction in item["reaction"]]}
                     for item in data["pilot_runs"]],
            "past": data.get("past"), "evenings": data["evenings"],
            "tokens": {"ledger": self.ledger_tokens(), "sim_person": self.sim.tokens.to_dict(),
                       "cloud_total": self.cloud_tokens()},
            "gpu": data["gpu"], "refused_tool_calls": data.get("refused_tool_calls", 0),
            "write_backs": list(self.sign_in.write_backs) if self.sign_in else [],
            "nights": {day: {step: (value or {}).get("outcome") for step, value in (night.get("steps") or {}).items()}
                       for day, night in data["nights"].items()},
        }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _messages(threads: Any, thread_id: str) -> list:
    try:
        return list(threads.get_latest_checkpoint_messages(thread_id))
    except Exception:  # noqa: BLE001 - a new thread has none
        return []


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(block.get("text", "")) if isinstance(block, dict) else str(block) for block in content)
    return str(content)


def _invoked(call: dict[str, Any]) -> str:
    """The tool a call ran: itself, or the tool ``tool_invoke`` ran for it (the small-window bridge)."""

    if call.get("name") == "tool_invoke":
        args = call.get("args") or {}
        return str(args.get("name") or args.get("tool_name") or args.get("tool") or "")
    return str(call.get("name") or "")


def _skill_ref(call: dict[str, Any]) -> str:
    args = call.get("args") or {}
    return str(args.get("name") or args.get("skill_id") or args.get("skill") or json.dumps(args))


def _bridged_memory(item: dict[str, Any], calls: list[dict[str, Any]]) -> bool:
    return item["name"] == "tool_invoke" and any("memory" in _invoked(call) for call in calls)


def _read_world_source(task: str, calls: list[dict[str, Any]]) -> bool:
    """Whether the attempt looked where the world itself says the fact (the inbox; the rota policy)."""

    for call in calls:
        name = _invoked(call)
        args = json.dumps(call.get("args") or {}, ensure_ascii=False).lower()
        if task == "bread_order" and name.startswith("mail_") and name != "mail_send":
            return True
        if task == "weekend_shift" and "rota-policy" in args:
            return True
    return False


def _memory_text(memory: Any) -> str:
    if isinstance(memory, dict):
        return " | ".join(str(value) for key, value in memory.items() if isinstance(value, (str, int, float)))
    return str(memory)


def _memory_count() -> int:
    try:
        from row_bot import knowledge_graph

        return int(knowledge_graph.count_entities())
    except Exception:  # noqa: BLE001 - a count for the record only
        return -1


def _proposals(since: set[str] | None = None) -> list[dict[str, Any]]:
    """Row-Bot's self-improvement proposals (id, type, status, skill name); with ``since``, only newer ones."""

    from row_bot import evolution

    found = []
    for item in evolution.list_proposals():
        if since is not None and item.get("id") in since:
            continue
        found.append({"id": item.get("id"), "type": item.get("proposal_type"), "status": item.get("status"),
                      "skill": (item.get("payload") or {}).get("name") or (item.get("payload") or {}).get(
                          "target_skill")})
    return found


def _skill_text(skill: Any) -> str:
    if skill is None:
        return ""
    try:
        return (Path(skill.path) / "SKILL.md").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return str(getattr(skill, "instructions", "") or "")


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One arm of the stale-skills pilot (Lane B)")
    parser.add_argument("--arm", choices=("main", "local"), required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--days", type=int, default=pilot_timeline.DAYS)
    parser.add_argument("--seed", type=int, default=pilot_timeline.SEED)
    parser.add_argument("--eval-profile", required=True)
    parser.add_argument("--cap-tokens", type=int, required=True)
    parser.add_argument("--spent-before", type=int, default=0)
    parser.add_argument("--gpu-booking", default="")
    parser.add_argument("--stages", default=",".join(STAGES))
    args = parser.parse_args(argv)
    # S1's PersonRun reads these; the pilot runs no Learning training, no crash or comparison checks.
    args.person, args.tier, args.lane, args.crash, args.gpu, args.pilot_compare = "mara", "8gb", "lane-B", False, False, True
    args.runtimes_from = ""
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    data = check_scratch(Path(args.data), Path(args.eval_profile))
    if "pilot" not in data.as_posix().lower():
        raise ScratchError(f"{data}: the pilot's profiles live under a pilot_ folder")
    run = PilotRun(args)
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
        code = EXIT_TOKEN_CAP
    finally:
        run.gpu.release()
        (run.run_dir / "pilot.json").write_text(json.dumps(run.pilot_result(), indent=1, sort_keys=True, default=str),
                                                encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
