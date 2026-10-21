from __future__ import annotations

import pytest

from row_bot.capability_search import CapabilityDocument, normalize_tokens, search_capabilities


def _doc(name: str, *, description: str = "", aliases: tuple[str, ...] = ()) -> CapabilityDocument:
    return CapabilityDocument(
        canonical_id=name,
        name=name,
        source="test",
        description=description,
        aliases=aliases,
    )


# Row-Bot's own tools as tool search sees them on a 32K window (name and description, cut at 180 characters).
REAL_TOOLS = [
    _doc("calculate", description="Evaluate a math, date or text-length expression. Operators: +, -, *, /, //, %, "
         "**. Functions: sqrt, cbrt, pow, abs, round, ceil, floor, trunc, int, float, min, max, sum, log, log2,"),
    _doc("create_chart", description="Create an interactive chart from tabular data when the person asks to plot, "
         "chart or visualise data, or a chart would explain it. Columns are picked automatically when x/y are not "),
    _doc("youtube_transcript", description="Fetch the transcript (captions/subtitles) of a YouTube video. Input a "
         "YouTube URL or video ID. Returns the full transcript text. Use this when a user shares a YouTube link or "
         "asks "),
    _doc("wiki_stats", description="Get statistics about the wiki vault: number of articles, conversations "
         "exported, vault path, and enabled status."),
    _doc("tracker_log", description="Log an entry to a habit or activity tracker (medications, symptoms, habits, "
         "periods, exercise, mood, sleep), creating the tracker if it doesn't exist yet."),
    _doc("tracker_delete", description="Delete a tracker or its entries. Examples: 'delete tracker Weight', 'remove "
         "last entry for Lexapro', 'stop tracking Exercise'."),
    _doc("send_file", description="Send files to the person: to=devices puts them in this chat as downloads on every "
         "device they're signed in on, with a notice (never asks); a chat app sends them there (may ask firs"),
    _doc("row_bot_install_update", description="Install a Row-Bot update (follows the thread's approval mode): "
         "prepares it in the background, or, once it is ready, restarts Row-Bot when nothing is running."),
    _doc("arxiv", description="Search arXiv for academic papers, sorted by newest first. Returns title, authors, "
         "date, abstract, and full-text HTML link for each result. Supports arXiv query syntax: ti:word (tit"),
    _doc("get_weather_forecast", description="Get a daily weather forecast for a location (1-14 days). Includes "
         "highs/lows, precipitation chance, wind, UV, sunrise/sunset times."),
]


def test_normalize_tokens_handles_case_and_common_separators() -> None:
    assert normalize_tokens("sendGmail_message.file-name") == (
        "send",
        "gmail",
        "message",
        "file",
        "name",
    )


def test_search_uses_exact_prefix_substring_then_text_score() -> None:
    docs = [
        _doc("mail_search", description="find inbox messages"),
        _doc("search_mail", description="find inbox messages"),
        _doc("archived_search_mail", description="find inbox messages"),
        _doc("inbox_lookup", description="search mail messages in an inbox"),
    ]

    assert search_capabilities(docs, "mail_search")[0].name == "mail_search"
    assert [doc.name for doc in search_capabilities(docs, "search", limit=4)] == [
        "search_mail",
        "archived_search_mail",
        "mail_search",
        "inbox_lookup",
    ]


def test_alias_exact_match_and_deterministic_ties() -> None:
    docs = [
        _doc("plugin:beta:lookup", aliases=("lookup",), description="records"),
        _doc("plugin:alpha:find", description="records"),
        _doc("manual_lookup", aliases=("manual",), description="records"),
    ]

    assert search_capabilities(docs, "lookup", limit=5)[0].canonical_id == "plugin:beta:lookup"
    assert [doc.canonical_id for doc in search_capabilities(docs, "records", limit=5)] == sorted(
        doc.canonical_id for doc in docs
    )


def test_search_clamps_limit_and_empty_query_returns_no_catalog_dump() -> None:
    docs = [_doc(f"tool_{index}", description="shared token") for index in range(10)]

    assert len(search_capabilities(docs, "shared", limit=99)) == 5
    assert len(search_capabilities(docs, "shared", limit=0)) == 1
    assert search_capabilities(docs, "   ", limit=5) == []


def test_module_is_provider_and_network_independent() -> None:
    import row_bot.capability_search as module

    names = set(module.__dict__)
    assert not any(name.startswith("requests") or name.startswith("httpx") for name in names)
    assert not any("provider" in name.lower() or "telemetry" in name.lower() for name in names)


@pytest.mark.parametrize(("query", "tool"), [
    ("calculator", "calculate"),  # -or: small models asked for "calculator" and found nothing (L2v)
    ("calculation", "calculate"),  # -ion
    ("calculating", "calculate"),  # -ing
    ("charts", "create_chart"),  # plural
    ("transcripts", "youtube_transcript"),
    ("statistic", "wiki_stats"),  # singular of "statistics"
    ("paper", "arxiv"),
    ("logged", "tracker_log"),  # -ed
    ("sender", "send_file"),  # -er
    ("installer", "row_bot_install_update"),
    ("deletion", "tracker_delete"),  # -ion
    ("forecasts", "get_weather_forecast"),
])
def test_another_form_of_a_word_finds_the_tool(query: str, tool: str) -> None:
    assert [doc.name for doc in search_capabilities(REAL_TOOLS, query)][:1] == [tool]


def test_the_word_as_written_ranks_above_another_form_of_it() -> None:
    docs = [_doc("first_tool", description="Lists the reminders"), _doc("second_tool", description="Edit a reminder")]

    assert [doc.name for doc in search_capabilities(docs, "reminder")] == ["second_tool", "first_tool"]
    assert [doc.name for doc in search_capabilities(docs, "reminders")] == ["first_tool", "second_tool"]


@pytest.mark.parametrize("query", [
    # Searches small models made for data, not tools (L2v and the Ollama fix's runs): still nothing.
    "Mezze House", "0f416237-befe-4e6c-951a-6add2e439437", "invoice INV-2207", "purchase order",
    "lunch meeting 2026-03-13",
])
def test_an_unrelated_search_still_finds_nothing(query: str) -> None:
    assert search_capabilities(REAL_TOOLS, query) == []


def test_short_words_are_not_folded_into_other_words() -> None:
    docs = [_doc("calendar_create_event", description="Create a new event. It does not invite anyone.")]

    assert search_capabilities(docs, "news") == []
    assert search_capabilities(docs, "notes") == []
    assert [doc.name for doc in search_capabilities(docs, "invites")] == ["calendar_create_event"]
