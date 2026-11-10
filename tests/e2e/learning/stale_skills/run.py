"""The stale-skills run of one arm for one person (Design A, Lane B): the product with its stale-skills setting.

One process per run (Row-Bot binds its data folder when imported), in its own scratch profile (``sa_`` folders under
``D:\\RowBotScratch\\stale-skills\\``), on S1's :class:`~tests.e2e.learning.seeded.live.PersonRun` (the profile, the
30-day past through Row-Bot's own import, the simulated person, the simulated clock, the nights' Dream and vault).

**The arm** (``--arm``, paired across arms: the same person, seed and changes) is the product's ``stale_skills.json``
setting, written into the profile before anything else (so the past's memories have their validity records too):

- ``N``: on with arm N: validity, links and the operation log only; no prompt text, no Dream phase (today's
  behaviour);
- ``R``: reconciliation: the Dream Cycle drafts ``patch_skill`` proposals overnight; **the person approves every one
  the next morning** (the policy), through the product's own apply;
- ``D``: the date-aware note where a stale skill is shown;
- ``F``: failure-triggered repair: after a run whose first attempt used the procedure's skill and failed its check,
  the product drafts a repair from that run (the request, the answer, the person's correction); **the person approves
  it at once** (the policy);
- ``A``: a second, independent copy of R (its own profile), for noise.

**The model** (``--model``):

- ``main``: the main model on the ChatGPT lane (the eval profile's sign-in, in this process's memory only; **a token
  refresh and any write-back are refused**);
- ``local``: ``model:ollama:<--local-model>`` as the default model, so chat turns, memory extraction, Dream and the
  arms' drafts run on it; the simulated person's words still come from the ChatGPT lane. Each simulated day books
  the card.

Each simulated day, in order (:func:`steps`): the morning (the world's files and inbox change on a change's day; the
person edits a skill on a newer-skill event's day; arm R's approvals; in the smoke world, the seeded skills on day
1), the day's items (what the person tells, the recurring
runs, the probes on probe days), the evening's background memory extraction, and the night (S1's: Dream, the vault's
refresh). **Every step is saved** in ``state.json`` as it ends, and a run started again continues at the first step
not done.

**The skill** is made through the simulated person's consent on the procedure's 2nd or 3rd passed run: they accept
Row-Bot's offer, or ask for one; Row-Bot drafts it and its approval card asks the person, who approves (the skill-save
fixes #559 and #560 made this work; phase 1's harness stand-in for the lost card is gone). If Row-Bot saves no skill,
the template skill (:func:`seeded_skill`) is seeded that day, and counted.

**A newer-skill event** (H-A3): on its morning the person edits the procedure's skill directly to the new value
(:func:`world.edit_skill_text`, through the product's own ``update_skill``; recorded with the cause ``person_edit``);
memory is never told. A procedure with no skill by then gets the template skill first (seeded, and counted).

The run stops cleanly at the token cap and at once on any usage or rate-limit error (exit 76).
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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tests.e2e.learning.seeded import live
from tests.e2e.learning.seeded.live import EXIT_TOKEN_CAP, PersonRun, _thread_id
from tests.e2e.learning.seeded.pilot_run import (_bridged_memory, _content_text, _invoked, _memory_count, _memory_text,
                                                 _messages, _proposals, _sha, _skill_ref, _skill_text)
from tests.e2e.learning.seeded.scratch import ScratchError, check_scratch
from tests.e2e.learning.stale_skills import phrasing as phrasing_module
from tests.e2e.learning.stale_skills import scorer, world

logger = logging.getLogger("row_bot.seeded.stale_skills")

EXIT_LIMIT = 76
#: The latest request slot of a day (S1's clock: 08:30 plus 95 minutes a slot, so 22:45).
MAX_ORDER = 9
STAGES = ("profile", "past", "days")
STAND_INS = ("calendar", "gmail", "outlook_calendar", "outlook_mail")
#: Tool calls that act on the world for each procedure's action (the acting step's tool).
ACTING_TOOLS = {"mail": "mail_send", "event": "calendar_create_event"}
#: The study's arms, and the product's setting each one runs (A is a second copy of R).
ARMS = {"N": "N", "R": "R", "D": "D", "F": "F", "A": "R"}
CONSENT_YES = "Yes please, save it as a skill."
CONSENT_ASK = ("I'll be asking you this a lot - can you save how you just did it as a skill, so it's done the same "
               "way every time?")
CONSENT_APPLY = "Yes, go ahead and save it."
_OFFER = re.compile(r"\bskill\b", re.IGNORECASE)
_LIMIT_WORDS = ("rate limit", "rate_limit", "ratelimit", "usage limit", "usage_limit", "429", "too many requests",
                "quota", "rate_limited", "usage_limit_reached")
#: No new card booking from here until the person's quiet hours end (a day takes a few minutes: never run into 00:40).
NO_NEW_DAY_FROM = (0, 25)


class RefreshRefused(RuntimeError):
    """The run never refreshes the eval profile's sign-in (it reads it, nothing more)."""


class LimitReached(RuntimeError):
    """A usage or rate-limit error: the run stops at once."""


def guard_sign_in() -> None:
    """Refuse any ChatGPT token refresh and any write-back to the eval profile, in this process."""

    from row_bot.providers import codex

    def refused(*_args: Any, **_kwargs: Any) -> Any:
        raise RefreshRefused("the stale-skills run never refreshes the eval profile's sign-in")

    codex.refresh_codex_token = refused

    def no_write_back(self: Any, token_set: Any) -> None:
        self.write_backs.append("refused: the run never writes to the eval profile")
        raise RefreshRefused("the stale-skills run never writes to the eval profile")

    live.SignIn._write_back = no_write_back  # noqa: SLF001 - harness only


def limit_error(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(word in lowered for word in _LIMIT_WORDS)


def quiet_soon(now: datetime | None = None) -> str | None:
    """No card booking from 00:25 to 05:00: a simulated day takes minutes and must end before 00:40."""

    current = now or datetime.now()
    minute = current.hour * 60 + current.minute
    if NO_NEW_DAY_FROM[0] * 60 + NO_NEW_DAY_FROM[1] <= minute < 5 * 60:
        return "the person's quiet hours (no new simulated day from 00:25; none 00:40-05:00)"
    return None


def simulated_datetime(now: Any) -> type[datetime]:
    """A ``datetime`` class whose ``now()`` is ``now()`` (the simulated clock), for the turn context."""

    class SimulatedNow(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # noqa: D102
            moment = now()
            return moment.astimezone(tz) if tz is not None else moment

    return SimulatedNow


def write_setting(data: Path, arm: str) -> dict[str, Any]:
    """The product's stale-skills setting for this study arm, in the profile."""

    setting = {"enabled": True, "arm": ARMS[arm]}
    data.mkdir(parents=True, exist_ok=True)
    (data / "stale_skills.json").write_text(json.dumps(setting), encoding="utf-8")
    return setting


# ── Steps and resuming (pure) ────────────────────────────────────────────────


def steps(timeline: world.Timeline, days: int) -> list[tuple[str, int, str]]:
    """Every step of the run in order: ``(what, day, id)`` with ``what`` one of morning, tell, run, probe, evening,
    night."""

    found: list[tuple[str, int, str]] = []
    by_day = timeline.by_day()
    for day in range(1, days + 1):
        found.append(("morning", day, f"morning-{day}"))
        for item in by_day.get(day, []):
            what = "tell" if isinstance(item, world.Tell) else "probe" if isinstance(item, world.Probe) else "run"
            found.append((what, day, item.id))
        found.append(("evening", day, f"evening-{day}"))
        found.append(("night", day, f"night-{day}"))
    return found


def done_steps(state: dict[str, Any]) -> set[str]:
    """The ids of the steps ``state`` (``state.json``) holds as finished."""

    done = {f"morning-{day}" for day in state.get("mornings") or ()}
    for key in ("runs", "tells", "probes"):
        done |= {item["id"] for item in state.get(key) or ()}
    done |= {f"evening-{item['day']}" for item in state.get("evenings") or ()}
    done |= {f"night-{day}" for day, night in (state.get("nights") or {}).items() if night.get("done")}
    return done


def remaining(timeline: world.Timeline, state: dict[str, Any], days: int) -> list[tuple[str, int, str]]:
    """The steps still to do, in order (a crash resumes at the first)."""

    done = done_steps(state)
    return [step for step in steps(timeline, days) if step[2] not in done]


# ── The seeded skill ─────────────────────────────────────────────────────────


def seeded_skill(procedure: world.Procedure, value: world.Value, first_name: str = "Mara") -> dict[str, str]:
    """The template skill for ``procedure`` with the fact's value on the day it is seeded."""

    said = world.say(procedure.kind, value)
    if procedure.action == "event":
        step = (f"2. Create one event with `calendar_create_event`: title `{procedure.event.replace('{who}', '<name>')}`,"
                f" the date from {first_name}'s message, **start {value['start']}, end {value['end']}** "
                f"({procedure.fact}).")
        take = f"1. Take the date (and the person's name, if any) from {first_name}'s message."
    elif procedure.kind in ("address", "supplier", "role"):
        step = (f"2. Send one email with `mail_send` to {value.get('name', procedure.party)}, **{said}** "
                f"({procedure.fact}), with the details from {first_name}'s message.")
        take = f"1. Take the {procedure.unit or 'details'} from {first_name}'s message."
    else:
        step = (f"2. Send one email with `mail_send` to {procedure.to} that states {procedure.fact}: **{said}**.")
        take = f"1. Take the week (if any) from {first_name}'s message."
    name = f"{procedure.id}_procedure"
    return {
        "name": name, "display_name": procedure.title[4:].capitalize() if procedure.title.startswith("the ")
        else procedure.title.capitalize(),
        "description": f"How {first_name}'s {procedure.title.removeprefix('the ')} is done.",
        "instructions": (f"Use this when {first_name} asks for {procedure.title}.\n\n## Steps\n{take}\n{step}\n"
                         f"3. Tell {first_name} it's done.\n"),
    }


def reads_before(procedure: world.Procedure, calls: list[dict[str, Any]]) -> list[str]:
    """The tools a first attempt called before its acting step (all of them when it never acted)."""

    acting = ACTING_TOOLS[procedure.action]
    found = []
    for call in calls:
        if call["name"] == acting:
            break
        found.append(call["name"])
    return found


def repair_correction(record: dict[str, Any], procedure: world.Procedure) -> str:
    """What the person said was wrong with the run (arm F's evidence): their first reaction's words, or, when they
    gave up without words, that the result was wrong (never the right value)."""

    for reaction in record.get("reaction") or ():
        if reaction.get("text"):
            return str(reaction["text"])
    return f"That wasn't right ({procedure.title})."


# ── The run ──────────────────────────────────────────────────────────────────


class _Asked:
    approvals: tuple[str, ...] = ()


class StaleRun(PersonRun):
    """One arm's run for one person (see the module's docstring)."""

    def __init__(self, args: argparse.Namespace) -> None:
        world.use_people_dir(args.person)
        super().__init__(args)
        self.lane = args.lane_model  # "main" or "local"
        self.study_arm = args.arm
        phrased = None if args.phrasing == "templates" else phrasing_module.load(args.person, args.phrasing)
        if args.phrasing != "templates" and phrased is None:
            raise ScratchError(f"no phrasing file for {args.person}/{args.phrasing}: run phrasing.py first, or "
                               "--phrasing templates")
        self.timeline = world.build(person=args.person, seed=args.seed, days=args.days, phrasing=phrased,
                                    smoke=args.smoke)
        self.history = replace(self.timeline.history, stream=self.timeline.runs)
        self.person["tools"] = [*self.person["tools"], "row_bot_status"]
        self.gpu.label = f"SA2 stale skills ({self.study_arm}, {self.lane})"
        self.gpu.quiet = quiet_soon
        self._recalled: list[list[str]] = []
        self._errors: list[str] = []
        data = self.state.data
        for key in ("runs", "tells", "probes", "evenings", "mornings", "redone", "unprompted_skills", "approvals",
                    "repairs", "edits"):
            data.setdefault(key, [])
        data.setdefault("skills", {})
        data.setdefault("started", [])

    # ── Set-up ──────────────────────────────────────────────────────────

    def boot(self) -> None:
        guard_sign_in()
        setting = write_setting(self.data, self.study_arm)
        super().boot()
        from row_bot import stale_skills

        stale_skills.invalidate()
        stale_skills.set_clock(lambda: self.clock.now().replace(tzinfo=None))
        codex_ref = self.main_ref
        if self.lane == "local":
            from row_bot import models

            self.main_ref = f"model:ollama:{self.args.local_model}"
            models.adopt_saved_default(self.main_ref)
        self.sim.model_ref = codex_ref  # the person's words: the same lane in both arms
        if self.args.sim_date:
            self._show_simulated_date()
        self._watch_limits()
        self._record_recall()
        arm = {"arm": self.study_arm, "setting": setting, "lane": self.lane, "main_ref": self.main_ref,
               "person_words": codex_ref, "build": self.build, "simulated_date": bool(self.args.sim_date),
               "smoke": bool(self.args.smoke), "timeline": self.timeline.fingerprint(),
               "phrasing": self.timeline.phrasing, "seed": self.args.seed, "person": self.person_id}
        prompts = {item.id: item.prompt for item in self.timeline.runs}
        if any(prompts.get(item["id"]) != item.get("prompt") for item in self.state.data["runs"]):
            raise ScratchError("this run's state was made from another timeline: start a new run folder")
        saved = self.state.data.get("arm") or {}
        if saved and (saved.get("arm"), saved.get("lane")) != (self.study_arm, self.lane):
            raise ScratchError("this run folder belongs to another arm or model: start a new run folder")
        self.state.data["arm"] = arm
        self.save()

    def _tools(self) -> None:
        """S1's tools, with Row-Bot's real calendar and mail tools taken out of this process's registry: the fake
        apps stand in for them (the pilot's finding, 25 Oct)."""

        from row_bot.tools import registry

        for name in STAND_INS:
            registry._tools.pop(name, None)  # noqa: SLF001 - harness only, this process only
        super()._tools()

    def _record_recall(self) -> None:
        from row_bot import agent

        original = agent._turn_recall  # noqa: SLF001 - observed, not changed

        def recording(messages: list, max_tokens: int) -> tuple[list[dict], Any]:
            memories, block = original(messages, max_tokens)
            self._recalled.append([_memory_text(item) for item in memories or ()])
            return memories, block

        agent._turn_recall = recording  # noqa: SLF001

    def _show_simulated_date(self) -> None:
        """The turn context's date and time from the simulated clock (Row-Bot otherwise says the real date: the
        first main run's memories said "from 26 October" on simulated 22 November). Only the turn context's clock
        is moved; nothing else in Row-Bot reads it."""

        from row_bot import agent

        agent._datetime = simulated_datetime(self.clock.now)  # noqa: SLF001 - harness only, this process only

    def _watch_limits(self) -> None:
        """Keep each turn's error payloads (to stop on a usage or rate-limit error), and the simulated person's."""

        from row_bot import agent

        for name in ("stream_agent", "resume_stream_agent"):
            original = getattr(agent, name)

            def watched(*args: Any, __original: Any = original, **kwargs: Any) -> Any:
                for event in __original(*args, **kwargs):
                    if event and event[0] in ("error", "terminal") and len(event) > 1:
                        self._errors.append(json.dumps(event[1], default=str)[:400])
                    yield event

            setattr(agent, name, watched)
        person = self.sim
        model = person._model  # noqa: SLF001

        def guarded() -> Any:
            inner = model()

            class Guarded:
                def invoke(self_inner, messages: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: N805
                    try:
                        return inner.invoke(messages, *args, **kwargs)
                    except Exception as error:
                        if limit_error(str(error)):
                            self._errors.append(f"simulated person: {str(error)[:300]}")
                        raise

            return Guarded()

        person._model = guarded  # noqa: SLF001

    def _check_limits(self) -> None:
        found = [text for text in self._errors if limit_error(text)]
        if found:
            self.state.data.setdefault("limit_errors", []).extend(found)
            self.save()
            self.note(f"usage or rate-limit error: {found[0][:200]}; stopping")
            raise SystemExit(EXIT_LIMIT)
        self._errors.clear()

    # ── Stage: past ─────────────────────────────────────────────────────

    def stage_past(self) -> None:
        if not self.state.data.get("past"):
            super().stage_past()
        if not (self.state.data.get("past") or {}).get("extraction"):
            self._book("memory extraction over the 30-day past", "10 min, 9 GB")
            started = time.monotonic()
            saved = self._extract()
            self._card("past extraction", time.monotonic() - started)
            self.state.data["past"]["extraction"] = saved
            self.save()
            self.gpu.release()

    def _extract(self) -> dict[str, Any]:
        from row_bot import memory_extraction

        before = self.cloud_tokens()
        started = time.monotonic()
        saved = memory_extraction.run_extraction()
        self._check_limits()
        return {"saved": int(saved or 0), "seconds": round(time.monotonic() - started, 1),
                "cloud_tokens": self.cloud_tokens() - before, "memories": _memory_count()}

    def _book(self, what: str, expect: str) -> None:
        if self.lane != "local":
            return
        self._wait_for_person()
        from tests.e2e.learning.seeded.scratch import after_quiet_hours

        if after_quiet_hours():
            # From 05:00 to 08:00 the gate waits while the card holds memory no lane booked (the person's Row-Bot may
            # still compare). Our own model, kept loaded by Ollama after the last day, counts as that: unload it
            # first, so the gate reads what others hold (found 05:02 26 Oct: the run waited on its own 9B).
            self._unload_local_model()
        self.gpu.acquire(what, expect=expect)

    def _unload_local_model(self) -> None:
        import httpx

        try:
            httpx.post("http://127.0.0.1:11434/api/generate", timeout=60,
                       json={"model": self.args.local_model, "keep_alive": 0})
        except httpx.HTTPError:
            logger.debug("couldn't unload %s", self.args.local_model, exc_info=True)

    def _card(self, phase: str, seconds: float) -> None:
        if self.lane == "local":
            self._gpu_time(phase, seconds)

    # ── Stage: days ─────────────────────────────────────────────────────

    def stage_days(self) -> None:
        items = {item.id: item for item in (*self.timeline.tells, *self.timeline.runs, *self.timeline.probes)}
        started_day: int | None = None
        clock_started = time.monotonic()
        self.state.data["started"].append({"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                           "next": (remaining(self.timeline, self.state.data, self.args.days) or
                                                    [("done", 0, "")])[0][2]})
        for interrupted in self.state.data.pop("in_progress", []) or []:
            self.state.data["redone"].append(interrupted)
        for what, day, step_id in remaining(self.timeline, self.state.data, self.args.days):
            if started_day != day:
                if started_day is not None:
                    self.gpu.release()
                self._book(f"{self.args.local_model} arm {self.study_arm}, simulated day {day}", "6 min, 12 GB")
                started_day, clock_started = day, time.monotonic()
            if what == "morning":
                self._morning(day)
            elif what in ("tell", "run", "probe"):
                item = items[step_id]
                self.clock.request_time(day, min(item.order, MAX_ORDER))  # probes come last, by 22:45
                self.state.data["in_progress"] = [step_id]
                self.save()
                record = getattr(self, f"_{what}")(item)
                self.state.data[f"{what}s"].append(record)
                self.state.data.pop("in_progress", None)
                self.save()
            elif what == "evening":
                self._clock_at_least(day, 21)
                self.state.data["evenings"].append({"day": day, **self._extract()})
                self.save()
            else:
                self.night(day)  # S1's: Dream (arm R's reconciliation is its phase), the vault's refresh
                self._check_limits()
                self._card(f"simulated day {day}", time.monotonic() - clock_started)
                self.save()
                self.gpu.release()
                started_day = None
                self.check_cap()
        self.gpu.release()

    def _morning(self, day: int) -> None:
        for path, text in self.timeline.files_on(day).items():
            target = self.workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        if day == 1 and self.timeline.seeded_skills:
            # The smoke world's skills were saved before its first day (the product's records say the evening
            # before), so they are older than that morning's change, as a skill made weeks earlier would be.
            from row_bot import stale_skills

            evening = datetime.combine(self.clock.now().date() - timedelta(days=1), datetime.min.time()).replace(
                hour=18)
            stale_skills.set_clock(lambda: evening)
            try:
                for procedure_id in self.timeline.seeded_skills:
                    self._seed_skill(self.timeline.procedure(procedure_id), day, why="the smoke world's skill")
            finally:
                stale_skills.set_clock(lambda: self.clock.now().replace(tzinfo=None))
        for edit in [item for item in self.timeline.edits if item.day == day]:
            self._edit_skill(edit)
        if self.study_arm in ("R", "A"):
            self._approve_reconciliations(day)
        self.state.data["mornings"].append(day)
        self.save()

    def _edit_skill(self, edit: world.Change) -> None:
        """A newer-skill event: the person edits the procedure's skill to the new value (memory is never told)."""

        from row_bot import skills, stale_skills

        procedure = self.timeline.procedure(edit.procedure)
        seeded = not self.state.data["skills"].get(procedure.id, {}).get("name")
        if seeded:
            self._seed_skill(procedure, edit.day, why="the person's edit found no skill")
        name = self.state.data["skills"][procedure.id]["name"]
        skills.load_skills()
        found = skills.get_skill(name)
        text, replaced = world.edit_skill_text(procedure, str(found.instructions or ""), edit)
        with stale_skills.cause("person_edit"):
            updated = skills.update_skill(name, instructions=text)
        self.state.data["edits"].append({"day": edit.day, "procedure": procedure.id, "skill": name, "seeded": seeded,
                                         "replaced": replaced, "ok": updated is not None,
                                         "names_new": world.states(procedure.kind, edit.new, text)})
        self.note(f"day {edit.day}: the person edited {name!r} to {world.say(procedure.kind, edit.new)} "
                  f"({'replaced' if replaced else 'a dated line added'})")

    def _approve_reconciliations(self, day: int) -> None:
        """Arm R's policy: the person approves every reconciliation proposal the next morning (the product's apply,
        as the approval card does)."""

        from row_bot import evolution

        for proposal in evolution.list_proposals(status="ready", proposal_type="patch_skill"):
            if not str(proposal.get("title") or "").startswith("Reconcile skill:"):
                continue
            result = evolution.apply_proposal(proposal["id"], require_approval=False, approved_by_user=True)
            target = str((proposal.get("payload") or {}).get("target_skill") or "")
            self.state.data["approvals"].append({"day": day, "proposal": proposal["id"], "skill": target,
                                                 "ok": bool(result.get("ok")),
                                                 "message": str(result.get("message") or "")[:300]})
            self.note(f"day {day}: approved reconciliation of {target!r}: {result.get('message')}")

    # ── A turn and what it did ──────────────────────────────────────────

    def _observed(self, thread_id: str, text: str, request: Any) -> tuple[Any, dict[str, Any]]:
        from row_bot import threads

        before_messages = len(_messages(threads, thread_id))
        before_apps = self.apps.state()
        self._recalled = []
        result = self.turn(thread_id, text, request)
        errors = list(self._errors)
        self._check_limits()
        messages = _messages(threads, thread_id)[before_messages:]
        calls, results = [], []
        for message in messages:
            for call in getattr(message, "tool_calls", None) or ():
                calls.append({"name": str(call.get("name") or ""), "args": call.get("args") or {}})
            if getattr(message, "type", "") == "tool":
                results.append({"name": str(getattr(message, "name", "") or ""),
                                "content": _content_text(getattr(message, "content", ""))})
        seen = {
            "actions": scorer.actions_between(before_apps, self.apps.state()),
            "tools": [_invoked(call) for call in calls],
            "calls": [{"name": _invoked(call), "args": call["args"]} for call in calls],
            "skill_loads": [_skill_ref(call) for call in calls if _invoked(call) == "skill_load"],
            "date_notes": [item["content"][:1200] for item in results if '"date_check"' in item["content"]],
            "memory_results": [item["content"][:4000] for item in results if "memory" in item["name"].lower()
                               or "recall" in item["name"].lower() or _bridged_memory(item, calls)],
            "recalled": [text for batch in self._recalled for text in batch],
            "answer": result.answer[:3000],
            "error": result.error,
            "error_text": errors[:3],
        }
        return result, seen

    def _new_thread(self, key: str, title: str) -> str:
        from row_bot import threads

        thread_id = _thread_id(self.person_id, f"{key}-{uuid.uuid4().hex[:4]}")
        threads.create_thread(title[:48], thread_id=thread_id, seed_default_skills=False,
                              approval_mode=str(self.person.get("approval_mode") or ""))
        return thread_id

    def _tell(self, item: world.Tell) -> dict[str, Any]:
        """What the person tells Row-Bot, in a chat of its own (no check: it is news, not a task)."""

        self.check_cap()
        self._place_apps(item.day)
        thread_id = self._new_thread(f"tell-{item.id}", item.messages[0])
        tokens = self.cloud_tokens()
        turns = []
        for message in item.messages:
            self.clock.used()
            result, seen = self._observed(thread_id, message, _Asked())
            turns.append({"said": message, "tools": seen["tools"], "calls": seen["calls"][:20],
                          "answer": seen["answer"][:600], "error": result.error})
            self.clock.advance(minutes=2)
        record = {"id": item.id, "day": item.day, "about": item.about, "what": item.what, "thread": thread_id,
                  "turns": turns, "saved_memory": sum(1 for turn in turns for name in turn["tools"]
                                                      if name in ("save_memory", "update_memory", "memory_save")),
                  "tokens": self.cloud_tokens() - tokens}
        self.note(f"day {item.day} told ({item.what} {item.about}): tools {[t['tools'] for t in turns]}")
        return record

    def _place_apps(self, day: int) -> None:
        from row_bot.learning import eval_worker
        from row_bot.learning.packs.fake_apps import FakeApps

        self.apps = FakeApps(self.timeline.apps_on(day))
        eval_worker._CURRENT.apps = self.apps  # noqa: SLF001

    def _run(self, request: Any) -> dict[str, Any]:
        """One recurring run: the turn, the check, the person's reaction (first attempt scored), arm F's repair,
        then consent."""

        from row_bot.learning import teach
        from row_bot.learning.packs.verify import Outcome, verify, workspace_files

        self.check_cap()
        self._place(request)
        procedure = self.timeline.procedure(self.timeline.procedure_of(request.id))
        thread_id = self._new_thread(f"run-{request.id}", request.prompt)
        tokens_before = self.cloud_tokens()
        skill = self._skill_state(procedure)
        skills_before = self._user_skills()
        record: dict[str, Any] = {"id": request.id, "day": request.day, "procedure": procedure.id,
                                  "at": self.clock.now().replace(tzinfo=None).isoformat(timespec="seconds"),
                                  "thread": thread_id, "prompt": request.prompt, "attempts": [], "reaction": [],
                                  "skill": skill, "build": self.build}
        text = request.prompt
        for attempt in range(1, 4):
            self.clock.used()
            result, seen = self._observed(thread_id, text, request)
            outcome = Outcome(result.answer, workspace_files(self.workspace), self.apps.state())
            verdict = verify(request.task, outcome, backend="auto")
            acting = scorer.acting_step(procedure, seen["actions"])
            value_ok = acting is not None and scorer.classify(self.timeline, procedure.id, request.day,
                                                              acting) == scorer.CURRENT
            passed = bool(verdict.passed and value_ok)
            record["attempts"].append({"passed": passed, "verifier": verdict.passed, "tool_calls": result.tool_calls,
                                       "seconds": round(result.seconds, 1), "stopped": result.stopped,
                                       "approvals": result.approvals, **seen})
            if passed:
                break
            problem = verdict.detail if not verdict.passed else f"{procedure.fact} was wrong"
            reaction = self.sim.react(request, result.answer, problem or "the outcome was wrong", attempt,
                                      stopped=result.stopped)
            self._check_limits()
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
        scored = scorer.score_run(self.timeline, request.id, request.day, first["actions"])
        record["score"] = scored.to_dict()
        texts = [*first["recalled"], *first["memory_results"]]
        record["recalled_new"] = scorer.surfaced(self.timeline, procedure.id, request.day, texts)
        record["recalled_old"] = scorer.surfaced(self.timeline, procedure.id, request.day, texts, which="old")
        record["auto_recall_new"] = scorer.surfaced(self.timeline, procedure.id, request.day, first["recalled"])
        record["skill_loaded"] = bool(skill.get("name")) and any(skill["name"] in ref for ref in first["skill_loads"])
        record["any_skill_loaded"] = bool(first["skill_loads"])
        record["date_note"] = bool(first["date_notes"])
        record["reads_before_acting"] = reads_before(procedure, first["calls"])
        record["asked_first"] = "ask_person" in record["reads_before_acting"]
        self.clock.advance(minutes=2)
        if self.study_arm == "F" and record["skill_loaded"] and not record["passed_first"]:
            self._repair(request, procedure, record)
        self._consent(request, procedure, thread_id, record)
        made = sorted(set(self._user_skills()) - set(skills_before) - set(
            (record.get("consent") or {}).get("made") or ()))
        if made:
            self.state.data["unprompted_skills"].append({"run": request.id, "day": request.day, "made": made})
        record["tokens"] = self.cloud_tokens() - tokens_before
        self.note(f"day {request.day} {procedure.id}: {scored.verdict}{' (in window)' if scored.in_window else ''}"
                  f", first {'ok' if record['passed_first'] else 'wrong'}, skill "
                  f"{'loaded' if record['skill_loaded'] else 'not loaded'}"
                  f"{', date note' if record['date_note'] else ''}; {record['tokens']:,} tokens")
        return record

    def _repair(self, request: Any, procedure: world.Procedure, record: dict[str, Any]) -> None:
        """Arm F: the product drafts a repair from the failed run, and the person approves it at once (the policy)."""

        from row_bot import evolution
        from row_bot.stale_skills import arms

        name = record["skill"]["name"]
        started = time.monotonic()
        outcome = arms.repair(name, request=request.prompt, answer=record["attempts"][0]["answer"],
                              correction=repair_correction(record, procedure))
        repair = {"day": request.day, "run": request.id, "skill": name, "seconds": round(time.monotonic() - started, 1),
                  **outcome}
        if outcome.get("outcome") == "proposed":
            result = evolution.apply_proposal(outcome["proposal_id"], require_approval=False, approved_by_user=True)
            repair["applied"] = {"ok": bool(result.get("ok")), "message": str(result.get("message") or "")[:300]}
        self.state.data["repairs"].append(repair)
        record["repair"] = repair
        self.note(f"day {request.day}: repair of {name!r}: {outcome.get('outcome')}")

    def _probe(self, probe: world.Probe) -> dict[str, Any]:
        self.check_cap()
        self._place_apps(probe.day)
        thread_id = self._new_thread(f"probe-{probe.id}", probe.question)
        tokens = self.cloud_tokens()
        self.clock.used()
        result, seen = self._observed(thread_id, probe.question, _Asked())
        self.clock.advance(minutes=2)
        verdict = scorer.score_probe(probe, result.answer)
        self.note(f"day {probe.day} probe {probe.category} ({probe.fact}): {verdict}")
        return {"id": probe.id, "day": probe.day, "category": probe.category, "fact": probe.fact,
                "question": probe.question, "expected": list(probe.expected), "wrong": list(probe.wrong),
                "kind": probe.kind, "answer": result.answer[:3000], "tools": seen["tools"], "verdict": verdict,
                "tokens": self.cloud_tokens() - tokens}

    # ── The skill ───────────────────────────────────────────────────────

    def _skill_state(self, procedure: world.Procedure) -> dict[str, Any]:
        from row_bot import skills

        entry = dict(self.state.data["skills"].get(procedure.id) or {})
        entry.pop("consent", None)
        if not entry.get("name"):
            return {}
        found = skills.get_skill(entry["name"])
        text = _skill_text(found)
        change = self.timeline.shift_of(procedure.id)
        entry.update({"exists": found is not None, "enabled": bool(found and skills.is_enabled(found.name)),
                      "names_start": world.states(procedure.kind, self.timeline.start[procedure.id], text),
                      "names_new": bool(change and world.states(procedure.kind, change.new, text)),
                      "sha": _sha(text)})
        return entry

    def _user_skills(self) -> dict[str, str]:
        from row_bot import skills

        skills.load_skills()
        return {item.name: _skill_text(item) for item in skills.get_manual_skills() if item.source == "user"}

    def _consent(self, request: Any, procedure: world.Procedure, thread_id: str, record: dict[str, Any]) -> None:
        """On the procedure's consent run the person says yes to Row-Bot's offer (or asks for the skill) and approves
        its card; if Row-Bot saves none, the template skill is seeded that day."""

        if self.state.data["skills"].get(procedure.id, {}).get("name") or not record["passed_final"]:
            return
        passed = sum(1 for item in self.state.data["runs"] if item["procedure"] == procedure.id
                     and item["passed_final"])
        if passed + 1 < self.timeline.consent_run[procedure.id]:
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
            result, seen = self._observed(thread_id, message, _Asked())
            consent["turns"].append({"said": message, "tools": seen["tools"], "calls": seen["calls"][:20],
                                     "approvals": result.approvals, "error": result.error,
                                     "answer": seen["answer"][:600]})
            made = {name: text for name, text in self._user_skills().items() if name not in before}
            self.clock.advance(minutes=2)
            if made:
                break
            message = CONSENT_APPLY
        consent["proposals"] = _proposals(since=proposals_before)
        consent["made"] = sorted(made)
        consent["made_text"] = {name: text[:3000] for name, text in made.items()}
        consent["card"] = any(item.get("tool") == "row_bot_apply_proposal" for turn in consent["turns"]
                              for item in turn["approvals"])
        value = self.timeline.value(procedure.id, request.day)
        if made:
            name = sorted(made)[0]
            consent["names_fact"] = world.states(procedure.kind, value, made[name])
            self.state.data["skills"][procedure.id] = {"name": name, "source": "offer" if offered else "asked",
                                                       "day": request.day, "names_fact": consent["names_fact"],
                                                       "card": consent["card"], "consent": consent}
            self.note(f"skill {name!r} made by Row-Bot ({'offer' if offered else 'asked'}, card {consent['card']}), "
                      f"day {request.day}, names the fact: {consent['names_fact']}")
            record["consent"] = consent
            return
        record["consent"] = consent
        self._seed_skill(procedure, request.day, why="Row-Bot saved no skill", consent=consent)

    def _seed_skill(self, procedure: world.Procedure, day: int, *, why: str,
                    consent: dict[str, Any] | None = None) -> None:
        from row_bot import skills

        if self.state.data["skills"].get(procedure.id, {}).get("name"):
            return
        spec = seeded_skill(procedure, self.timeline.value(procedure.id, day) if consent is not None
                            else self.timeline.start[procedure.id], self.timeline.catalogue.first_name)
        skills.load_skills()
        if skills.get_skill(spec["name"]) is None:
            skills.create_skill(name=spec["name"], display_name=spec["display_name"], icon="📋",
                                description=spec["description"], instructions=spec["instructions"],
                                tags=["stale-skills"], enabled=True)
        self.state.data["skills"][procedure.id] = {"name": spec["name"], "source": "seeded", "day": day,
                                                   "names_fact": True, "why": why, "consent": consent or {}}
        self.note(f"skill {spec['name']!r} seeded on day {day} ({why})")

    # ── Result ──────────────────────────────────────────────────────────

    def stale_result(self) -> dict[str, Any]:
        from row_bot import stale_skills

        data = self.state.data
        scored = [{**item["score"], "skill_loaded": item["skill_loaded"], "recalled_new": item["recalled_new"],
                   "passed_first": item["passed_first"], "at": item.get("at")} for item in data["runs"]]
        operations = stale_skills.operations()
        stored = scorer.stored_changes(self.timeline, operations)
        return {
            "format": "row_bot.learning.stale_skills_arm", "version": 2, "arm": data.get("arm"),
            "person": self.person_id, "seed": self.args.seed, "days_asked": self.args.days, "smoke": self.args.smoke,
            "days_done": len([day for day, night in data["nights"].items() if night.get("done")]),
            "timeline": self.timeline.fingerprint(), "phrasing": self.timeline.phrasing,
            "changes": [item.to_dict() for item in self.timeline.changes], "arrivals": self.timeline.arrivals,
            "newer_skill_events": [item.to_dict() for item in self.timeline.edits], "edits": data["edits"],
            "stored": stored, "metrics": scorer.metrics(self.timeline, scored, stored),
            "probes": scorer.probe_metrics(data["probes"]),
            "patches": scorer.patch_metrics(self.timeline, data["skills"], operations),
            "operations": {event: sum(1 for item in operations if item["event"] == event)
                           for event in sorted({item["event"] for item in operations})},
            "approvals": data["approvals"], "repairs": data["repairs"],
            "skills": {key: {k: v for k, v in value.items() if k != "consent"} | {
                "consent_turns": len((value.get("consent") or {}).get("turns") or ()),
                "proposals": (value.get("consent") or {}).get("proposals")} for key, value in data["skills"].items()},
            "unprompted_skills": data["unprompted_skills"], "redone": data["redone"],
            "runs": [{key: item.get(key) for key in ("id", "day", "procedure", "passed_first", "passed_final",
                                                     "recalled_new", "recalled_old", "auto_recall_new",
                                                     "skill_loaded", "any_skill_loaded", "date_note",
                                                     "reads_before_acting", "asked_first", "tokens")}
                     | {"score": item["score"], "skill": {key: (item.get("skill") or {}).get(key)
                                                          for key in ("name", "source", "names_start", "names_new")},
                        "tools": item["attempts"][0]["tools"],
                        "reactions": [reaction["action"] for reaction in item["reaction"]]}
                     for item in data["runs"]],
            "tells": [{key: item.get(key) for key in ("id", "day", "about", "what", "saved_memory", "tokens")}
                      for item in data["tells"]],
            "past": data.get("past"), "evenings": data["evenings"],
            "tokens": {"ledger": self.ledger_tokens(), "sim_person": self.sim.tokens.to_dict(),
                       "cloud_total": self.cloud_tokens()},
            "gpu": data["gpu"], "refused_tool_calls": data.get("refused_tool_calls", 0),
            "write_backs": list(self.sign_in.write_backs) if self.sign_in else [],
            "limit_errors": data.get("limit_errors", []), "started": data["started"],
            "nights": {day: {step: (value or {}).get("error") or "ok" for step, value in (night.get("steps") or {}).items()}
                       for day, night in data["nights"].items()},
            "reconciled": {day: (((night.get("steps") or {}).get("dream") or {}).get("outcome") or {}).get("reconciled")
                           for day, night in data["nights"].items()},
        }

    def _dream(self) -> Any:
        """S1's Dream step, keeping arm R's reconciliation summary (S1 keeps only the numbers)."""

        from row_bot import dream_cycle

        if not dream_cycle.is_enabled():
            return {"ran": False, "why": "off"}
        found = dream_cycle.run_dream_cycle() or {}
        kept = {key: value for key, value in found.items() if isinstance(value, (int, float, bool))}
        if found.get("reconciled_skills") is not None:
            kept["reconciled"] = found["reconciled_skills"]
        kept["errors"] = list(found.get("errors") or ())[:5]
        return kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One arm of the stale-skills run for one person (Design A)")
    parser.add_argument("--arm", default="N", choices=tuple(ARMS), help="the study arm (A: a second copy of R)")
    parser.add_argument("--person", default=world.PERSON, choices=world.PEOPLE)
    parser.add_argument("--model", choices=("main", "local"), required=True)
    parser.add_argument("--local-model", default="qwen3.5:9b")
    parser.add_argument("--data", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--days", type=int, default=world.DAYS)
    parser.add_argument("--seed", type=int, default=world.SEED)
    parser.add_argument("--phrasing", default="main", help="main, an Ollama model's label, or templates")
    parser.add_argument("--smoke", action="store_true", help="the two-day smoke world (phase 2)")
    parser.add_argument("--eval-profile", required=True)
    parser.add_argument("--cap-tokens", type=int, required=True)
    parser.add_argument("--spent-before", type=int, default=0)
    parser.add_argument("--gpu-booking", default="")
    parser.add_argument("--stages", default=",".join(STAGES))
    parser.add_argument("--sim-date", action="store_true", help="the turn context says the simulated date")
    args = parser.parse_args(argv)
    args.lane_model = args.model
    if args.smoke:
        args.days = world.SMOKE_DAYS
    # S1's PersonRun reads these; this run trains nothing and runs no crash or comparison checks.
    args.tier, args.lane, args.crash, args.gpu, args.pilot_compare = "8gb", "lane-B", False, False, True
    args.runtimes_from = ""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    data = check_scratch(Path(args.data), Path(args.eval_profile))
    if not re.search(r"(^|/)sa2?_", data.as_posix().lower()):
        raise ScratchError(f"{data}: the stale-skills profiles live under an sa_ or sa2_ folder")
    run = StaleRun(args)
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
        if stop.code not in (EXIT_TOKEN_CAP, EXIT_LIMIT):
            raise
        code = int(stop.code)
    finally:
        run.gpu.release()
        run.save()
        (run.run_dir / "stale.json").write_text(json.dumps(run.stale_result(), indent=1, sort_keys=True, default=str),
                                                encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
