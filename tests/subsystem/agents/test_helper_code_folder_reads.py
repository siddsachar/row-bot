"""A read-only helper reads the code folder of the chat that delegated it, and only reads (B335).

A chat with a code folder leaves the general file and shell tools out of its turns, and the
built-in read-only profiles list no Developer tools, so a review helper delegated from it could
not open the folder. It now gets the Developer tools on that folder; its read-only capability
refuses every other Developer action at dispatch, before any approval, even under Allow all.
These are security tests: they stay in the pull request lane.
"""
from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.subsystem

# What a code-folder chat's own turn offers (no general file or shell tools).
_CODE_CHAT_TOOLS = ["developer", "memory", "conversation_search", "agents", "row_bot_status", "url_reader"]
# The reviewed Developer reads. Those that start git or ripgrep are offered but not run here.
_READS = {
    "developer_workspace_info", "developer_list_files", "developer_read_file", "developer_search",
    "developer_git_status", "developer_get_diff", "developer_preview_patch", "developer_list_agent_changes",
}
_RUN_READS = {"developer_list_files", "developer_read_file"}
# A real attempt for the reads and writes that matter most; any other tool gets placeholders.
_ATTEMPTS = {
    "developer_read_file": {"path": "src/dates.py"},
    "developer_write_file": {"path": "app.py", "content": "print('replaced')\n"},
    "developer_apply_patch": {
        "patch": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-print('hello')\n+print('patched')\n",
    },
    "developer_run_command": {"command": "python -c \"open('app.py','w').write('')\""},
    "developer_run_detected_test": {"command": "pytest"},
    "developer_create_branch": {"branch_name": "helper-branch"},
    "developer_switch_branch": {"branch_name": "helper-branch"},
    "developer_commit_changes": {"message": "Helper commit"},
    "developer_fast_forward_merge": {"branch_name": "main"},
    "developer_revert_agent_changes": {"change_set_id": "change-set"},
    "developer_import_media": {"media_ref": "media", "path": "media.png"},
    "developer_update_todos": {"labels": ["Helper plan"]},
}


def _modules(tmp_path, monkeypatch, reload_for_data_dir):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    skills_activation = importlib.import_module("row_bot.skills_activation")
    monkeypatch.setattr(skills_activation, "DATA_DIR", data_dir)
    monkeypatch.setattr(skills_activation, "STATE_PATH", data_dir / "skills_activation.json")
    storage = importlib.import_module("row_bot.developer.storage")
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DEVELOPER_DIR", data_dir / "developer")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", data_dir / "developer" / "workspaces.json")
    _tasks, threads, _settings, agent_profiles, _runs, _context, agent_runner = reload_for_data_dir(
        data_dir, "row_bot.tasks", "row_bot.threads", "row_bot.agent_settings",
        "row_bot.agent_profiles", "row_bot.agent_runs", "row_bot.agent_context", "row_bot.agent_runner",
    )
    return agent_runner, threads, agent_profiles


def _code_chat(tmp_path, threads, approval_mode: str = "auto"):
    """A chat on Auto (or another level) with a code folder bound, as the client binds one."""
    from row_bot import conversation_resources
    from row_bot.developer.state import DeveloperWorkspace
    from row_bot.developer.storage import save_workspace

    folder = tmp_path / "shop"
    (folder / "src").mkdir(parents=True)
    (folder / "app.py").write_text("print('hello')\n", encoding="utf-8", newline="\n")
    (folder / "src" / "dates.py").write_text("FORMAT = '%Y-%m-%d'\n", encoding="utf-8", newline="\n")
    save_workspace(DeveloperWorkspace(id="shop", name="Shop", path=str(folder)))
    chat = threads.create_thread("Fix the date bug", approval_mode=approval_mode)
    revision = conversation_resources.list_bindings(chat).revision
    conversation_resources.bind(chat, "workspace", "shop", expected_revision=revision)
    return chat, folder


def _fingerprint(folder: Path) -> dict[str, str]:
    return {
        str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*")) if path.is_file()
    }


def _placeholder(field) -> object:
    annotation = str(field.annotation)
    if "list" in annotation:
        return ["placeholder"]
    if "int" in annotation:
        return 1
    if "bool" in annotation:
        return False
    return "placeholder"


def _arguments(tool) -> dict:
    if tool.name in _ATTEMPTS:
        return dict(_ATTEMPTS[tool.name])
    schema = getattr(tool, "args_schema", None)
    fields = getattr(schema, "model_fields", {}) if schema is not None else {}
    return {name: _placeholder(field) for name, field in fields.items() if field.is_required()}


def _helper_dispatch(observed: dict):
    """Inside the helper's own run, as its graph would: its tools, each through the dispatch guard."""

    def invoke(prompt, enabled_tool_names, config, *, stop_event):
        from row_bot import agent
        from row_bot.tools import registry
        from row_bot.tools.developer_tool import DeveloperTool

        if registry.get_tool("developer") is None:
            registry.register(DeveloperTool())
        configurable = config["configurable"]
        allowlist = configurable.get("tool_allowlist")
        agent._set_active_runtime_context(
            thread_id=configurable["thread_id"],
            runtime_surface=configurable["runtime_surface"],
            approval_mode=configurable["approval_mode"],
            tool_allowlist=allowlist,
            agent_profile_id=configurable["agent_profile_id"],
            agent_profile_snapshot=configurable["agent_profile_snapshot"],
            agent_profile_frozen=True,
            agent_run_id=configurable["agent_run_id"],
        )
        observed["tools"] = list(enabled_tool_names)
        core, _external, _destructive = agent._collect_agent_tool_candidates(
            list(enabled_tool_names), set(allowlist) if allowlist else None
        )
        results = observed.setdefault("results", {})
        for entry in core:
            name = entry["tool"].name
            if entry["parent"] != "developer" or (name in _READS and name not in _RUN_READS):
                continue
            tool = agent._bind_profile_tool(
                entry["tool"], source=entry["source"], parent=entry["parent"],
                argument_function=entry.get("argument_function"),
            )
            try:
                results[name] = str(tool.invoke(_arguments(tool)))
            except Exception as exc:  # Reported, never hidden: a refused call must not raise.
                results[name] = f"raised {type(exc).__name__}: {exc}"
        return "Reviewed."

    return invoke


@pytest.mark.parametrize("approval_mode", ["auto", "full"])
def test_read_only_helper_reads_the_chats_code_folder_and_changes_nothing(
    tmp_path, monkeypatch, reload_for_data_dir, approval_mode
):
    """B335, and a hard limit no approval level changes: not even Hands-off lets a read-only helper write."""
    agent_runner, threads, _profiles = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    chat, folder = _code_chat(tmp_path, threads, approval_mode)
    before = _fingerprint(folder)
    observed: dict = {}
    monkeypatch.setattr(agent_runner, "_invoke_agent", _helper_dispatch(observed))

    run = agent_runner.spawn_agent_run(
        "Check the date handling in the fix.", parent_thread_id=chat, profile="review",
        enabled_tool_names=_CODE_CHAT_TOOLS, wait=True, timeout=10,
    )

    assert run["status"] == "completed"
    assert "developer" in observed["tools"]
    assert not {"filesystem", "shell"} & set(observed["tools"])
    results = observed["results"]
    # It lists and reads the chat's own folder.
    assert "- app.py" in results["developer_list_files"]
    assert "- src/" in results["developer_list_files"]
    assert "FORMAT = '%Y-%m-%d'" in results["developer_read_file"]
    # Every Developer action that is not a reviewed read is refused before it runs or asks,
    # although the chat is on Auto or Hands-off.
    refused = {name: text for name, text in results.items() if name not in _READS}
    assert {
        "developer_write_file", "developer_apply_patch", "developer_run_command",
        "developer_create_branch", "developer_commit_changes", "developer_push_current_branch",
        "developer_create_pull_request", "developer_import_sandbox_changes",
        "developer_revert_agent_changes",
    } <= set(refused)
    for name, text in refused.items():
        assert text.startswith("BLOCKED") and "read-only Agent Profile" in text, (name, text)
    assert _fingerprint(folder) == before


@pytest.mark.parametrize("profile", ["write", "plan"])
def test_a_write_capable_helper_gains_no_code_folder_tools(
    tmp_path, monkeypatch, reload_for_data_dir, profile
):
    """Only a read-only helper inherits the folder's reads: a writer never gains write access."""
    agent_runner, threads, _profiles = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    chat, _folder = _code_chat(tmp_path, threads)
    observed: dict = {}
    monkeypatch.setattr(agent_runner, "_invoke_agent", _helper_dispatch(observed))

    run = agent_runner.spawn_agent_run(
        "Draft the release note.", parent_thread_id=chat, profile=profile,
        enabled_tool_names=_CODE_CHAT_TOOLS, wait=True, timeout=10,
    )

    assert run["status"] == "completed"
    assert "developer" not in observed["tools"]
    assert observed["results"] == {}


def test_a_person_s_own_read_only_profile_keeps_its_tool_list(
    tmp_path, monkeypatch, reload_for_data_dir
):
    agent_runner, threads, agent_profiles = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    chat, _folder = _code_chat(tmp_path, threads)
    agent_profiles.save_agent_profile(
        slug="notes_only", display_name="Notes only", instructions="Read notes.",
        tool_policy_json={"capability": "read_only", "allow_tools": ["memory"]},
    )
    observed: dict = {}
    monkeypatch.setattr(agent_runner, "_invoke_agent", _helper_dispatch(observed))

    run = agent_runner.spawn_agent_run(
        "Summarise the notes.", parent_thread_id=chat, profile="notes_only",
        enabled_tool_names=_CODE_CHAT_TOOLS, wait=True, timeout=10,
    )

    assert run["status"] == "completed"
    assert observed["tools"] == ["memory"]


def test_a_helper_on_another_folder_gains_no_reads_of_it(
    tmp_path, monkeypatch, reload_for_data_dir
):
    """The reads follow the delegating chat's own folder, not one the helper is pointed at."""
    agent_runner, threads, _profiles = _modules(tmp_path, monkeypatch, reload_for_data_dir)
    from row_bot.developer.state import DeveloperWorkspace
    from row_bot.developer.storage import save_workspace

    chat, _folder = _code_chat(tmp_path, threads)
    (tmp_path / "other").mkdir()
    save_workspace(DeveloperWorkspace(id="other", name="Other", path=str(tmp_path / "other")))
    observed: dict = {}
    monkeypatch.setattr(agent_runner, "_invoke_agent", _helper_dispatch(observed))

    run = agent_runner.spawn_agent_run(
        "Review the other folder.", parent_thread_id=chat, profile="review",
        developer_workspace_id="other", enabled_tool_names=_CODE_CHAT_TOOLS, wait=True, timeout=10,
    )

    assert run["status"] == "completed"
    assert "developer" not in observed["tools"]
