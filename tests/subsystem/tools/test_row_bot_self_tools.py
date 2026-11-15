"""Row-Bot's guides tool, what needs attention, and its fix (self system, WP5 and WP8)."""
from __future__ import annotations

import re

import pytest

from row_bot import guides
from row_bot.tools import row_bot_status_tool as status

pytestmark = pytest.mark.subsystem

SYSTEM = """---
title: "Settings: System"
---

## Privacy

1. Open **Settings › System**.
2. Turn off **Share install stats**.
"""


@pytest.fixture
def docs(tmp_path, monkeypatch):
    root = tmp_path / "docs"
    (root / "settings").mkdir(parents=True)
    (root / "settings" / "system.mdx").write_text(SYSTEM, encoding="utf-8")
    monkeypatch.setattr(guides, "guides_dir", lambda: root)
    return root


def test_the_guides_tool_answers_a_how_to_with_steps_and_a_citation(docs):
    answer = status._row_bot_docs(query="how do I turn off install stats")

    assert answer.startswith("### Settings: System › Privacy\nCite: [Settings: System › Privacy]"
                             "(/docs/settings/system#privacy)\n\n1. Open **Settings › System**.")
    assert "each setting linked to its row" in answer


def test_the_guides_tool_reads_a_section_whole_and_says_what_it_cant_find(docs):
    assert "2. Turn off **Share install stats**." in status._row_bot_docs(page="/docs/settings/system#privacy")
    assert status._row_bot_docs(page="/docs/settings/nowhere") == "No guide at /docs/settings/nowhere. Search with a query instead."
    assert "No guide covers 'zebra quantum'" in status._row_bot_docs(query="zebra quantum")


def test_the_guides_tool_says_so_when_an_install_has_no_guides(tmp_path, monkeypatch):
    monkeypatch.setattr(guides, "guides_dir", lambda: tmp_path / "missing")

    assert "This install has no guides" in status._row_bot_docs(query="install stats")


def test_the_self_tools_come_in_one_group():
    names = [tool.name for tool in status.RowBotStatusTool().as_langchain_tools()]

    assert names[:4] == ["row_bot_status", "row_bot_docs", "row_bot_update_setting", "row_bot_fix"]


def _problems(monkeypatch, *, attention=(), checks=(), diagnosed=None):
    from row_bot.application import client_diagnosis, client_monitor

    state = {"attention": list(attention), "checks": list(checks), "runs": 0}

    def run_system_diagnosis():
        state["runs"] += 1
        if diagnosed is not None:
            state["attention"], state["checks"] = diagnosed
        return {}

    monkeypatch.setattr(client_monitor, "read_attention",
                        lambda *, include_update: {"schema_version": 1, "problems": state["attention"], "update": None})
    monkeypatch.setattr(client_diagnosis, "read_system_health",
                        lambda **_kwargs: {"schema_version": 1, "hourly_network_checks": True, "checks": state["checks"]})
    monkeypatch.setattr(client_diagnosis, "run_system_diagnosis", run_system_diagnosis)
    return state


EXPIRED = {"id": "health:sign-in:codex", "title": "ChatGPT sign-in needs attention",
           "detail": "Your ChatGPT sign-in has expired. Sign in again to use its models.", "place": "health",
           "fix": {"kind": "reconnect_account", "href": "/settings/models?provider=codex", "target": "codex",
                   "name": "ChatGPT"}}
STOPPED = {"id": "channel:telegram", "title": "Telegram stopped",
           "detail": "It is set to start with Row-Bot but isn't running.", "place": "channels",
           "fix": {"kind": "restart_channel", "href": "/settings/apps/telegram", "target": "telegram", "name": "Telegram"}}
DISK = {"id": "disk", "name": "Disk", "status": "warn", "detail": "4.0 GB free (92% used)", "checked_at": 1.0,
        "settings_tab": "System", "network": False, "stale": False,
        "fix": {"kind": "check_again", "href": None, "target": None, "name": "Disk"}}
TELEGRAM_CHECK = {**DISK, "id": "channel:telegram", "name": "Telegram", "detail": "Stopped",
                  "fix": {"kind": "restart_channel", "href": "/settings/apps/telegram", "target": "telegram",
                          "name": "Telegram"}}


def test_health_is_what_monitor_and_needs_you_show_errors_first_each_with_its_fix(monkeypatch):
    """G12: Row-Bot read raw log lines while Monitor knew "Telegram stopped" and how to fix it."""
    _problems(monkeypatch, attention=[EXPIRED, STOPPED], checks=[DISK, TELEGRAM_CHECK])

    lines = status._row_bot_status("health").splitlines()

    assert lines[0] == "**What needs attention** (3, as Monitor and Needs you show it)"
    assert lines[1] == ("- [error] ChatGPT sign-in needs attention: Your ChatGPT sign-in has expired. Sign in again to "
                        "use its models. Fix: [Reconnect ChatGPT](/settings/models?provider=codex). "
                        "(problem 'health:sign-in:codex')")
    assert lines[2].startswith("- [error] Telegram stopped: It is set to start") and "[Restart Telegram]" in lines[2]
    # The channel's own Monitor check is the same problem: listed once.
    assert lines[3] == ("- [warn] Disk: 4.0 GB free (92% used). Fix: Check Disk again. (problem 'health:disk')")
    assert len(lines) == 5


def test_nothing_to_fix_says_so(monkeypatch):
    _problems(monkeypatch)

    assert status._row_bot_status("health").startswith("**What needs attention**\nNothing:")


def test_a_fix_only_the_person_can_do_comes_back_as_their_link(monkeypatch):
    state = _problems(monkeypatch, attention=[EXPIRED])

    answer = status._row_bot_fix("health:sign-in:codex")

    assert "[Reconnect ChatGPT](/settings/models?provider=codex)" in answer and "the person's to do" in answer
    assert state["runs"] == 0  # Nothing contacted for a sign-in.


def test_checking_again_runs_the_checks_and_reports_what_is_left(monkeypatch):
    state = _problems(monkeypatch, checks=[DISK], diagnosed=([], []))

    assert status._row_bot_fix("health:disk") == "Checked again: Disk is fine now."
    assert state["runs"] == 1


def test_a_problem_that_is_gone_says_so_without_running_anything(monkeypatch):
    state = _problems(monkeypatch)

    assert status._row_bot_fix("health:disk").startswith("That problem isn't there any more.")
    assert state["runs"] == 0


def test_providers_say_when_a_sign_in_needs_the_person(monkeypatch):
    """G5: "configured" while the ChatGPT sign-in had expired."""
    from row_bot.providers import status as provider_status

    monkeypatch.setattr(provider_status, "sign_in_problems", lambda: [
        {"provider_id": "codex", "name": "ChatGPT", "status": "error",
         "detail": "Your ChatGPT sign-in has expired. Sign in again to use its models."}])
    monkeypatch.setattr(provider_status, "provider_status_cards", lambda **_kwargs: [
        {"provider_id": "codex", "display_name": "ChatGPT / Codex", "configured": True, "source": "",
         "model_count": None, "runtime_enabled": True}])

    text = status._row_bot_status("providers")

    assert ("- ChatGPT / Codex: configured, catalog count unknown; Your ChatGPT sign-in has expired. Sign in again "
            "to use its models. Fix: [ChatGPT](/settings/models?provider=codex)") in text


# The private-path pattern Monitor keeps out of what it shows (client_diagnosis).
_PRIVATE_PATH = re.compile(r"(?i)\b[A-Z]:\\|/(?:Users|home|root|var|private|tmp|Volumes)/")


@pytest.mark.parametrize("category", ["overview", "tasks", "config"])
def test_no_area_row_bot_reads_back_names_a_private_path(tmp_path, monkeypatch, category):
    """G7: the task database's path and the wiki vault's went to the model, and so to channels."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    (tmp_path / "wiki_config.json").write_text('{"enabled": true, "vault_path": "C:\\\\Users\\\\Fixture\\\\vault"}',
                                               encoding="utf-8")

    text = status._row_bot_status(category)

    assert not _PRIVATE_PATH.search(text), text
    assert str(tmp_path) not in text
