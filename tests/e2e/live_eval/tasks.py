"""The live eval's tasks: what each one asks, the files it seeds, and its pass check.

Every check is deterministic: a string in the answer, a parsed structure, a
file in the task's workspace, or a fact the runner collected from Row-Bot
(a saved memory, a workflow run, ledger purposes). Checks are pure functions of
an :class:`Outcome`, so the deterministic tests exercise them without a model.

Tiers: ``core`` runs by default; ``full`` adds the heavier paging tasks
(``ROW_BOT_LIVE_EVAL_TIER=full``); ``learning`` runs Learning's task packs and
replay fixtures instead (``learning.py``). Provider-specific tasks (local Ollama)
run only on that provider.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

TIER_ENV = "ROW_BOT_LIVE_EVAL_TIER"
TASKS_ENV = "ROW_BOT_LIVE_EVAL_TASKS"
LONG_TURNS_ENV = "ROW_BOT_LIVE_EVAL_LONG_TURNS"
LONG_WINDOW_ENV = "ROW_BOT_LIVE_EVAL_LONG_WINDOW"
LONG_CHAPTER_ENV = "ROW_BOT_LIVE_EVAL_LONG_CHAPTER_CHARS"
DEFAULT_LONG_TURNS = 12
DEFAULT_LONG_WINDOW = 40_000
DEFAULT_LONG_CHAPTER_CHARS = 10_000

# Planning prefixes (system prompt, guides and tool schemas) per tool profile, in
# tokens, on the high side. Measured offline on 2026-10-11: the tool schemas of one
# to three tool groups are 0.3-2K tokens and the default profile's are about 11K;
# the Agent system prompt and guidelines are about 5K.
PREFIX_TOKENS = {"none": 5_000, "some": 9_000, "default": 24_000}


@dataclass(frozen=True)
class Turn:
    prompt: str
    mode: str = "agent"  # "agent" | "chat_only"
    new_thread: bool = False


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    name: str
    content: str = ""
    error: bool = False


@dataclass
class TurnResult:
    prompt: str
    thread_id: str = ""
    answer: str = ""
    status: str = "unknown"  # done | error | exception | budget
    errors: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    interrupts: int = 0
    event_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class Outcome:
    turns: list[TurnResult] = field(default_factory=list)
    workspace: Path | None = None
    collected: dict[str, Any] = field(default_factory=dict)

    @property
    def answer(self) -> str:
        return self.turns[-1].answer if self.turns else ""

    def tool_names(self) -> list[str]:
        return [call.name for turn in self.turns for call in turn.tool_calls]

    def failed_turn(self) -> TurnResult | None:
        return next((turn for turn in self.turns if turn.status != "done"), None)


@dataclass(frozen=True)
class Check:
    passed: bool
    reason: str = ""
    metrics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Context:
    """What a task's turns and seeds may depend on."""

    fixture_url: str = ""
    long_turns: int = DEFAULT_LONG_TURNS
    long_window: int = DEFAULT_LONG_WINDOW
    long_chapter_chars: int = DEFAULT_LONG_CHAPTER_CHARS

    @classmethod
    def from_env(cls, fixture_url: str = "", env: Mapping[str, str] | None = None) -> Context:
        source = os.environ if env is None else env

        def number(name: str, default: int, floor: int) -> int:
            text = str(source.get(name, "") or "").strip()
            return max(floor, int(text)) if text else default

        return cls(
            fixture_url=fixture_url,
            long_turns=number(LONG_TURNS_ENV, DEFAULT_LONG_TURNS, 3),
            long_window=number(LONG_WINDOW_ENV, DEFAULT_LONG_WINDOW, 32_000),
            long_chapter_chars=number(LONG_CHAPTER_ENV, DEFAULT_LONG_CHAPTER_CHARS, 2_000),
        )


@dataclass(frozen=True)
class Task:
    task_id: str
    category: str
    turns: Callable[[Context], Sequence[Turn]]
    check: Callable[[Outcome, Context], Check]
    tools: tuple[str, ...] | str = ()  # registry names, or "default" (the enabled set, minus risky tools)
    est_calls: int = 1
    est_growth_tokens: int = 300  # tokens each later call adds (tool results, answers)
    est_output_tokens: int = 600
    seed: Callable[[Path, Context], None] | None = None
    # Names of collectors the runner calls after the turns (Row-Bot lookups).
    collect: tuple[str, ...] = ()
    depends_on: str = ""
    only_providers: frozenset[str] = frozenset()
    skip_providers: frozenset[str] = frozenset()
    tier: str = "core"
    # Force this context window (tokens) for the task, to reach masking and compaction.
    context_window: Callable[[Context], int] | None = None
    note: str = ""

    def tool_profile(self) -> str:
        if self.tools == "default":
            return "default"
        return "some" if self.tools else "none"

    def runs_on(self, provider_id: str) -> bool:
        if self.only_providers and provider_id not in self.only_providers:
            return False
        return provider_id not in self.skip_providers


# ── Helpers ────────────────────────────────────────────────────────────────


def normalize(text: str) -> str:
    """Lower case, markdown and surrounding punctuation removed, spaces collapsed."""
    cleaned = re.sub(r"[`*_]", "", str(text or "")).strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .!\"'")


def contains(text: str, needle: str) -> bool:
    return normalize(needle) in normalize(text)


def contains_number(text: str, number: str) -> bool:
    """The number appears as a number (thousands separators allowed), not inside a longer one."""
    plain = str(text or "").replace(",", "")
    return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", plain) is not None


def parse_json_answer(text: str) -> Any:
    body = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", body, re.S)
    if fenced:
        body = fenced.group(1).strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the answer")
    return json.loads(body[start:end + 1])


def _done(outcome: Outcome) -> Check | None:
    failed = outcome.failed_turn()
    if failed is not None:
        return Check(False, f"turn did not finish ({failed.status}): {'; '.join(failed.errors)[:200]}")
    return None


def _answer_check(expected: str, *, number: bool = False, tool: str = "") -> Callable[[Outcome, Context], Check]:
    def check(outcome: Outcome, _context: Context) -> Check:
        if (failure := _done(outcome)) is not None:
            return failure
        if tool and tool not in outcome.tool_names():
            return Check(False, f"{tool} was not called (called: {sorted(set(outcome.tool_names()))})")
        found = contains_number(outcome.answer, expected) if number else contains(outcome.answer, expected)
        return Check(found, "" if found else f"expected {expected!r} in the answer")

    return check


def _file_text(outcome: Outcome, relative: str) -> str | None:
    if outcome.workspace is None:
        return None
    path = outcome.workspace / relative
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _write(workspace: Path, relative: str, text: str) -> None:
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


# ── Seeds ──────────────────────────────────────────────────────────────────

CSV_ROWS = (("apples", 12), ("pears", 7), ("plums", 30), ("figs", 5))
STEP_NUMBERS = (11, 23, 35, 47, 59)
CHAIN_NUMBERS = (12, 18, 25, 31, 44)
INBOX_FILES = ("a_report.txt", "b_notes.md", "c_budget.csv")
ARTICLE_CODEWORD = "ORCHID-7731"
LONG_PAGE_CODEWORD = "MAGENTA-2290"
BIG_FILE_CODEWORD = "TEAL-5521"
MEMORY_FACT = "BX-4471-Q"
LOOP_PRODUCTS = ("13*17", "19*23", "29*31", "37*41", "43*47", "53*59", "61*67", "71*73")
CHAPTER_WORDS = (
    "LANTERN", "QUARRY", "MERIDIAN", "THISTLE", "BASALT", "CORMORANT", "JUNIPER", "OBSIDIAN",
    "PELICAN", "SAFFRON", "TUNDRA", "VERMILION", "WILLOW", "ZEPHYR", "ANVIL", "BRAMBLE",
)


def filler(chars: int, *, seed: str) -> str:
    """Deterministic prose-like filler of about *chars* characters (no codewords)."""
    words = (
        "river", "ledger", "copper", "harbor", "signal", "orchard", "granite", "meadow", "pattern",
        "window", "kernel", "lattice", "beacon", "cellar", "summit", "thread", "valley", "engine",
    )
    out: list[str] = []
    total = 0
    index = sum(ord(character) for character in seed)
    while total < chars:
        sentence = " ".join(words[(index + step * 7) % len(words)] for step in range(11)).capitalize() + ". "
        out.append(sentence)
        total += len(sentence)
        index += 5
    return "".join(out)[:chars]


def chapter_word(index: int) -> str:
    return CHAPTER_WORDS[index % len(CHAPTER_WORDS)] + (str(index // len(CHAPTER_WORDS)) if index >= len(CHAPTER_WORDS) else "")


def long_page_html(chars: int = 130_000) -> str:
    body = filler(chars, seed="long-page")
    return (
        "<html><head><title>Field notes</title></head><body><h1>Field notes</h1>"
        f"<p>{body}</p><p>Final codeword: {LONG_PAGE_CODEWORD}</p></body></html>"
    )


def article_html() -> str:
    return (
        "<html><head><title>Harbor report</title></head><body><h1>Harbor report</h1>"
        f"<p>{filler(1_500, seed='article')}</p><p>The codeword for this report is {ARTICLE_CODEWORD}.</p>"
        "</body></html>"
    )


FIXTURE_PAGES: dict[str, Callable[[], str]] = {"/article.html": article_html, "/long.html": long_page_html}


def _seed_csv(workspace: Path, _context: Context) -> None:
    _write(workspace, "data.csv", "item,qty\n" + "".join(f"{name},{qty}\n" for name, qty in CSV_ROWS))


def _seed_ini(workspace: Path, _context: Context) -> None:
    _write(workspace, "settings.ini", "[server]\nhost = localhost\nport = 8080\nmode = prod\n")


def _seed_inbox(workspace: Path, _context: Context) -> None:
    for name in INBOX_FILES:
        _write(workspace, f"inbox/{name}", f"{name}\n")


def _seed_json(workspace: Path, _context: Context) -> None:
    _write(workspace, "config.json", json.dumps({"service": {"name": "atlas", "limits": {"daily": 250, "burst": 40}}}))


def _seed_chain(workspace: Path, _context: Context) -> None:
    _write(workspace, "numbers.txt", "".join(f"{number}\n" for number in CHAIN_NUMBERS))


def _seed_steps(workspace: Path, _context: Context) -> None:
    for index, number in enumerate(STEP_NUMBERS, start=1):
        _write(workspace, f"steps/s{index}.txt", f"Step {index} value: {number}\n")


def _seed_big_file(workspace: Path, _context: Context) -> None:
    # One long line: the reader pages it, and the end is only reachable by paging.
    _write(workspace, "big.txt", filler(120_000, seed="big") + f" Final codeword: {BIG_FILE_CODEWORD}\n")


def chapter_text(index: int, chars: int) -> str:
    """Chapter *index* (0-based): filler with its keyword sentence in the middle."""
    text = filler(chars, seed=f"chapter{index}")
    middle = len(text) // 2
    return f"{text[:middle]} The keyword of chapter {index + 1} is {chapter_word(index)}. {text[middle:]}"


def _file_chapter(index: int) -> bool:
    """Even indexes are read from files (tool output A4a clears); odd ones are pasted (history that grows)."""
    return index % 2 == 0


def _seed_chapters(workspace: Path, context: Context) -> None:
    for index in range(context.long_turns):
        if _file_chapter(index):
            _write(workspace, f"chapters/chapter_{index + 1}.txt", chapter_text(index, context.long_chapter_chars) + "\n")


# ── Checks that need more than the answer ──────────────────────────────────


def _check_json_city(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    try:
        data = parse_json_answer(outcome.answer)
    except (ValueError, json.JSONDecodeError) as exc:
        return Check(False, f"answer is not a JSON object: {exc}")
    ok = normalize(str(data.get("city"))) == "tokyo" and normalize(str(data.get("country"))) == "japan"
    return Check(ok, "" if ok else f"unexpected object {data!r}"[:200])


def _check_exact(expected: str) -> Callable[[Outcome, Context], Check]:
    def check(outcome: Outcome, _context: Context) -> Check:
        if (failure := _done(outcome)) is not None:
            return failure
        ok = normalize(outcome.answer) == normalize(expected)
        return Check(ok, "" if ok else f"expected exactly {expected!r}")

    return check


def _check_followup(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    if len(outcome.turns) < 3:
        return Check(False, "fewer than 3 turns ran")
    reversed_ok = contains(outcome.turns[1].answer, "NOREH")
    month_ok = contains(outcome.turns[2].answer, "march")
    return Check(reversed_ok and month_ok, "" if reversed_ok and month_ok else "lost context between turns")


def _check_tool_followup(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    if len(outcome.turns) < 2 or "calculate" not in outcome.tool_names():
        return Check(False, "the tool turn or the follow-up did not run")
    ok = contains_number(outcome.turns[0].answer, "5535") and contains_number(outcome.turns[1].answer, "11070")
    return Check(ok, "" if ok else "wrong product or follow-up")


def _check_write_read(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    text = _file_text(outcome, "notes/plan.txt")
    if text is None or normalize(text) != "alpha beta gamma delta":
        return Check(False, f"notes/plan.txt is {text!r}"[:200])
    return Check(contains_number(outcome.answer, "4"), "" if contains_number(outcome.answer, "4") else "word count not 4")


def _check_ini(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    text = _file_text(outcome, "settings.ini") or ""
    lines = {re.sub(r"\s+", "", line) for line in text.splitlines() if line.strip()}
    ok = "port=8085" in lines and "host=localhost" in lines and "mode=prod" in lines and "port=8080" not in lines
    return Check(ok, "" if ok else f"settings.ini is {text!r}"[:200])


def _check_inbox(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    answer = normalize(outcome.answer)
    positions = [answer.find(name) for name in INBOX_FILES]
    ok = all(position >= 0 for position in positions) and positions == sorted(positions)
    return Check(ok, "" if ok else "file names missing or out of order")


def _check_chain(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    if "calculate" not in outcome.tool_names():
        return Check(False, "calculate was not called")
    text = _file_text(outcome, "result.txt")
    ok_file = text is not None and contains_number(text, "26.00")
    ok_answer = contains_number(outcome.answer, "26.00") or contains_number(outcome.answer, "26")
    return Check(ok_file and ok_answer, "" if ok_file and ok_answer else f"result.txt is {text!r}"[:200])


def _check_status_usage(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    calls = [call for turn in outcome.turns for call in turn.tool_calls if call.name == "row_bot_status"]
    if not any(str(call.args.get("category", "")).lower() == "usage" for call in calls):
        return Check(False, f"row_bot_status not called with category usage: {[c.args for c in calls]}"[:200])
    bad = [result for turn in outcome.turns for result in turn.tool_results
           if result.name == "row_bot_status" and (result.error or "validation error" in result.content.lower())]
    return Check(not bad, "" if not bad else "row_bot_status returned a validation error",
                 {"status_calls": len(calls)})


def _check_paged(codeword: str) -> Callable[[Outcome, Context], Check]:
    def check(outcome: Outcome, _context: Context) -> Check:
        if (failure := _done(outcome)) is not None:
            return failure
        names = outcome.tool_names()
        paged = "read_tool_output" in names or sum(name in {"read_url", "workspace_read_file"} for name in names) > 1
        found = contains(outcome.answer, codeword)
        metrics = {"read_tool_output_calls": names.count("read_tool_output"), "paged": paged}
        return Check(found and paged, "" if found and paged else f"codeword found={found}, paged={paged}", metrics)

    return check


def _check_memory_saved(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    saved = bool(outcome.collected.get("memory_has_fact"))
    return Check(saved, "" if saved else "no saved memory contains the fact")


def _check_workflow(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    run = outcome.collected.get("workflow") or {}
    if not run.get("task_id"):
        return Check(False, "no workflow named 'Live eval digest' was created")
    status = str(run.get("status") or "")
    ok = status == "completed" and bool(run.get("output_ok"))
    return Check(ok, "" if ok else f"workflow run status={status!r} output_ok={run.get('output_ok')}", {"run": run})


def _check_delegate(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    if "delegate_work" not in outcome.tool_names():
        return Check(False, "delegate_work was not called")
    child_calls = int(outcome.collected.get("child_calls") or 0)
    ok = contains_number(outcome.answer, "1591") and child_calls > 0
    return Check(ok, "" if ok else f"answer has 1591={contains_number(outcome.answer, '1591')}, child calls={child_calls}")


def _check_loop(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    calls = outcome.tool_names().count("calculate")
    ok = contains_number(outcome.answer, "17492") and calls >= 4
    return Check(ok, "" if ok else f"sum present={contains_number(outcome.answer, '17492')}, calculate calls={calls}",
                 {"calculate_calls": calls})


def _check_long_turn(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    total = str(sum(STEP_NUMBERS))
    text = _file_text(outcome, "steps/total.txt")
    tool_calls = len(outcome.tool_names())
    ok = contains_number(outcome.answer, total) and text is not None and contains_number(text, total) and tool_calls >= 6
    return Check(ok, "" if ok else f"total.txt={text!r}, tool calls={tool_calls}"[:200], {"tool_calls": tool_calls})


def _check_long_thread(outcome: Outcome, context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    expected = [chapter_word(index) for index in range(context.long_turns)]
    final = outcome.turns[-1].answer
    recalled = [word for word in expected if contains(final, word)]
    per_turn = sum(contains(turn.answer, word) for turn, word in zip(outcome.turns[:-1], expected))
    compactions = int(outcome.collected.get("compaction_calls") or 0)
    share = len(recalled) / len(expected)
    ok = per_turn >= len(expected) - 1 and share >= 0.75 and compactions >= 1
    return Check(
        ok,
        "" if ok else f"per-turn keywords {per_turn}/{len(expected)}, recalled {len(recalled)}/{len(expected)}, "
                      f"compactions {compactions}",
        {"per_turn_keywords": per_turn, "recalled_share": round(share, 2), "compaction_calls": compactions},
    )


def _check_ollama_reload(outcome: Outcome, _context: Context) -> Check:
    if (failure := _done(outcome)) is not None:
        return failure
    probe = outcome.collected.get("ollama") or {}
    problems = list(probe.get("problems") or [])
    return Check(not problems and bool(probe.get("requests")), "; ".join(problems) or (
        "" if probe.get("requests") else "no Ollama requests were captured"), {"ollama": probe})


# ── The registry ───────────────────────────────────────────────────────────


def _turns(*prompts: str, mode: str = "agent") -> Callable[[Context], Sequence[Turn]]:
    return lambda _context: tuple(Turn(prompt, mode=mode) for prompt in prompts)


def _long_thread_turns(context: Context) -> Sequence[Turn]:
    turns = []
    for index in range(context.long_turns):
        if _file_chapter(index):
            turns.append(Turn(
                f"Read chapters/chapter_{index + 1}.txt from the workspace and reply with only that chapter's keyword."
            ))
        else:
            turns.append(Turn(
                f"Here is chapter {index + 1} of my notes:\n\n{chapter_text(index, context.long_chapter_chars)}\n\n"
                "Reply with only that chapter's keyword."
            ))
    turns.append(Turn(
        "List every chapter keyword you reported in this conversation, in chapter order, separated by commas. "
        "Do not read the files again."
    ))
    return turns


def _url_turns(path: str, prompt: str) -> Callable[[Context], Sequence[Turn]]:
    return lambda context: (Turn(prompt.format(url=f"{context.fixture_url}{path}")),)


SAFE_DEFAULT_EXCLUDE = frozenset({
    # Never bound in the "default" profile: they act on the host or an account outside the
    # run's sandbox (shell, browser, desktop, camera, mail, updates), cost extra media
    # calls, or always stop for a person (conversation_setup).
    "shell", "browser", "computer_use", "gmail", "calendar", "x", "developer", "designer", "mcp",
    "row_bot_updater", "custom_tool_builder", "vision", "system_info", "image_gen", "video_gen",
    "conversation_setup",
})

TASKS: tuple[Task, ...] = (
    Task("chat_exact", "chat", _turns("Reply with exactly: live eval ok"), _check_exact("live eval ok"),
         est_calls=1, est_output_tokens=400),
    Task("chat_json", "chat",
         _turns('Reply with only a JSON object (no code fence) with the keys "city" and "country" for the capital of Japan.'),
         _check_json_city, est_calls=1, est_output_tokens=400),
    Task("chat_reasoning", "chat",
         _turns("A train leaves at 09:40 and the journey takes 2 hours 35 minutes. Reply with only the arrival time as HH:MM."),
         _answer_check("12:15"), est_calls=1, est_output_tokens=1_200),
    Task("chat_followup", "chat",
         _turns(
             "My project codename is HERON and the launch date is 14 March. Acknowledge in one short sentence.",
             "Write the codename backwards in capital letters. Reply with only that word.",
             "In which month is the launch? Reply with one word.",
         ),
         _check_followup, est_calls=3, est_output_tokens=400),
    Task("chat_only_mode", "chat", _turns("Reply with exactly: chat only ok", mode="chat_only"),
         _check_exact("chat only ok"), est_calls=1, est_output_tokens=300),
    Task("calc_single", "tools", _turns("Use the calculate tool to compute 17**3 + 289. Reply with only the number."),
         _answer_check("5202", number=True, tool="calculate"), tools=("calculator",), est_calls=2),
    Task("tool_then_followup", "tools",
         _turns("Use the calculate tool to compute 123*45. Reply with only the number.",
                "Now double that result. Reply with only the number."),
         _check_tool_followup, tools=("calculator",), est_calls=4,
         note="B2: the next turn replays the tool turn's reasoning (OpenAI, Codex, xAI) without a 400"),
    Task("calc_compound", "tools",
         _turns("Use the calculate tool to compute 2500 * 1.04**7 rounded to 2 decimal places. Reply with only the number."),
         _answer_check("3289.83", number=True, tool="calculate"), tools=("calculator",), est_calls=2),
    Task("file_write_read", "files",
         _turns("In the workspace, create the file notes/plan.txt containing exactly this line: alpha beta gamma delta. "
                "Then read it back and reply with only the number of words in it."),
         _check_write_read, tools=("filesystem",), est_calls=3),
    Task("file_csv_sum", "files",
         _turns("Read data.csv from the workspace and reply with only the sum of the qty column."),
         _answer_check("54", number=True, tool="workspace_read_file"), tools=("filesystem",), seed=_seed_csv, est_calls=2),
    Task("file_edit_ini", "files",
         _turns("In the workspace file settings.ini, change the port to 8085 and leave every other line as it is. "
                "Reply with only: done"),
         _check_ini, tools=("filesystem",), seed=_seed_ini, est_calls=3),
    Task("file_list_dir", "files",
         _turns("List the files in the workspace folder inbox. Reply with only their names, sorted alphabetically, "
                "separated by commas."),
         _check_inbox, tools=("filesystem",), seed=_seed_inbox, est_calls=2),
    Task("file_json_lookup", "files",
         _turns("Read config.json from the workspace. What is the burst limit of the service? Reply with only the number."),
         _answer_check("40", number=True, tool="workspace_read_file"), tools=("filesystem",), seed=_seed_json, est_calls=2),
    Task("multi_tool_chain", "tools",
         _turns("Read numbers.txt from the workspace (one number per line), use the calculate tool to compute their "
                "average, and write the average with exactly two decimals to result.txt. Reply with only the average."),
         _check_chain, tools=("filesystem", "calculator"), seed=_seed_chain, est_calls=4),
    Task("read_url_local", "web",
         _url_turns("/article.html", "Read {url} with read_url and reply with only the codeword it contains."),
         _answer_check(ARTICLE_CODEWORD, tool="read_url"), tools=("url_reader",), est_calls=2, est_growth_tokens=1_000),
    Task("status_usage_enum", "tools",
         _turns("Use row_bot_status to look at the usage category, then tell me in one sentence how many model calls "
                "this conversation has made so far."),
         _check_status_usage, tools=("row_bot_status",), est_calls=2, est_growth_tokens=800),
    Task("default_toolset_choice", "tools",
         _turns("What is 15% of 2,340? Use a tool to compute it and reply with only the number."),
         _answer_check("351", number=True), tools="default", est_calls=2,
         note="the enabled tool set (minus tools that reach outside the sandbox): tool choice among many"),
    Task("memory_save", "memory",
         _turns(f"Please remember this for later: my bicycle's frame number is {MEMORY_FACT}. Confirm in one sentence."),
         _check_memory_saved, tools=("memory",), collect=("memory_fact",), est_calls=2),
    Task("memory_recall", "memory",
         lambda _context: (Turn("What is my bicycle's frame number? Reply with only the frame number.", new_thread=True),),
         _answer_check(MEMORY_FACT), tools=("memory",), depends_on="memory_save", est_calls=2),
    Task("workflow_run", "workflow",
         _turns("Create a workflow named 'Live eval digest' with exactly one step whose prompt is: Reply with exactly: "
                "digest ready. Do not schedule it. Then run it once now. Reply with only: started"),
         _check_workflow, tools=("task",), collect=("workflow",), est_calls=6, est_growth_tokens=600),
    Task("delegate_child", "delegation",
         _turns("Use delegate_work and wait for the result: ask a helper agent to compute 37*43 with the calculate tool "
                "and report the number. Then reply with only that number."),
         _check_delegate, tools=("agents", "calculator"), collect=("child_calls",), est_calls=6, est_growth_tokens=800),
    Task("warm_loop_cache", "cache",
         _turns("Compute each of these with the calculate tool, one call at a time and in this order, never two in one "
                "step: " + ", ".join(LOOP_PRODUCTS) + ". Then reply with only the sum of the eight results."),
         _check_loop, tools=("calculator",), est_calls=10, est_growth_tokens=250,
         note="A3 healthy-loop signature: cache reads should dominate input from the second call on"),
    Task("long_turn_positions", "cache",
         _turns("Work strictly one tool call at a time, writing one short progress sentence before each call: "
                "list the workspace folder steps; read steps/s1.txt; read steps/s2.txt; read steps/s3.txt; read "
                "steps/s4.txt; read steps/s5.txt; compute the sum of the five values with the calculate tool; write "
                "the sum to steps/total.txt. Then reply with only the sum."),
         _check_long_turn, tools=("filesystem", "calculator"), seed=_seed_steps, est_calls=10, est_growth_tokens=300,
         note="A3: more than 15 positions in one turn should still read the earlier prefix"),
    Task("long_thread_compaction", "long_thread", _long_thread_turns, _check_long_thread, tools=("filesystem",),
         seed=_seed_chapters, collect=("compaction_calls",), est_calls=21, est_growth_tokens=2_900,
         context_window=lambda context: context.long_window,
         note="A4a masking jumps (file chapters) and compaction (pasted chapters) in a forced small window"),
    Task("read_url_paged", "web",
         _url_turns("/long.html", "Read {url} and reply with only the codeword given at the very end of the page."),
         _check_paged(LONG_PAGE_CODEWORD), tools=("url_reader",), est_calls=4, est_growth_tokens=12_000, tier="full"),
    Task("read_tool_output_file", "files",
         _turns("Read big.txt from the workspace and reply with only the codeword given at the very end of it."),
         _check_paged(BIG_FILE_CODEWORD), tools=("filesystem",), seed=_seed_big_file, est_calls=4,
         est_growth_tokens=14_000, tier="full", note="D1 paging: the end is only reachable by paging or read_tool_output"),
    Task("ollama_local_turn", "local",
         _turns("Use the calculate tool to compute 48*52 and reply with only the number."),
         _answer_check("2496", number=True, tool="calculate"), tools=("calculator",), only_providers=frozenset({"ollama"}),
         est_calls=2),
    Task("ollama_no_reload", "local",
         _turns("Reply with exactly: warm", "Use the calculate tool to compute 9*9 and reply with only the number."),
         _check_ollama_reload, tools=("calculator",), collect=("ollama_requests",), only_providers=frozenset({"ollama"}),
         est_calls=6, note="B2: one num_ctx, a 30-minute keep-alive, and no reload across agent, Chat Only, "
                           "compaction, vision and title calls"),
)


def by_id() -> dict[str, Task]:
    from .learning import all_tasks

    return {task.task_id: task for task in (*TASKS, *all_tasks())}


def select_tasks(provider_id: str, env: Mapping[str, str] | None = None) -> list[Task]:
    """The tasks one provider runs, in registry order, for the tier and task filter in the environment."""
    source = os.environ if env is None else env
    tier = str(source.get(TIER_ENV, "") or "core").strip().lower()
    if tier not in {"core", "full", "learning"}:
        raise ValueError(f"{TIER_ENV} must be core, full or learning, not {tier!r}")
    wanted = [name.strip() for name in str(source.get(TASKS_ENV, "") or "").split(",") if name.strip()]
    if tier == "learning":
        from .learning import tasks as learning_tasks

        chosen = learning_tasks(source)
        unknown = [name for name in wanted if name not in {task.task_id for task in chosen}]
        if unknown:
            raise ValueError(f"{TASKS_ENV} names unknown learning tasks {unknown}")
        return [task for task in chosen if not wanted or task.task_id in wanted]
    known = by_id()
    unknown = [name for name in wanted if name not in known]
    if unknown:
        raise ValueError(f"{TASKS_ENV} names unknown tasks {unknown}")
    chosen = []
    for task in TASKS:
        if wanted and task.task_id not in wanted:
            continue
        if not wanted and tier == "core" and task.tier != "core":
            continue
        # A local model runs a short representative set unless tasks are named.
        if not wanted and provider_id == "ollama" and task.task_id not in LOCAL_DEFAULT_TASKS:
            continue
        if task.runs_on(provider_id):
            chosen.append(task)
    return chosen


LOCAL_DEFAULT_TASKS = frozenset({
    "chat_exact", "chat_followup", "calc_single", "file_csv_sum", "warm_loop_cache", "ollama_local_turn",
    "ollama_no_reload",
})


def long_thread_calls(context: Context) -> int:
    """File chapters take two calls (read, answer), pasted ones one; then the final turn and compactions."""
    file_turns = sum(1 for index in range(context.long_turns) if _file_chapter(index))
    return 2 * file_turns + (context.long_turns - file_turns) + 1 + 2


def estimate_tokens(task: Task, context: Context, prefix_tokens: Mapping[str, int] | None = None) -> int:
    """Upper-leaning tokens a task processes: each call re-sends the prefix and what came before."""
    prefix = (prefix_tokens or PREFIX_TOKENS)[task.tool_profile()]
    calls = task.est_calls
    growth = task.est_growth_tokens
    if task.task_id == "long_thread_compaction":
        calls = long_thread_calls(context)
        growth = int(context.long_chapter_chars / 3.5)
        # The forced window caps what any one call can send.
        per_call = [min(context.long_window, prefix + growth * (index // 2 + 1)) for index in range(calls)]
        return sum(per_call) + calls * task.est_output_tokens
    total = 0
    for index in range(calls):
        total += prefix + growth * index + task.est_output_tokens
    return total


def estimate(task: Task, context: Context, rates: Mapping[str, Any], provider: str, model: str) -> tuple[int, float | None]:
    """(tokens, USD ceiling or None when unpriced) for one task on one model."""
    from .pricing import rates_for, upper_bound_usd

    tokens = estimate_tokens(task, context)
    calls = long_thread_calls(context) if task.task_id == "long_thread_compaction" else task.est_calls
    output = calls * task.est_output_tokens
    return tokens, upper_bound_usd(tokens - output, output, rates_for(rates, provider, model))
