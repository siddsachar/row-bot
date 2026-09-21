from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.helpers.live_chat_parity import (
    AttemptBudget,
    FINAL_COMBINED_SCENARIO,
    LIVE_OPT_IN,
    LiveParitySafetyError,
    MAX_GENERATION_ATTEMPTS,
    SCENARIO_PROMPTS,
    assert_localhost_url,
    qualify_saved_calculator,
    resolve_profile_after_opt_in,
    validate_redacted_report,
)


ROOT = Path(__file__).resolve().parents[3]


def test_missing_opt_in_fails_before_profile_resolution() -> None:
    called = False

    def resolver() -> Path:
        nonlocal called
        called = True
        raise AssertionError("profile must not be resolved")

    with pytest.raises(LiveParitySafetyError, match="opt-in missing"):
        resolve_profile_after_opt_in({}, resolver)
    assert called is False


def test_runner_missing_opt_in_does_not_touch_candidate_profile(tmp_path: Path) -> None:
    candidate = tmp_path / "must-not-exist"
    environment = dict(os.environ)
    environment.pop(LIVE_OPT_IN, None)
    environment["ROW_BOT_DATA_DIR"] = str(candidate)
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests/e2e/live_chat_parity/run_live.py")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "live opt-in missing" in result.stderr
    assert candidate.exists() is False


def test_exact_opt_in_allows_profile_resolution(tmp_path: Path) -> None:
    assert resolve_profile_after_opt_in({LIVE_OPT_IN: "1"}, lambda: tmp_path) == tmp_path.resolve()


def test_attempt_budget_is_one_shot_and_never_exceeds_four() -> None:
    budget = AttemptBudget()
    for index, scenario in enumerate(SCENARIO_PROMPTS, start=1):
        assert budget.admit(scenario) == index
    with pytest.raises(LiveParitySafetyError, match="replay"):
        budget.admit(next(iter(SCENARIO_PROMPTS)))
    budget.limit = len(SCENARIO_PROMPTS)
    assert len(budget.admitted) <= MAX_GENERATION_ATTEMPTS
    budget.mark_uncertain(budget.admitted[-1])
    assert budget.can_retry(budget.admitted[-1]) is False


def test_single_remaining_attempt_admits_only_combined_scenario() -> None:
    budget = AttemptBudget(used_before=MAX_GENERATION_ATTEMPTS - 1)
    assert FINAL_COMBINED_SCENARIO in SCENARIO_PROMPTS
    assert budget.admit(FINAL_COMBINED_SCENARIO) == MAX_GENERATION_ATTEMPTS
    with pytest.raises(LiveParitySafetyError, match="cap reached"):
        budget.admit("shared_markdown")


def test_final_runner_path_has_one_generation_admission() -> None:
    path = ROOT / "tests/e2e/live_chat_parity/run_live.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_run_final_calculator_stop"
    )
    admissions = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "admit"
    ]
    assert len(admissions) == 1


def test_calculator_qualification_requires_saved_enablement(tmp_path: Path) -> None:
    implementation = ROOT / "src/row_bot/tools/calculator_tool.py"
    missing = qualify_saved_calculator(tmp_path / "absent.json", implementation)
    assert missing.qualified and missing.reason == "qualified"

    config = tmp_path / "tools_config.json"
    config.write_text(json.dumps({"tools": {"calculator": False}}), encoding="utf-8")
    disabled = qualify_saved_calculator(config, implementation)
    assert disabled.enabled is False
    assert disabled.qualified is False
    assert disabled.reason == "calculator_disabled"


def test_calculator_qualification_rejects_network_or_mutation(tmp_path: Path) -> None:
    implementation = tmp_path / "calculator_tool.py"
    implementation.write_text(
        "import requests\n"
        "class CalculatorTool:\n"
        " @property\n"
        " def enabled_by_default(self): return True\n"
        "def unsafe():\n"
        " open('x', 'w')\n"
        " return simple_eval('1')\n"
        "calculator = 'calculator'\n"
        "calculate = 'calculate'\n",
        encoding="utf-8",
    )
    result = qualify_saved_calculator(tmp_path / "absent.json", implementation)
    assert result.enabled is True
    assert result.local_read_only is False


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": "hidden"},
        {"nested": {"provider_output": "hidden"}},
        {"note": r"C:\\Users\\owner\\.row-bot"},
        {"note": "/home/owner/.row-bot"},
        {"note": next(iter(SCENARIO_PROMPTS.values()))},
    ],
)
def test_redacted_report_rejects_sensitive_fields(payload: dict) -> None:
    with pytest.raises(LiveParitySafetyError):
        validate_redacted_report(payload)


def test_redacted_report_accepts_only_structural_evidence() -> None:
    validate_redacted_report(
        {
            "scenario_id": "shared_markdown",
            "attempt": 1,
            "conversation_hash": "0123456789abcdef",
            "milestones_ms": [12.4, 20.1],
            "row_counts": {"react": 2, "nicegui": 2},
            "states": ["admitted", "streaming", "checkpointed"],
        }
    )


def test_live_runner_urls_are_loopback_only() -> None:
    assert_localhost_url("http://127.0.0.1:8123/app-v2/")
    for invalid in ("http://localhost:8123/", "http://0.0.0.0:8123/", "https://127.0.0.1/"):
        with pytest.raises(LiveParitySafetyError):
            assert_localhost_url(invalid)


def test_nicegui_live_open_selects_validation_conversation_before_composer() -> None:
    from tests.e2e.live_chat_parity.run_live import _open_nicegui

    events: list[str] = []

    class Locator:
        def __init__(self, name: str):
            self.name = name
            self.last = self

        def wait_for(self, **_kwargs) -> None:
            events.append(f"wait:{self.name}")

        def click(self) -> None:
            events.append(f"click:{self.name}")

    class Page:
        def goto(self, url: str, **_kwargs) -> None:
            events.append(f"goto:{url}")

        def locator(self, selector: str) -> Locator:
            assert selector == ".row-bot-main-shell"
            return Locator("shell")

        def get_by_text(self, title: str, *, exact: bool) -> Locator:
            assert title == "Validation" and exact is True
            return Locator("conversation")

        def get_by_placeholder(self, value: str) -> Locator:
            assert value == "Do anything…"
            return Locator("composer")

    _open_nicegui(Page(), "http://127.0.0.1:8123", "Validation")  # type: ignore[arg-type]
    assert events == [
        "goto:http://127.0.0.1:8123/",
        "wait:shell",
        "wait:conversation",
        "click:conversation",
        "wait:composer",
    ]


def test_live_capture_uses_the_rendered_react_workspace_root() -> None:
    from tests.e2e.live_chat_parity.run_live import REACT_CAPTURE_ROOT

    workspace = (ROOT / "frontend/src/features/shell/Workspace.tsx").read_text(
        encoding="utf-8"
    )
    assert REACT_CAPTURE_ROOT == '[data-testid="conversation-workspace"]'
    assert 'data-testid="conversation-workspace"' in workspace
    assert "conversation-view" not in workspace


def test_live_runner_stops_owned_child_before_temporary_log_cleanup() -> None:
    source = (ROOT / "tests/e2e/live_chat_parity/run_live.py").read_text(
        encoding="utf-8"
    )
    temporary = source.index("with tempfile.TemporaryDirectory")
    inner_shutdown = source.index(
        "graceful = _stop_owned(process, base, secret)", temporary
    )
    exception_handler = source.index("except LiveParitySafetyError", temporary)
    assert temporary < inner_shutdown < exception_handler


def test_prior_evidence_counts_attempts_and_selects_latest_validation_chat(tmp_path: Path) -> None:
    from tests.e2e.live_chat_parity.run_live import _recorded_live_state

    values = [
        ("live-1", 1, "React Chat Live Parity Validation 20260921T090000Z", "1" * 16),
        ("live-2", 2, "React Chat Live Parity Validation 20260921T100000Z", "2" * 16),
    ]
    for name, attempts, title, identity_hash in values:
        directory = tmp_path / name
        directory.mkdir()
        (directory / "live-report.json").write_text(
            json.dumps(
                {
                    "attempt_count": attempts,
                    "conversation_title": title,
                    "conversation_hash": identity_hash,
                    "conversation_created_at": "2026-09-21T10:00:00Z",
                }
            ),
            encoding="utf-8",
        )

    attempts, target = _recorded_live_state(tmp_path)

    assert attempts == 3
    assert target == {
        "title": values[-1][2],
        "conversation_hash": values[-1][3],
        "conversation_created_at": "2026-09-21T10:00:00Z",
    }


def test_nicegui_stop_probe_requires_computed_visibility_and_enabled_state() -> None:
    source = (ROOT / "tests/e2e/live_chat_parity/run_live.py").read_text(
        encoding="utf-8"
    )
    start = source.index("def _nicegui_shape")
    body = source[start : source.index("def _capture", start)]
    assert "node.disabled" in body
    assert "getComputedStyle(node)" in body
    assert "style.display !== 'none'" in body
    assert "node.getClientRects().length > 0" in body


def test_final_preflight_truthfully_identifies_reopened_validation_chat() -> None:
    source = (ROOT / "tests/e2e/live_chat_parity/run_live.py").read_text(
        encoding="utf-8"
    )
    assert "only the prior runner-owned validation conversation is reopened" in source
    assert "_ensure_ask_approval(page)" in source[source.index("def _open_react_existing") :]


def test_existing_live_chat_open_requires_prior_identity_hash() -> None:
    from tests.e2e.live_chat_parity.run_live import _open_react_existing
    from tests.helpers.live_chat_parity import transient_hash

    conversation_id = "3b9188b6-91a2-4f8e-a134-5a56bd73e4f0"
    events: list[str] = []

    class Locator:
        def __init__(self, name: str):
            self.name = name

        def wait_for(self, **_kwargs) -> None:
            events.append(f"wait:{self.name}")

        def click(self) -> None:
            events.append(f"click:{self.name}")
            page.url = f"http://127.0.0.1:8123/app-v2/conversations/{conversation_id}"

        def inner_text(self) -> str:
            return "Ask" if self.name == "button:Approvals" else "Configured model"

    class Page:
        url = ""

        def goto(self, url: str, **_kwargs) -> None:
            events.append(f"goto:{url}")

        def locator(self, selector: str) -> Locator:
            assert selector == ".home-connection-status"
            return Locator("connection")

        def get_by_role(self, role: str, *, name: str, exact: bool) -> Locator:
            assert exact is True
            return Locator(f"{role}:{name}")

        def wait_for_url(self, value: str, **_kwargs) -> None:
            assert value == "**/app-v2/conversations/*"

    page = Page()
    target = {
        "title": "React Chat Live Parity Validation 20260921T100000Z",
        "conversation_hash": transient_hash(conversation_id),
    }
    opened, model = _open_react_existing(  # type: ignore[arg-type]
        page,
        "http://127.0.0.1:8123",
        target,
    )
    assert opened == conversation_id
    assert model == "Configured model"
    assert events[:3] == [
        "goto:http://127.0.0.1:8123/app-v2/",
        "wait:connection",
        f"click:button:{target['title']}",
    ]


def test_live_flag_short_circuits_unrelated_app_autostarts() -> None:
    source = (ROOT / "src/row_bot/app.py").read_text(encoding="utf-8")
    start = source.index("async def _run_startup_sequence():")
    body = source[start : source.index("async def _launcher_ping_handler", start)]
    guard = body.index('live_chat_parity = os.environ.get("ROW_BOT_LIVE_CHAT_PARITY") == "1"')
    recovery = body.index("await application_lifecycle.startup()")
    early_return = body.index('if live_chat_parity:', recovery)
    mcp = body.index('discover_enabled_servers')
    channels = body.index('_load_channel_modules()')
    tunnel = body.index('tunnel_manager.start_tunnel')
    assert guard < recovery < early_return < mcp < channels < tunnel
    assert 'if not live_chat_parity:' in body[:recovery]


def test_live_flag_exposes_only_an_already_enabled_calculator(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from row_bot.tools import registry

    tools = [SimpleNamespace(name="calculator"), SimpleNamespace(name="web_search")]
    monkeypatch.setattr(registry, "get_all_tools", lambda: tools)
    monkeypatch.setattr(registry, "is_enabled", lambda name: name != "calculator")
    monkeypatch.setenv(LIVE_OPT_IN, "1")
    assert registry.get_enabled_tools() == []

    monkeypatch.setattr(registry, "is_enabled", lambda _name: True)
    assert [tool.name for tool in registry.get_enabled_tools()] == ["calculator"]
