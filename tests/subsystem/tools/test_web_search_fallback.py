"""A2 WP3: web search always has a keyless path, and says what to do when nothing answers.

Tavily with no key, a refused key, a spent quota or no connection falls back to the keyless search with a
one-line label; when that fails too, the result is one plain next step. No network: both searches are fakes.
"""

from __future__ import annotations

import pytest
import requests
from tavily.errors import InvalidAPIKeyError, TimeoutError, UsageLimitExceededError

pytestmark = pytest.mark.subsystem

FOUND = [{"title": "Rowing results", "snippet": "Cambridge won.", "link": "https://example.test/boat-race"}]


@pytest.fixture
def search(monkeypatch):
    """Fake Tavily and fake keyless engines; each test says how they behave."""
    import tavily
    from langchain_community.tools import DuckDuckGoSearchResults
    from row_bot.tools import duckduckgo_tool, registry, web_search_tool

    state = {"key": "tvly-test", "tavily": None, "keyless": FOUND, "tavily_calls": 0, "keyless_calls": 0}

    class FakeTavily:
        def __init__(self, api_key=None):
            assert api_key == state["key"]

        def search(self, query, max_results=8):
            state["tavily_calls"] += 1
            if isinstance(state["tavily"], Exception):
                raise state["tavily"]
            return {"results": [{"title": "From Tavily", "content": "Oxford won.", "url": "https://example.test/t"}]}

    def keyless_invoke(self, query, *args, **kwargs):
        state["keyless_calls"] += 1
        if isinstance(state["keyless"], Exception):
            raise state["keyless"]
        return state["keyless"]

    monkeypatch.setattr(tavily, "TavilyClient", FakeTavily)
    monkeypatch.setattr(DuckDuckGoSearchResults, "invoke", keyless_invoke)
    monkeypatch.setattr("row_bot.api_keys.get_key", lambda name: state["key"] if name == "TAVILY_API_KEY" else "")
    monkeypatch.setattr(registry, "is_enabled", lambda name: name != "duckduckgo" or state.get("ddg_on", True))
    state["web_search"] = web_search_tool.WebSearchTool().as_langchain_tool()
    state["duckduckgo"] = duckduckgo_tool.DuckDuckGoTool().as_langchain_tool()
    return state


def test_a_working_tavily_answers_itself(search):
    result = search["web_search"].invoke({"query": "boat race"})

    assert "Oxford won." in result and "keyless" not in result
    assert search["keyless_calls"] == 0


def test_with_no_key_tavily_is_not_asked_and_the_keyless_search_answers(search):
    search["key"] = ""

    result = search["web_search"].invoke({"query": "boat race"})

    assert search["tavily_calls"] == 0
    assert result.splitlines()[0] == "Tavily isn't available (no API key); results from the keyless search:"
    assert "Cambridge won." in result and "SOURCE_URL: https://example.test/boat-race" in result


@pytest.mark.parametrize(("error", "reason"), [
    (InvalidAPIKeyError("Unauthorized"), "its API key was refused"),
    (UsageLimitExceededError("Plan limit exceeded"), "its usage limit is reached"),
    (TimeoutError(10), "it couldn't be reached"),
    (requests.exceptions.ConnectionError("offline"), "it couldn't be reached"),
])
def test_a_tavily_auth_quota_or_network_error_falls_back_with_a_label(search, error, reason):
    search["tavily"] = error

    result = search["web_search"].invoke({"query": "boat race"})

    assert result.splitlines()[0] == f"Tavily isn't available ({reason}); results from the keyless search:"
    assert "Cambridge won." in result


def test_when_both_fail_one_line_gives_the_next_step(search):
    from ddgs.exceptions import RatelimitException

    search["tavily"] = UsageLimitExceededError("Plan limit exceeded")
    search["keyless"] = RatelimitException("202 Ratelimit")

    result = search["web_search"].invoke({"query": "boat race"})

    assert "\n" not in result
    assert result.startswith("Web search couldn't get results just now: Tavily isn't available")
    assert result.endswith("Try different words in a minute, or read a page you know with read_url.")


def test_the_keyless_search_is_not_used_when_the_person_switched_it_off(search):
    search["key"] = ""
    search["ddg_on"] = False

    result = search["web_search"].invoke({"query": "boat race"})

    assert search["keyless_calls"] == 0
    assert "switched off" in result and "read_url" in result


def test_a_keyless_search_failure_gives_a_next_step_not_an_error(search):
    from ddgs.exceptions import DDGSException

    search["keyless"] = DDGSException("No results found.")

    result = search["duckduckgo"].invoke({"query": "boat race"})

    assert not result.startswith("Error")
    assert result == (
        "Web search couldn't get results just now (the search engines limited requests or found nothing). "
        "Try different words in a minute, or read a page you know with read_url."
    )
