from __future__ import annotations

import importlib
import pathlib
import subprocess
import sys
from types import SimpleNamespace


# In import order: edits must bind the reloaded change_ledger's FileChange.
_FRESH_MODULES = (
    "row_bot.developer.executables",
    "row_bot.developer.storage",
    "row_bot.developer.tool_context",
    "row_bot.developer.change_ledger",
    "row_bot.developer.edits",
    "row_bot.developer.sandbox_runtime",
    "row_bot.tools.developer_tool",
)


def _fresh_modules(tmp_path, reload_for_data_dir):
    _executables, storage, tool_context, change_ledger, edits, sandbox_runtime, developer_tool = reload_for_data_dir(
        tmp_path / "data", *_FRESH_MODULES
    )
    return storage, tool_context, edits, change_ledger, sandbox_runtime, developer_tool


def _init_repo(path):
    path.mkdir()
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test User"], check=True)


def test_developer_native_tools_read_search_and_status(tmp_path, monkeypatch, reload_for_data_dir):
    storage, tool_context, _edits, _ledger, _sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("Hello Developer\n", encoding="utf-8")
    workspace = storage.add_or_update_local_workspace(str(repo))
    thread_id = storage.ensure_workspace_thread(workspace.id)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    try:
        assert "Workspace:" in developer_tool._workspace_info()
        assert "README.md" in developer_tool._list_files()
        assert "Hello Developer" in developer_tool._read_file("README.md")
        assert "README.md:1:Hello Developer" in developer_tool._search("Developer")
        assert '"is_git": true' in developer_tool._git_status().lower()
    finally:
        tool_context.reset_context(tokens)


def test_developer_runtime_treats_quoted_code_as_running_code():
    from row_bot.developer.runtime import classify_command_action, has_shell_control_operator

    local_markdown_parser = (
        'python -c "from pathlib import Path; '
        "print('\\n'.join(line for line in Path('README.md').read_text().splitlines() "
        "if line.strip().startswith('>')))" + '"'
    )

    assert has_shell_control_operator(local_markdown_parser) is False
    assert classify_command_action(local_markdown_parser) == "run_command"
    assert has_shell_control_operator('python -c "print(1)" > out.txt') is True
    assert classify_command_action('python -c "print(1)" > out.txt') == "run_network"
    assert classify_command_action("curl https://example.com") == "run_network"


def test_developer_patch_records_and_reverts_agent_change(tmp_path, monkeypatch, reload_for_data_dir):
    storage, tool_context, _edits, change_ledger, _sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("Hello\n", encoding="utf-8")
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_approval_mode(workspace.id, "auto_edit")
    thread_id = storage.ensure_workspace_thread(workspace.id)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-Hello
+Hello Developer
"""
    try:
        result = developer_tool._apply_patch(patch, "Update README")
        assert "Applied patch as change set" in result
        assert (repo / "README.md").read_text(encoding="utf-8") == "Hello Developer\n"
        changes = change_ledger.list_change_sets(workspace_id=workspace.id, thread_id=thread_id)
        assert len(changes) == 1
        assert changes[0].files[0].path == "README.md"
        reverted = developer_tool._revert_change_set(changes[0].id)
        assert "Reverted" in reverted
        assert (repo / "README.md").read_text(encoding="utf-8") == "Hello\n"
    finally:
        tool_context.reset_context(tokens)


def test_developer_write_file_records_agent_change(tmp_path, monkeypatch, reload_for_data_dir):
    storage, tool_context, _edits, change_ledger, _sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_approval_mode(workspace.id, "auto_edit")
    thread_id = storage.ensure_workspace_thread(workspace.id)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    try:
        result = developer_tool._write_file("tests/test_example.py", "def test_ok():\n    assert True\n", "Add test")
        assert "change set" in result
        assert (repo / "tests" / "test_example.py").exists()
        changes = change_ledger.list_change_sets(workspace_id=workspace.id, thread_id=thread_id)
        assert changes[0].files[0].path == "tests/test_example.py"
    finally:
        tool_context.reset_context(tokens)


def test_developer_ordinary_write_policy_allows_ask_and_auto_but_blocks_block(
    tmp_path,
    monkeypatch,
    reload_for_data_dir,
):
    storage, _tool_context, edits, _ledger, _sandbox_runtime, _developer_tool = _fresh_modules(
        tmp_path,
        reload_for_data_dir,
    )
    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))

    for mode, path in (("approve", "ask.txt"), ("allow_all", "auto.txt")):
        change_set, decision = edits.write_file_to_workspace(
            workspace_id=workspace.id,
            thread_id="thread-ordinary",
            path=path,
            content=f"{mode}\n",
            approval_mode=mode,
        )
        assert decision.allowed is True
        assert decision.requires_approval is False
        assert change_set is not None
        assert (repo / path).read_text(encoding="utf-8") == f"{mode}\n"

    blocked, decision = edits.write_file_to_workspace(
        workspace_id=workspace.id,
        thread_id="thread-ordinary",
        path="blocked.txt",
        content="must not write\n",
        approval_mode="block",
    )
    assert blocked is None
    assert decision.decision == "block"
    assert not (repo / "blocked.txt").exists()


def test_developer_ordinary_patch_in_ask_mode_does_not_request_approval(
    tmp_path,
    monkeypatch,
    reload_for_data_dir,
):
    storage, _tool_context, edits, _ledger, _sandbox_runtime, _developer_tool = _fresh_modules(
        tmp_path,
        reload_for_data_dir,
    )
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    workspace = storage.add_or_update_local_workspace(str(repo))
    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-before
+after
"""

    change_set, decision = edits.apply_patch_to_workspace(
        workspace_id=workspace.id,
        thread_id="thread-ordinary",
        patch=patch,
        approval_mode="approve",
    )

    assert decision.allowed is True
    assert decision.requires_approval is False
    assert change_set is not None
    assert (repo / "README.md").read_text(encoding="utf-8") == "after\n"


def test_developer_non_git_workspace_writes_and_surfaces_changed_path(
    tmp_path,
    monkeypatch,
    reload_for_data_dir,
):
    storage, tool_context, _edits, change_ledger, _sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    folder = tmp_path / "budget-calc"
    folder.mkdir()
    import row_bot.developer.git as developer_git

    monkeypatch.setattr(
        developer_git,
        "get_git_status",
        lambda path: developer_git.GitStatus(path=str(path), is_git=False),
    )
    workspace = storage.add_or_update_local_workspace(str(folder))
    storage.set_workspace_approval_mode(workspace.id, "auto_edit")
    thread_id = storage.ensure_workspace_thread(workspace.id)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    try:
        result = developer_tool._write_file(
            "index.html",
            "<h1>Budget calculator</h1>\n",
            "Create budget calculator",
        )
        assert "change set" in result
        assert (folder / "index.html").read_text(encoding="utf-8") == (
            "<h1>Budget calculator</h1>\n"
        )
        [change_set] = change_ledger.list_change_sets(
            workspace_id=workspace.id,
            thread_id=thread_id,
        )
        assert change_set.files[0].path == "index.html"

        from row_bot.developer.inspector_snapshot import _collect_snapshot_sync

        snapshot = _collect_snapshot_sync(workspace.id, thread_id)
        assert snapshot.git_summary["is_git"] is False
        assert [item.files[0].path for item in snapshot.agent_changes] == [
            "index.html"
        ]
    finally:
        tool_context.reset_context(tokens)


def test_developer_write_file_uses_docker_shadow_when_enabled(tmp_path, monkeypatch, reload_for_data_dir):
    storage, tool_context, _edits, _ledger, sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker")
    storage.set_workspace_approval_mode(workspace.id, "auto_edit")
    workspace = storage.get_workspace(workspace.id)
    thread_id = storage.ensure_workspace_thread(workspace.id)

    monkeypatch.setattr(
        sandbox_runtime,
        "detect_container_runtime",
        lambda: sandbox_runtime.SandboxProbe(True, binary="docker", version="Docker version test"),
    )
    docker_state = {"exists": False, "shadow": ""}

    def fake_run(args, **_kwargs):
        if args[:3] == ["docker", "inspect", "-f"] and "{{.HostConfig.NetworkMode}}" in args:
            return SimpleNamespace(returncode=0 if docker_state["exists"] else 1, stdout="none\n" if docker_state["exists"] else "", stderr="")
        if args[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0 if docker_state["exists"] else 1, stdout="true\n" if docker_state["exists"] else "", stderr="")
        if args[:2] == ["docker", "run"]:
            docker_state["shadow"] = args[args.index("-v") + 1].split(":/workspace", 1)[0]
            docker_state["exists"] = True
            return SimpleNamespace(returncode=0, stdout="container\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    try:
        result = developer_tool._write_file("sandbox_only.txt", "sandbox hello\n", "Create sandbox probe")
    finally:
        tool_context.reset_context(tokens)

    pending = sandbox_runtime.list_pending_changes(workspace_id=workspace.id, thread_id=thread_id)

    assert "Docker Sandbox as pending change" in result
    assert pending and pending[0].files == ["sandbox_only.txt"]
    assert not (repo / "sandbox_only.txt").exists()


def test_developer_apply_patch_uses_docker_shadow_when_enabled(tmp_path, monkeypatch, reload_for_data_dir):
    storage, tool_context, _edits, _ledger, sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker")
    storage.set_workspace_approval_mode(workspace.id, "auto_edit")
    workspace = storage.get_workspace(workspace.id)
    thread_id = storage.ensure_workspace_thread(workspace.id)

    monkeypatch.setattr(
        sandbox_runtime,
        "detect_container_runtime",
        lambda: sandbox_runtime.SandboxProbe(True, binary="docker", version="Docker version test"),
    )
    real_run = subprocess.run
    docker_state = {"exists": False, "shadow": ""}

    def fake_run(args, **kwargs):
        if args[:2] == ["git", "apply"]:
            return real_run(args, **kwargs)
        if args[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0 if docker_state["exists"] else 1, stdout="true\n" if docker_state["exists"] else "", stderr="")
        if args[:2] == ["docker", "run"]:
            docker_state["shadow"] = args[args.index("-v") + 1].split(":/workspace", 1)[0]
            docker_state["exists"] = True
            return SimpleNamespace(returncode=0, stdout="container\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)
    patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-before
+after
"""
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    try:
        result = developer_tool._apply_patch(patch, "Sandbox patch")
    finally:
        tool_context.reset_context(tokens)

    pending = sandbox_runtime.list_pending_changes(workspace_id=workspace.id, thread_id=thread_id)

    assert "Docker Sandbox as pending change" in result
    assert pending and pending[0].files == ["README.md"]
    assert (repo / "README.md").read_text(encoding="utf-8") == "before\n"


def test_developer_tool_exposes_write_and_command_tools(tmp_path, monkeypatch, reload_for_data_dir):
    _storage, _tool_context, _edits, _ledger, _sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    tool_names = {tool.name for tool in developer_tool.DeveloperTool().as_langchain_tools()}
    assert "developer_write_file" in tool_names
    assert "developer_run_command" in tool_names
    assert "developer_import_sandbox_changes" in tool_names
    assert "developer_create_branch" in tool_names
    assert "developer_switch_branch" in tool_names
    assert "developer_commit_changes" in tool_names
    assert "developer_push_current_branch" in tool_names
    assert "developer_fast_forward_merge" in tool_names


def test_status_reports_developer_enabled_for_new_install_when_active(tmp_path, monkeypatch, reload_for_data_dir):
    storage, tool_context, _edits, _ledger, _sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    sys.modules.pop("tools.row_bot_status_tool", None)
    import row_bot.tools.row_bot_status_tool as row_bot_status_tool

    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))
    thread_id = storage.ensure_workspace_thread(workspace.id)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    try:
        output = row_bot_status_tool._query_tools()
        assert "Developer" in output
        assert "contextual: Developer" not in output
    finally:
        tool_context.reset_context(tokens)


def test_developer_guides_and_skills_are_bundled():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    assert (root / "tool_guides" / "developer_guide" / "SKILL.md").exists()
    assert (root / "bundled_skills" / "developer_coding" / "SKILL.md").exists()
    assert (root / "bundled_skills" / "developer_review" / "SKILL.md").exists()
    assert (root / "bundled_skills" / "developer_pr_prep" / "SKILL.md").exists()
    assert (root / "bundled_skills" / "developer_custom_tools" / "SKILL.md").exists()


def test_developer_skill_prompt_is_scoped_to_developer_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    sys.modules.pop("skills", None)

    import row_bot.skills as skills
    importlib.reload(skills)
    skills.load_skills()

    regular_prompt = skills.get_skills_prompt([], active_tool_names=[])
    developer_prompt = skills.get_skills_prompt([], active_tool_names=["developer"])

    assert "Developer Tool Guide" not in regular_prompt
    assert "Developer Tool Guide" in developer_prompt
    assert "Developer Coding" in developer_prompt
    assert "Developer Review" in developer_prompt
    assert "Developer PR Prep" in developer_prompt
    assert "Developer Custom Tools" in developer_prompt
    assert "custom_tool_builder" in developer_prompt
    assert "developer_custom_tool_builder" not in developer_prompt

    custom_tool_prompt = skills.get_skills_prompt([], active_tool_names=["custom_tool_builder"])
    assert "Custom Tool Builder Guide" in custom_tool_prompt
    assert "Use `custom_tool_builder` for lifecycle state" in custom_tool_prompt
    assert "Shell can help with extra read-only inspection" in custom_tool_prompt
    assert "Do not use shell to manually register" in custom_tool_prompt


def test_custom_tool_request_does_not_fall_back_to_shell_when_builder_disabled():
    import row_bot.agent as agent

    config = {"configurable": {"thread_id": "test-custom-tool-disabled"}}
    prompt = "Turn https://github.com/github/gitignore into a Custom Tool"

    invoked = agent.invoke_agent(prompt, ["read_url", "shell"], config)
    events = list(agent.stream_agent(prompt, ["read_url", "shell"], config))

    assert "Custom Tool Builder is off" in invoked
    assert "read_url or shell commands" in invoked
    assert events == [("token", invoked), ("done", invoked)]


def test_custom_tool_request_allows_builder_when_enabled(monkeypatch):
    import row_bot.agent as agent

    prompt = "Turn https://github.com/github/gitignore into a Custom Tool"
    assert agent._custom_tool_builder_disabled_response(
        prompt,
        ["read_url", "shell", "custom_tool_builder"],
    ) is None


def test_developer_snapshot_noops_do_not_advance_version(tmp_path, monkeypatch, reload_for_data_dir):
    storage, _tool_context, _edits, _ledger, _sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    import row_bot.developer.inspector_snapshot as inspector_snapshot

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("Hello\n", encoding="utf-8")
    workspace = storage.add_or_update_local_workspace(str(repo))
    thread_id = storage.ensure_workspace_thread(workspace.id)

    first = inspector_snapshot.refresh_snapshot_for_tests(workspace.id, thread_id)
    second = inspector_snapshot.refresh_snapshot_for_tests(workspace.id, thread_id)

    assert first.version == second.version


def test_developer_patch_rejects_path_traversal(tmp_path, monkeypatch, reload_for_data_dir):
    storage, tool_context, _edits, _ledger, _sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))
    thread_id = storage.ensure_workspace_thread(workspace.id)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    bad_patch = """diff --git a/../outside.txt b/../outside.txt
--- a/../outside.txt
+++ b/../outside.txt
@@ -0,0 +1 @@
+bad
"""
    try:
        try:
            developer_tool._preview_patch(bad_patch)
        except ValueError as exc:
            assert "escapes workspace" in str(exc)
        else:
            raise AssertionError("path traversal patch should be rejected")
    finally:
        tool_context.reset_context(tokens)


def test_developer_workspace_persists_execution_settings(tmp_path, monkeypatch, reload_for_data_dir):
    storage, _tool_context, _edits, _ledger, _sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))

    updated = storage.set_workspace_execution_settings(
        workspace.id,
        execution_mode="docker",
        sandbox_network="ask",
        sandbox_image="example/dev:latest",
    )
    reloaded = storage.get_workspace(workspace.id)

    assert updated.execution_mode == "docker"
    assert reloaded.execution_mode == "docker"
    assert reloaded.sandbox_network == "ask"
    assert reloaded.sandbox_image == "example/dev:latest"


def test_developer_executable_resolver_finds_standard_windows_installs(tmp_path, monkeypatch, reload_for_data_dir):
    _storage, _tool_context, _edits, _ledger, _sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    import row_bot.developer.executables as executables

    def fake_which(_name):
        return None

    existing = {
        pathlib.Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"),
        pathlib.Path(r"C:\Program Files\GitHub CLI\gh.exe"),
    }

    monkeypatch.setattr(executables.shutil, "which", fake_which)
    monkeypatch.setattr(executables, "_is_windows", lambda: True)
    monkeypatch.setattr(executables.os, "environ", {
        "ProgramFiles": r"C:\Program Files",
        "ProgramFiles(x86)": r"C:\Program Files (x86)",
        "LOCALAPPDATA": r"C:\Users\Test\AppData\Local",
    })
    monkeypatch.setattr(executables.Path, "exists", lambda self: self in existing)
    monkeypatch.setattr(executables.Path, "is_file", lambda self: self in existing)

    assert executables.resolve_docker().endswith(r"Docker\Docker\resources\bin\docker.exe")
    assert executables.resolve_github_cli().endswith(r"GitHub CLI\gh.exe")


def test_developer_executable_resolver_finds_macos_gui_installs(tmp_path, monkeypatch, reload_for_data_dir):
    _storage, _tool_context, _edits, _ledger, _sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    import row_bot.developer.executables as executables

    def fake_which(_name):
        return None

    existing = {
        pathlib.Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
        pathlib.Path("/opt/homebrew/bin/gh"),
        pathlib.Path("/opt/homebrew/bin/podman"),
    }

    monkeypatch.setattr(executables.shutil, "which", fake_which)
    monkeypatch.setattr(executables, "_is_windows", lambda: False)
    monkeypatch.setattr(executables.Path, "exists", lambda self: self in existing)
    monkeypatch.setattr(executables.Path, "is_file", lambda self: self in existing)

    assert executables.resolve_docker() == "/Applications/Docker.app/Contents/Resources/bin/docker"
    assert executables.resolve_github_cli() == "/opt/homebrew/bin/gh"
    assert executables.resolve_podman() == "/opt/homebrew/bin/podman"


def test_docker_runtime_reports_installed_but_engine_inaccessible(tmp_path, monkeypatch, reload_for_data_dir):
    _storage, _tool_context, _edits, _ledger, sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)

    monkeypatch.setattr(sandbox_runtime, "resolve_docker", lambda: r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    monkeypatch.setattr(sandbox_runtime, "resolve_podman", lambda: "")

    def fake_run(args, **_kwargs):
        if args[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="Docker version 29.4.2, build test\n", stderr="")
        if args[1:3] == ["info", "--format"]:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="permission denied while trying to connect to the docker API at npipe:////./pipe/docker_engine\n",
            )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)

    probe = sandbox_runtime.detect_container_runtime()

    assert probe.available is False
    assert probe.binary.endswith(r"Docker\Docker\resources\bin\docker.exe")
    assert "Docker version 29.4.2" in probe.version
    assert "engine is not accessible" in probe.message
    assert "permission denied" in probe.message


def test_docker_runtime_reports_stopped_docker_desktop_clearly(tmp_path, monkeypatch, reload_for_data_dir):
    _storage, _tool_context, _edits, _ledger, sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)

    monkeypatch.setattr(sandbox_runtime, "resolve_docker", lambda: r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    monkeypatch.setattr(sandbox_runtime, "resolve_podman", lambda: "")

    def fake_run(args, **_kwargs):
        if args[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="Docker version 29.4.2, build test\n", stderr="")
        if args[1:3] == ["info", "--format"]:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=(
                    "error during connect: Get "
                    "\"http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.51/info\": "
                    "open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified."
                ),
            )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)

    probe = sandbox_runtime.detect_container_runtime()

    assert probe.available is False
    assert "Docker Desktop is installed but not running" in probe.message
    assert "file specified" not in probe.message


def test_docker_sandbox_missing_runtime_does_not_run_or_touch_repo(tmp_path, monkeypatch, reload_for_data_dir):
    storage, _tool_context, _edits, _ledger, sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker")
    workspace = storage.get_workspace(workspace.id)

    monkeypatch.setattr(
        sandbox_runtime,
        "detect_container_runtime",
        lambda: sandbox_runtime.SandboxProbe(False, message="not installed"),
    )

    outcome = sandbox_runtime.run_docker_sandbox_command(workspace, "echo test", thread_id="thread")

    assert outcome.returncode is None
    assert "not installed" in outcome.stderr
    assert (repo / "README.md").read_text(encoding="utf-8") == "before\n"


def test_docker_sandbox_missing_image_fails_before_container_run(tmp_path, monkeypatch, reload_for_data_dir):
    storage, _tool_context, _edits, _ledger, sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker", sandbox_image="missing/image:latest")
    workspace = storage.get_workspace(workspace.id)

    monkeypatch.setattr(
        sandbox_runtime,
        "detect_container_runtime",
        lambda: sandbox_runtime.SandboxProbe(True, binary="docker", version="Docker version test"),
    )
    calls = {"run": 0}

    def fake_run(args, **_kwargs):
        if args[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="No such image")
        if args[:2] == ["docker", "run"]:
            calls["run"] += 1
            return SimpleNamespace(returncode=1, stdout="", stderr="should not run")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)

    outcome = sandbox_runtime.run_docker_sandbox_command(workspace, "python --version", thread_id="thread")

    assert outcome.returncode is None
    assert "not available locally" in outcome.stderr
    assert "docker pull missing/image:latest" in outcome.stderr
    assert calls["run"] == 0


def test_docker_sandbox_status_requires_configured_image(tmp_path, monkeypatch, reload_for_data_dir):
    storage, _tool_context, _edits, _ledger, sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker", sandbox_image="missing/image:latest")
    workspace = storage.get_workspace(workspace.id)

    monkeypatch.setattr(
        sandbox_runtime,
        "detect_container_runtime",
        lambda: sandbox_runtime.SandboxProbe(True, binary="docker", version="Docker version test"),
    )

    def fake_run(args, **_kwargs):
        if args[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="No such image")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)

    status = sandbox_runtime.get_docker_sandbox_status(workspace)

    assert status.available is False
    assert "not available locally" in status.message


def test_docker_network_off_blocks_network_commands_before_docker(tmp_path, monkeypatch, reload_for_data_dir):
    storage, tool_context, _edits, _ledger, sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker", sandbox_network="off")
    storage.set_workspace_approval_mode(workspace.id, "agent_run")
    workspace = storage.get_workspace(workspace.id)
    thread_id = storage.ensure_workspace_thread(workspace.id)

    def fail_if_docker_starts(*_args, **_kwargs):
        raise AssertionError("Network Off should block network commands before Docker starts")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fail_if_docker_starts)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    try:
        result = developer_tool._run_command("python -c \"import urllib.request; urllib.request.urlopen('https://example.com')\"")
    finally:
        tool_context.reset_context(tokens)

    assert "Docker Sandbox network is Off" in result
    assert '"returncode": null' in result


def test_docker_network_off_blocks_package_installs_before_docker(tmp_path, monkeypatch, reload_for_data_dir):
    storage, tool_context, _edits, _ledger, sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker", sandbox_network="off")
    storage.set_workspace_approval_mode(workspace.id, "agent_run")
    workspace = storage.get_workspace(workspace.id)
    thread_id = storage.ensure_workspace_thread(workspace.id)

    def fail_if_docker_starts(*_args, **_kwargs):
        raise AssertionError("Network Off should block package installs before Docker starts")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fail_if_docker_starts)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    try:
        result = developer_tool._run_command("python -m pip install requests")
    finally:
        tool_context.reset_context(tokens)

    assert "Docker Sandbox network is Off" in result
    assert "package install" in result
    assert '"returncode": null' in result


def test_docker_sandbox_recreates_container_when_network_policy_changes(tmp_path, monkeypatch, reload_for_data_dir):
    storage, _tool_context, _edits, _ledger, sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker", sandbox_network="off", sandbox_image="local/image:latest")
    workspace = storage.get_workspace(workspace.id)

    monkeypatch.setattr(
        sandbox_runtime,
        "detect_container_runtime",
        lambda: sandbox_runtime.SandboxProbe(True, binary="docker", version="Docker version test"),
    )
    calls = {"rm": 0, "run": []}

    def fake_run(args, **_kwargs):
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="image\n", stderr="")
        if args[:3] == ["docker", "inspect", "-f"] and "{{.State.Running}}" in args:
            return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
        if args[:3] == ["docker", "inspect", "-f"] and "{{.HostConfig.NetworkMode}}" in args:
            return SimpleNamespace(returncode=0, stdout="bridge\n", stderr="")
        if args[:3] == ["docker", "rm", "-f"]:
            calls["rm"] += 1
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "run"]:
            calls["run"].append(args)
            return SimpleNamespace(returncode=0, stdout="container\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)

    sandbox_runtime.ensure_docker_sandbox(workspace)

    assert calls["rm"] == 1
    assert calls["run"]
    assert "--network" in calls["run"][0]
    assert "none" in calls["run"][0]


def test_developer_write_file_in_docker_mode_requires_verified_sandbox(tmp_path, monkeypatch, reload_for_data_dir):
    storage, tool_context, _edits, _ledger, sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker")
    storage.set_workspace_approval_mode(workspace.id, "auto_edit")
    workspace = storage.get_workspace(workspace.id)
    thread_id = storage.ensure_workspace_thread(workspace.id)

    def fail_docker_probe(*_args, **_kwargs):
        raise RuntimeError("Docker image probe failed")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fail_docker_probe)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    try:
        result = developer_tool._write_file("sandbox_only.txt", "sandbox hello\n", "Create sandbox probe")
    finally:
        tool_context.reset_context(tokens)

    pending = sandbox_runtime.list_pending_changes(workspace_id=workspace.id, thread_id=thread_id)

    assert "Docker Sandbox is not available" in result
    assert not pending
    assert not (repo / "sandbox_only.txt").exists()


def test_docker_sandbox_records_pending_patch_without_touching_host(tmp_path, monkeypatch, reload_for_data_dir):
    storage, _tool_context, _edits, _ledger, sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker")
    workspace = storage.get_workspace(workspace.id)

    monkeypatch.setattr(
        sandbox_runtime,
        "detect_container_runtime",
        lambda: sandbox_runtime.SandboxProbe(True, binary="docker", version="Docker version test"),
    )

    docker_state = {"exists": False, "shadow": "", "runs": 0, "execs": 0}

    def fake_run(args, **_kwargs):
        if args[:3] == ["docker", "inspect", "-f"] and "{{.HostConfig.NetworkMode}}" in args:
            return SimpleNamespace(returncode=0 if docker_state["exists"] else 1, stdout="none\n" if docker_state["exists"] else "", stderr="")
        if args[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0 if docker_state["exists"] else 1, stdout="true\n" if docker_state["exists"] else "", stderr="")
        if args[:2] == ["docker", "run"]:
            mount_arg = args[args.index("-v") + 1]
            docker_state["shadow"] = mount_arg.split(":/workspace", 1)[0]
            docker_state["exists"] = True
            docker_state["runs"] += 1
            return SimpleNamespace(returncode=0, stdout="container\n", stderr="")
        if args[:2] == ["docker", "exec"]:
            docker_state["execs"] += 1
            (pathlib.Path(docker_state["shadow"]) / "README.md").write_text("after\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)

    outcome = sandbox_runtime.run_docker_sandbox_command(workspace, "python edit.py", thread_id="thread")
    pending = sandbox_runtime.list_pending_changes(workspace_id=workspace.id, thread_id="thread")

    assert outcome.returncode == 0
    assert outcome.pending_change_id
    assert outcome.changed_files == ["README.md"]
    assert pending[0].id == outcome.pending_change_id
    assert "-before" in pending[0].patch
    assert "+after" in pending[0].patch
    assert (repo / "README.md").read_text(encoding="utf-8") == "before\n"
    assert docker_state["runs"] == 1
    assert docker_state["execs"] == 1


def test_docker_sandbox_reuses_persistent_container(tmp_path, monkeypatch, reload_for_data_dir):
    storage, _tool_context, _edits, _ledger, sandbox_runtime, _developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker")
    workspace = storage.get_workspace(workspace.id)

    monkeypatch.setattr(
        sandbox_runtime,
        "detect_container_runtime",
        lambda: sandbox_runtime.SandboxProbe(True, binary="docker", version="Docker version test"),
    )
    docker_state = {"exists": False, "shadow": "", "runs": 0, "execs": 0}

    def fake_run(args, **_kwargs):
        if args[:3] == ["docker", "inspect", "-f"] and "{{.HostConfig.NetworkMode}}" in args:
            return SimpleNamespace(returncode=0 if docker_state["exists"] else 1, stdout="none\n" if docker_state["exists"] else "", stderr="")
        if args[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0 if docker_state["exists"] else 1, stdout="true\n" if docker_state["exists"] else "", stderr="")
        if args[:2] == ["docker", "run"]:
            docker_state["shadow"] = args[args.index("-v") + 1].split(":/workspace", 1)[0]
            docker_state["exists"] = True
            docker_state["runs"] += 1
            return SimpleNamespace(returncode=0, stdout="container\n", stderr="")
        if args[:2] == ["docker", "exec"]:
            docker_state["execs"] += 1
            return SimpleNamespace(returncode=0, stdout=f"run {docker_state['execs']}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)

    first = sandbox_runtime.run_docker_sandbox_command(workspace, "echo one", thread_id="thread")
    second = sandbox_runtime.run_docker_sandbox_command(workspace, "echo two", thread_id="thread")

    assert first.returncode == 0
    assert second.returncode == 0
    assert docker_state["runs"] == 1
    assert docker_state["execs"] == 2


def test_import_sandbox_changes_applies_patch_to_host_workspace(tmp_path, monkeypatch, reload_for_data_dir):
    storage, tool_context, _edits, _ledger, sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker")
    storage.set_workspace_approval_mode(workspace.id, "auto_edit")
    workspace = storage.get_workspace(workspace.id)
    thread_id = storage.ensure_workspace_thread(workspace.id)

    monkeypatch.setattr(
        sandbox_runtime,
        "detect_container_runtime",
        lambda: sandbox_runtime.SandboxProbe(True, binary="docker", version="Docker version test"),
    )

    real_subprocess_run = subprocess.run

    docker_state = {"exists": False, "shadow": ""}

    def fake_run(args, **kwargs):
        if args[:2] == ["git", "apply"]:
            return real_subprocess_run(args, **kwargs)
        if args[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0 if docker_state["exists"] else 1, stdout="true\n" if docker_state["exists"] else "", stderr="")
        if args[:2] == ["docker", "run"]:
            mount_arg = args[args.index("-v") + 1]
            docker_state["shadow"] = mount_arg.split(":/workspace", 1)[0]
            docker_state["exists"] = True
            return SimpleNamespace(returncode=0, stdout="container\n", stderr="")
        if args[:2] == ["docker", "exec"]:
            (pathlib.Path(docker_state["shadow"]) / "README.md").write_text("after\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)
    outcome = sandbox_runtime.run_docker_sandbox_command(workspace, "python edit.py", thread_id=thread_id)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    try:
        result = developer_tool._import_sandbox_changes(outcome.pending_change_id, "Import sandbox README edit")
    finally:
        tool_context.reset_context(tokens)

    assert "Imported sandbox change" in result
    assert (repo / "README.md").read_text(encoding="utf-8") == "after\n"
    assert sandbox_runtime.get_pending_change(outcome.pending_change_id).imported is True


def test_importing_the_newest_sandbox_change_brings_in_the_earlier_ones_first(tmp_path, monkeypatch,
                                                                               reload_for_data_dir):
    """B303: sandbox changes build on each other; importing the latest no longer fails with a git error."""
    storage, tool_context, _edits, _ledger, sandbox_runtime, developer_tool = _fresh_modules(tmp_path, reload_for_data_dir)
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    workspace = storage.add_or_update_local_workspace(str(repo))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker")
    storage.set_workspace_approval_mode(workspace.id, "auto_edit")
    workspace = storage.get_workspace(workspace.id)
    thread_id = storage.ensure_workspace_thread(workspace.id)
    monkeypatch.setattr(sandbox_runtime, "detect_container_runtime",
                        lambda: sandbox_runtime.SandboxProbe(True, binary="docker", version="Docker version test"))
    real_subprocess_run = subprocess.run
    docker = {"exists": False, "shadow": "", "writes": ["after\n", "after\nand more\n"]}

    def fake_run(args, **kwargs):
        if args[:2] == ["git", "apply"]:
            return real_subprocess_run(args, **kwargs)
        if args[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0 if docker["exists"] else 1, stdout="true\n" if docker["exists"] else "",
                                   stderr="")
        if args[:2] == ["docker", "run"]:
            docker["shadow"] = args[args.index("-v") + 1].split(":/workspace", 1)[0]
            docker["exists"] = True
            return SimpleNamespace(returncode=0, stdout="container\n", stderr="")
        if args[:2] == ["docker", "exec"]:
            (pathlib.Path(docker["shadow"]) / "README.md").write_text(docker["writes"].pop(0), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(sandbox_runtime.subprocess, "run", fake_run)
    first = sandbox_runtime.run_docker_sandbox_command(workspace, "python step1.py", thread_id=thread_id)
    second = sandbox_runtime.run_docker_sandbox_command(workspace, "python step2.py", thread_id=thread_id)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    try:
        result = developer_tool._import_sandbox_changes(second.pending_change_id, "Import the latest change")
    finally:
        tool_context.reset_context(tokens)

    assert (repo / "README.md").read_text(encoding="utf-8") == "after\nand more\n"
    assert result.index(first.pending_change_id) < result.index(second.pending_change_id)
    assert all(sandbox_runtime.get_pending_change(change).imported
               for change in (first.pending_change_id, second.pending_change_id))
