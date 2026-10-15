"""The agent opens a pull request with the GitHub CLI on this computer, by the thread's approval mode (F20)."""

from __future__ import annotations

import json
import subprocess

import pytest

from tests.helpers.fake_gh import install_fake_gh

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

_FRESH_MODULES = (
    "row_bot.developer.executables",
    "row_bot.developer.storage",
    "row_bot.developer.tool_context",
    "row_bot.developer.change_ledger",
    "row_bot.developer.edits",
    "row_bot.developer.sandbox_runtime",
    "row_bot.tools.developer_tool",
)
PR_URL = "https://github.com/example/shop/pull/12"


def git(repo, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def branch(tmp_path, monkeypatch, reload_for_data_dir):
    """A repo on fix/csv-escaping with one commit beyond main, and the agent's tool context set."""
    _executables, storage, tool_context, *_rest, developer_tool = reload_for_data_dir(
        tmp_path / "data", *_FRESH_MODULES
    )
    repo = tmp_path / "shop"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "export.js").write_text("export {}\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Start")
    git(repo, "switch", "-c", "fix/csv-escaping")
    (repo / "export.js").write_text("export const quote = true;\n", encoding="utf-8")
    git(repo, "commit", "-am", "Quote CSV fields that contain commas")
    workspace = storage.add_or_update_local_workspace(str(repo))
    thread_id = storage.ensure_workspace_thread(workspace.id)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    gh = install_fake_gh(tmp_path, monkeypatch, pr_url=PR_URL)
    approvals: list[dict] = []
    answer = {"approve": True}
    monkeypatch.setattr(developer_tool, "interrupt", lambda request: approvals.append(request) or answer["approve"])

    def mode(value: str) -> None:
        monkeypatch.setattr(developer_tool, "_active_approval_mode", lambda: value)

    yield developer_tool, gh, approvals, answer, mode
    tool_context.reset_context(tokens)


def pr_creates(gh) -> list[list[str]]:
    return [call for call in gh.calls() if call[:2] == ["pr", "create"]]


def test_ask_mode_asks_then_opens_a_draft_described_from_the_branch(branch) -> None:
    developer_tool, gh, approvals, _answer, mode = branch
    mode("approve")

    result = json.loads(developer_tool._create_pull_request())

    assert [request["label"] for request in approvals] == ["Open pull request"]
    assert "draft pull request" in approvals[0]["description"]
    assert result == {
        "ok": True,
        "url": PR_URL,
        "draft": True,
        "title": "Quote CSV fields that contain commas",
    }
    (call,) = pr_creates(gh)
    assert call[call.index("--title") + 1] == "Quote CSV fields that contain commas"
    body = call[call.index("--body-file") + 1]
    assert body.startswith("@## Summary\n\n- Quote CSV fields that contain commas")
    assert "- `export.js`" in body
    assert "--draft" in call


def test_a_denied_approval_opens_nothing(branch) -> None:
    developer_tool, gh, approvals, answer, mode = branch
    mode("approve")
    answer["approve"] = False

    assert developer_tool._create_pull_request(title="Ready", draft=False) == "Pull request cancelled by user."
    assert len(approvals) == 1
    assert pr_creates(gh) == []


def test_block_mode_refuses_without_asking_and_full_autonomy_opens_without_asking(branch) -> None:
    developer_tool, gh, approvals, _answer, mode = branch
    mode("block")
    refused = json.loads(developer_tool._create_pull_request(title="Ready"))
    assert refused["ok"] is False
    assert "blocks attempts to open a pull request" in refused["error"]
    assert pr_creates(gh) == []

    mode("full")
    opened = json.loads(developer_tool._create_pull_request(title="Ready", body="Line one\nLine two", draft=False))
    assert opened["url"] == PR_URL
    assert approvals == []
    (call,) = pr_creates(gh)
    assert call[call.index("--body-file") + 1] == "@Line one\nLine two"
    assert "--draft" not in call


@pytest.mark.parametrize(("decision", "asked"), [("approve", 0), ("ask", 1)])
def test_auto_shows_the_pull_request_to_the_reviewer_first(branch, monkeypatch, decision, asked) -> None:
    from row_bot.approval_review import ReviewVerdict

    developer_tool, gh, approvals, _answer, mode = branch
    reviewed: list[tuple] = []
    monkeypatch.setattr("row_bot.approval_review.review", lambda payload, *, risks=(), **_kw: reviewed.append(
        (payload["tool"], tuple(risks))) or ReviewVerdict(decision, "You asked for a pull request."))
    mode("auto")

    opened = json.loads(developer_tool._create_pull_request(title="Ready"))

    assert opened["url"] == PR_URL
    assert len(approvals) == asked
    [(tool, risks)] = reviewed
    assert tool == "developer_create_pull_request"
    assert any("other people can see" in risk for risk in risks)


def test_a_branch_with_nothing_new_has_nothing_to_open(branch, tmp_path) -> None:
    developer_tool, gh, approvals, _answer, mode = branch
    mode("full")
    git(tmp_path / "shop", "switch", "main")

    assert "adds no commits" in developer_tool._create_pull_request()
    assert approvals == [] and pr_creates(gh) == []


def git_tab(developer_tool, monkeypatch):
    """What the Git tab reads for the workspace's current branch."""
    from row_bot.application.developer_repository_commands import CanonicalDeveloperRepositoryBackend
    from row_bot.developer import review

    monkeypatch.setattr(review, "workspace_has_custom_read_hooks", lambda _path: False)
    workspace = developer_tool.get_workspace(developer_tool.get_workspace_id())
    return CanonicalDeveloperRepositoryBackend().repository(workspace)[0]


def test_the_git_tab_shows_the_pull_request_the_agent_opened(branch, monkeypatch, tmp_path) -> None:
    """B336: the Git tab showed an empty new-pull-request form after the agent opened one."""
    developer_tool, gh, _approvals, _answer, mode = branch
    mode("full")
    assert git_tab(developer_tool, monkeypatch)["open_pull_request"] is None

    developer_tool._create_pull_request()
    shown = git_tab(developer_tool, monkeypatch)["open_pull_request"]

    assert {key: shown[key] for key in ("number", "title", "url", "draft")} == {
        "number": 12, "title": "Quote CSV fields that contain commas", "url": PR_URL, "draft": True}
    assert shown["opened_at"]
    # Reading the tab never contacts GitHub.
    assert [call for call in gh.calls() if call[:2] == ["pr", "view"]] == []
    git(tmp_path / "shop", "switch", "main")
    assert git_tab(developer_tool, monkeypatch)["open_pull_request"] is None


def test_a_branch_that_already_has_a_pull_request_shows_that_one(tmp_path, monkeypatch, branch) -> None:
    developer_tool, _gh, _approvals, _answer, mode = branch
    mode("full")
    earlier = "https://github.com/example/shop/pull/9"
    gh = install_fake_gh(tmp_path / "existing", monkeypatch, existing={
        "number": 9, "title": "Quote CSV fields", "url": earlier, "isDraft": False, "state": "OPEN"})

    result = json.loads(developer_tool._create_pull_request(title="Again"))

    assert result["ok"] is True and result["url"] == earlier
    assert "already has pull request #9" in result["message"]
    assert [call[:3] for call in gh.calls() if call[:2] == ["pr", "view"]] == [["pr", "view", "fix/csv-escaping"]]
    shown = git_tab(developer_tool, monkeypatch)["open_pull_request"]
    assert (shown["number"], shown["title"], shown["url"], shown["draft"]) == (9, "Quote CSV fields", earlier, False)

