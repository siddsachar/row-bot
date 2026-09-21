"""Run the bounded owner-profile NiceGUI/React live chat parity lane.

This script is intentionally separate from pytest and every default test
matrix.  It fails before profile resolution unless
``ROW_BOT_LIVE_CHAT_PARITY=1`` is present, never stores prompt or response
bodies, and owns exactly one loopback application child.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
from urllib.request import ProxyHandler, Request, build_opener
import uuid

import psutil
from playwright.sync_api import (
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.browser.client_foundation.run_browser import fingerprint  # noqa: E402
from tests.helpers.live_chat_parity import (  # noqa: E402
    AttemptBudget,
    FINAL_COMBINED_SCENARIO,
    LIVE_OPT_IN,
    LiveParitySafetyError,
    MAX_GENERATION_ATTEMPTS,
    SCENARIO_PROMPTS,
    VALIDATION_TITLE,
    assert_localhost_url,
    qualify_saved_calculator,
    require_live_opt_in,
    resolve_profile_after_opt_in,
    transient_hash,
    validate_redacted_report,
    write_redacted_json,
)


EVIDENCE = ROOT / ".local/evidence/unified-client-platform/react-chat-live-parity/live"
APP_ENTRY = ROOT / "app.py"
CALCULATOR = ROOT / "src/row_bot/tools/calculator_tool.py"
REACT_CAPTURE_ROOT = '[data-testid="conversation-workspace"]'


def _recorded_live_state(evidence: Path) -> tuple[int, dict[str, str] | None]:
    """Count prior real attempts and identify the latest runner-owned chat."""

    attempts = 0
    resume: dict[str, str] | None = None
    for path in sorted(evidence.glob("live-*/live-report.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            validate_redacted_report(document)
            count = document.get("attempt_count", 0)
            if type(count) is not int or not 0 <= count <= MAX_GENERATION_ATTEMPTS:
                raise ValueError
            uncertain = document.get("uncertain_attempts", 0)
            if type(uncertain) is not int or not 0 <= uncertain <= count:
                raise ValueError
            if uncertain:
                raise LiveParitySafetyError(
                    "prior uncertain live attempt forbids continuation"
                )
            attempts += count
            title = document.get("conversation_title")
            identity_hash = document.get("conversation_hash")
            created_at = document.get("conversation_created_at")
            if (
                isinstance(title, str)
                and title.startswith(VALIDATION_TITLE + " ")
                and isinstance(identity_hash, str)
                and re.fullmatch(r"[0-9a-f]{16}", identity_hash)
                and isinstance(created_at, str)
            ):
                resume = {
                    "title": title,
                    "conversation_hash": identity_hash,
                    "conversation_created_at": created_at,
                }
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise LiveParitySafetyError("prior live evidence is not safely readable") from exc
    if attempts > MAX_GENERATION_ATTEMPTS:
        raise LiveParitySafetyError("recorded live generation attempt cap exceeded")
    return attempts, resume


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _source_digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _wait_until(predicate: Callable[[], bool], *, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise LiveParitySafetyError(f"live milestone timed out: {label}")


def _process_is_row_bot(process: psutil.Process) -> bool:
    try:
        command = process.cmdline()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied as exc:
        try:
            name = process.name().casefold()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            name = ""
        if "python" in name:
            raise LiveParitySafetyError("python process inventory is not inspectable") from exc
        return False
    normalized = [str(part).replace("\\", "/").casefold() for part in command]
    return any(part.endswith("/app.py") and "row-bot" in part for part in normalized) or any(
        part in {"row_bot.launcher", "row_bot.app"} for part in normalized
    )


def _assert_profile_quiescent(profile: Path) -> None:
    state_path = profile / "launcher_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state_pid = int(state.get("pid") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise LiveParitySafetyError("launcher ownership state is unreadable") from exc
        if state_pid > 0 and psutil.pid_exists(state_pid):
            raise LiveParitySafetyError("another Row-Bot launcher owns the profile")

    for process in psutil.process_iter(["pid", "name"]):
        if process.pid == os.getpid():
            continue
        if _process_is_row_bot(process):
            raise LiveParitySafetyError("another Row-Bot application process is running")

    for database in sorted(profile.glob("*.db")):
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=0)
            connection.execute("PRAGMA busy_timeout=0")
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        except sqlite3.Error as exc:
            raise LiveParitySafetyError("exclusive profile database access is unavailable") from exc
        finally:
            if connection is not None:
                connection.close()


def _free_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_ready(process: subprocess.Popen[Any], base: str, secret: str) -> float:
    opener = build_opener(ProxyHandler({}))
    started = time.perf_counter()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LiveParitySafetyError("owned Row-Bot process exited before readiness")
        try:
            request = Request(
                base + "/api/launcher-ping",
                headers={"Authorization": f"Bearer {secret}"},
            )
            with opener.open(request, timeout=1) as response:
                payload = json.loads(response.read(512).decode("utf-8"))
                if response.status == 200 and payload.get("app") == "row-bot":
                    return round((time.perf_counter() - started) * 1000, 2)
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            time.sleep(0.2)
    raise LiveParitySafetyError("owned Row-Bot readiness timed out")


def _stop_owned(process: subprocess.Popen[Any] | None, base: str, secret: str) -> bool:
    if process is None or process.poll() is not None:
        return True
    opener = build_opener(ProxyHandler({}))
    try:
        request = Request(
            base + "/api/launcher-shutdown",
            data=b"",
            method="POST",
            headers={"Authorization": f"Bearer {secret}"},
        )
        with opener.open(request, timeout=5) as response:
            if not 200 <= response.status < 300:
                raise OSError("shutdown rejected")
        process.wait(timeout=20)
        return True
    except (OSError, TimeoutError, subprocess.TimeoutExpired):
        try:
            owner = psutil.Process(process.pid)
            children = owner.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        return False


def _safe_display(value: str, *, label: str) -> str:
    text = " ".join(value.split()).strip()
    if not text or len(text) > 180:
        raise LiveParitySafetyError(f"{label} display value is unavailable")
    validate_redacted_report({f"{label}_display": text})
    return text


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("content_copy", "").split())


def _message_metric(role: str, identity: str, text: str, structure: dict[str, int]) -> dict[str, Any]:
    normalized = _normalize_text(text)
    return {
        "role": role,
        "identity_hash": transient_hash(identity),
        "text_hash": transient_hash(normalized),
        "characters": len(normalized),
        **structure,
    }


def _react_shape(page: Page) -> dict[str, Any]:
    raw = page.evaluate(
        """() => {
          const semanticText = (root) => {
            if (!root) return '';
            const selector = 'h1,h2,h3,h4,h5,h6,p,li,pre,blockquote,th,td';
            const blocks = [...root.querySelectorAll(selector)]
              .filter(node => !node.querySelector(selector));
            const values = blocks.map(node => {
              const clone = node.cloneNode(true);
              clone.querySelectorAll('button,[role="status"],figcaption').forEach(child => child.remove());
              return clone.textContent || '';
            });
            if (values.length) return values.join('\\n');
            const clone = root.cloneNode(true);
            clone.querySelectorAll('button,[role="status"],figcaption').forEach(child => child.remove());
            return clone.textContent || '';
          };
          return ({
          rows: [...document.querySelectorAll('article.message')].map((row, index) => ({
            role: row.classList.contains('message-user') ? 'user' :
                  row.classList.contains('message-assistant') ? 'assistant' : 'tool',
            identity: row.getAttribute('data-message-id') || row.getAttribute('data-row-id') || String(index),
            text: semanticText(row.querySelector('.message-text')),
            headings: row.querySelectorAll('.message-text h1,.message-text h2,.message-text h3').length,
            lists: row.querySelectorAll('.message-text ul,.message-text ol').length,
            code: row.querySelectorAll('.message-text pre').length,
          })),
          traces: [...document.querySelectorAll('.trace-group')].map(node => ({
            status: node.getAttribute('data-trace-status') || 'unknown',
            collapsed: !node.hasAttribute('open'),
          })),
          buddy: document.querySelector('.buddy-avatar-frame')?.getAttribute('data-state') ||
                 document.querySelector('[aria-label="Buddy companion"]')?.getAttribute('data-state') || 'hidden',
          connected: (document.querySelector('.connection-status')?.textContent || '').includes('Connected'),
          stop_visible: !![...document.querySelectorAll('button')].find(node => node.getAttribute('aria-label') === 'Stop'),
          has_alert: document.querySelectorAll('[role="alert"]').length > 0,
          });
        }"""
    )
    rows = [
        _message_metric(
            str(row["role"]),
            str(row["identity"]),
            str(row["text"]),
            {
                "headings": int(row["headings"]),
                "lists": int(row["lists"]),
                "code_blocks": int(row["code"]),
            },
        )
        for row in raw["rows"]
    ]
    return {**raw, "rows": rows}


def _nicegui_shape(page: Page) -> dict[str, Any]:
    raw = page.evaluate(
        """() => {
          const semanticText = (root) => {
            if (!root) return '';
            const selector = 'h1,h2,h3,h4,h5,h6,p,li,pre,blockquote,th,td';
            const blocks = [...root.querySelectorAll(selector)]
              .filter(node => !node.querySelector(selector));
            const values = blocks.map(node => {
              const clone = node.cloneNode(true);
              clone.querySelectorAll('button,[role="status"],figcaption').forEach(child => child.remove());
              return clone.textContent || '';
            });
            if (values.length) return values.join('\\n');
            const clone = root.cloneNode(true);
            clone.querySelectorAll('button,[role="status"],figcaption').forEach(child => child.remove());
            return clone.textContent || '';
          };
          return ({
          rows: [...document.querySelectorAll('.row-bot-msg-row')].map((row, index) => ({
            role: row.classList.contains('row-bot-msg-row-user') ? 'user' : 'assistant',
            identity: row.getAttribute('data-message-id') || row.getAttribute('data-row-id') || String(index),
            text: semanticText(row.querySelector('.row-bot-msg')),
            headings: row.querySelectorAll('.row-bot-msg h1,.row-bot-msg h2,.row-bot-msg h3').length,
            lists: row.querySelectorAll('.row-bot-msg ul,.row-bot-msg ol').length,
            code: row.querySelectorAll('.row-bot-msg pre').length,
          })),
          traces: [...document.querySelectorAll('[data-docs-id="tool-trace"],[data-trace-status]')].map(node => ({
            status: node.getAttribute('data-trace-status') || 'settled',
            collapsed: !node.classList.contains('q-expansion-item--expanded'),
          })),
          buddy: document.querySelector('[data-buddy-state]')?.getAttribute('data-buddy-state') ||
                 document.querySelector('.row-bot-buddy-wrap')?.getAttribute('data-state') || 'hidden',
          connected: !document.body.innerText.includes('Disconnected'),
          stop_visible: (() => {
            const node = document.querySelector('.row-bot-composer-stop-button');
            if (!node || node.disabled || node.getAttribute('aria-disabled') === 'true') return false;
            const style = getComputedStyle(node);
            return style.display !== 'none' && style.visibility !== 'hidden' &&
                   style.opacity !== '0' && node.getClientRects().length > 0;
          })(),
          external_live: !!document.querySelector('[data-external-generation]'),
          has_alert: document.querySelectorAll('[role="alert"]').length > 0,
          });
        }"""
    )
    rows = [
        _message_metric(
            str(row["role"]),
            str(row["identity"]),
            str(row["text"]),
            {
                "headings": int(row["headings"]),
                "lists": int(row["lists"]),
                "code_blocks": int(row["code"]),
            },
        )
        for row in raw["rows"]
    ]
    return {**raw, "rows": rows}


def _capture(page: Page, client: str, destination: Path) -> str:
    name = destination.name
    if client == "react":
        root = page.locator(REACT_CAPTURE_ROOT)
        masks = [
            page.locator(".navigation"),
            page.locator(".message-text"),
            page.locator(".conversation-title-block"),
            page.locator("textarea,input"),
            page.locator("[data-sensitive]"),
        ]
    else:
        root = page.locator(".row-bot-main-shell")
        masks = [
            page.locator(".q-drawer--left"),
            page.locator(".row-bot-msg"),
            page.locator(".row-bot-msg-name"),
            page.locator("textarea,input"),
            page.locator("[data-sensitive]"),
        ]
    if root.count() != 1:
        raise LiveParitySafetyError(f"{client} capture root is unavailable")
    root.screenshot(path=str(destination), animations="disabled", mask=masks)
    return name


def _open_react_and_create(page: Page, base: str, title: str) -> tuple[str, str]:
    page.goto(base + "/app-v2/", wait_until="domcontentloaded", timeout=120_000)
    page.locator(".home-connection-status").wait_for(state="visible", timeout=120_000)
    page.get_by_role("button", name="New chat", exact=True).click()
    page.wait_for_url("**/app-v2/conversations/*", timeout=60_000)
    conversation_id = page.url.rstrip("/").split("/")[-1]
    uuid.UUID(conversation_id)
    page.get_by_role("textbox", name="Message", exact=True).wait_for(timeout=60_000)

    page.get_by_role("button", name="Conversation actions", exact=True).click()
    page.get_by_role("menuitem", name="Manage conversation", exact=True).click()
    dialog = page.get_by_role("dialog", name="Conversation actions")
    dialog.get_by_role("textbox", name="Conversation name").fill(title)
    dialog.get_by_role("button", name="Review rename", exact=True).click()
    dialog.get_by_role("button", name="Apply reviewed action", exact=True).click()
    dialog.get_by_text("Conversation action completed.", exact=True).wait_for(timeout=30_000)
    dialog.get_by_role("button", name="Close", exact=True).click()
    page.get_by_role("heading", name=title, exact=True).wait_for(timeout=30_000)

    _ensure_ask_approval(page)

    model = _safe_display(page.get_by_role("button", name="Model", exact=True).inner_text(), label="model")
    if model.casefold() in {"choose model", "no cached models. open models in settings."}:
        raise LiveParitySafetyError("no usable configured default model")
    return conversation_id, model


def _ensure_ask_approval(page: Page) -> None:
    approvals = page.get_by_role("button", name="Approvals", exact=True)
    if "Ask" not in approvals.inner_text():
        approvals.click()
        page.get_by_role("menuitem", name="Ask", exact=True).click()
        _wait_until(lambda: "Ask" in approvals.inner_text(), timeout=30, label="approval mode Ask")


def _open_react_existing(
    page: Page,
    base: str,
    target: dict[str, str],
) -> tuple[str, str]:
    """Open only the prior runner-owned validation conversation."""

    page.goto(base + "/app-v2/", wait_until="domcontentloaded", timeout=120_000)
    page.locator(".home-connection-status").wait_for(state="visible", timeout=120_000)
    page.get_by_role("button", name=target["title"], exact=True).click()
    page.wait_for_url("**/app-v2/conversations/*", timeout=60_000)
    conversation_id = page.url.rstrip("/").split("/")[-1]
    uuid.UUID(conversation_id)
    if transient_hash(conversation_id) != target["conversation_hash"]:
        raise LiveParitySafetyError("validation conversation identity did not match prior evidence")
    page.get_by_role("textbox", name="Message", exact=True).wait_for(timeout=60_000)
    _ensure_ask_approval(page)
    model = _safe_display(
        page.get_by_role("button", name="Model", exact=True).inner_text(),
        label="model",
    )
    if model.casefold() in {"choose model", "no cached models. open models in settings."}:
        raise LiveParitySafetyError("no usable configured default model")
    return conversation_id, model


def _open_nicegui(page: Page, base: str, title: str) -> None:
    page.goto(base + "/", wait_until="domcontentloaded", timeout=120_000)
    page.locator(".row-bot-main-shell").wait_for(state="visible", timeout=120_000)
    title_link = page.get_by_text(title, exact=True).last
    title_link.wait_for(state="visible", timeout=60_000)
    title_link.click()
    page.get_by_placeholder("Do anything…").wait_for(state="visible", timeout=30_000)


def _last_metric(shape: dict[str, Any], role: str) -> dict[str, Any] | None:
    return next((row for row in reversed(shape["rows"]) if row["role"] == role), None)


def _structurally_same_assistant(react: dict[str, Any], nicegui: dict[str, Any]) -> bool:
    left = _last_metric(react, "assistant")
    right = _last_metric(nicegui, "assistant")
    if not left or not right:
        return False
    return (
        left["text_hash"] == right["text_hash"]
        and left["headings"] == right["headings"]
        and left["lists"] == right["lists"]
        and left["code_blocks"] == right["code_blocks"]
    )


def _assistant_characters(shape: dict[str, Any]) -> int:
    return sum(
        int(row.get("characters") or 0)
        for row in shape["rows"]
        if row["role"] == "assistant"
    )


def _run_final_calculator_stop(
    *,
    react: Page,
    nicegui: Page,
    run: Path,
    budget: AttemptBudget,
    mark: Callable[[str, str, Page, Page], None],
    screenshots: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use one final generation for both calculator trace and Stop convergence."""

    scenario = FINAL_COMBINED_SCENARIO
    budget.admit(scenario)
    before_react = _react_shape(react)
    before_nicegui = _nicegui_shape(nicegui)
    prior_react_traces = len(before_react["traces"])
    prior_nicegui_traces = len(before_nicegui["traces"])
    prior_react_succeeded = react.locator(
        '.trace-group[data-trace-status="succeeded"]'
    ).count()
    prior_assistant_characters = _assistant_characters(before_react)
    composer = react.get_by_role("textbox", name="Message", exact=True)
    composer.fill(SCENARIO_PROMPTS[scenario])
    react.get_by_role("button", name="Send", exact=True).click()
    try:
        react.get_by_role("button", name="Stop", exact=True).wait_for(timeout=120_000)

        def tool_or_completion() -> bool:
            return (
                react.get_by_role("button", name="Review approval", exact=True).count() > 0
                or len(_react_shape(react)["traces"]) > prior_react_traces
                or react.get_by_role("button", name="Send", exact=True).is_visible()
            )

        _wait_until(tool_or_completion, timeout=240, label="calculator decision")
        approval = react.get_by_role("button", name="Review approval", exact=True)
        if approval.count() and approval.is_visible():
            approval.click()
            dialog = react.get_by_role("dialog", name="Approval required")
            dialog.wait_for(timeout=30_000)
            if "calculat" not in dialog.inner_text().casefold():
                dialog.get_by_role("button", name="Reject action", exact=True).click()
                raise LiveParitySafetyError("provider requested a non-calculator tool")
            mark(scenario, "approval_wait", react, nicegui)
            dialog.get_by_role("button", name="Approve action", exact=True).click()

        _wait_until(
            lambda: (
                react.locator('.trace-group[data-trace-status="succeeded"]').count()
                > prior_react_succeeded
                and len(_nicegui_shape(nicegui)["traces"]) > prior_nicegui_traces
            ),
            timeout=180,
            label="paired calculator trace",
        )
        _wait_until(
            lambda: (
                _assistant_characters(_react_shape(react)) > prior_assistant_characters
                and _react_shape(react)["stop_visible"]
            ),
            timeout=120,
            label="post-calculator first token",
        )
        mark(scenario, "calculator_checkpointed", react, nicegui)
        screenshots.append(_capture(react, "react", run / "tool-settled-react.png"))
        screenshots.append(_capture(nicegui, "nicegui", run / "tool-settled-nicegui.png"))
        react.get_by_role("button", name="Stop", exact=True).click()
    except (PlaywrightTimeout, LiveParitySafetyError):
        budget.mark_uncertain(scenario)
        raise

    react.get_by_role("button", name="Send", exact=True).wait_for(timeout=180_000)
    _wait_until(
        lambda: not _react_shape(react)["stop_visible"]
        and not _nicegui_shape(nicegui)["stop_visible"],
        timeout=60,
        label="paired final stop quiescence",
    )
    final_react = _react_shape(react)
    final_nicegui = _nicegui_shape(nicegui)
    if (
        len(final_react["traces"]) <= prior_react_traces
        or len(final_nicegui["traces"]) <= prior_nicegui_traces
    ):
        raise LiveParitySafetyError("paired calculator traces did not survive Stop")
    new_react_traces = final_react["traces"][prior_react_traces:]
    new_nicegui_traces = final_nicegui["traces"][prior_nicegui_traces:]
    if not all(
        trace["status"] == "succeeded" and trace["collapsed"]
        for trace in new_react_traces
    ):
        raise LiveParitySafetyError("React calculator trace did not remain settled and collapsed")
    if not all(trace["collapsed"] for trace in new_nicegui_traces):
        raise LiveParitySafetyError("NiceGUI calculator trace did not remain collapsed")
    mark(scenario, "stopped_quiesced", react, nicegui)
    screenshots.append(_capture(react, "react", run / "stop-quiesced-react.png"))
    screenshots.append(_capture(nicegui, "nicegui", run / "stop-quiesced-nicegui.png"))
    return final_react, final_nicegui


def _run_shared_markdown(
    *,
    react: Page,
    nicegui: Page,
    run: Path,
    budget: AttemptBudget,
    mark: Callable[[str, str, Page, Page], None],
    screenshots: list[str],
) -> None:
    """Run the first generation and prove paired settlement after React reload."""

    budget.admit("shared_markdown")
    composer = react.get_by_role("textbox", name="Message", exact=True)
    composer.fill(SCENARIO_PROMPTS["shared_markdown"])
    react.get_by_role("button", name="Send", exact=True).click()
    try:
        react.get_by_role("button", name="Stop", exact=True).wait_for(timeout=120_000)
        _wait_until(
            lambda: bool(
                (_last_metric(_react_shape(react), "assistant") or {}).get("characters")
            ),
            timeout=180,
            label="React first token",
        )
        _wait_until(
            lambda: _nicegui_shape(nicegui).get("external_live") is True,
            timeout=30,
            label="NiceGUI shared live row",
        )
        mark("shared_markdown", "first_token", react, nicegui)
        screenshots.append(_capture(react, "react", run / "shared-first-token-react.png"))
        screenshots.append(_capture(nicegui, "nicegui", run / "shared-first-token-nicegui.png"))
    except (PlaywrightTimeout, LiveParitySafetyError):
        budget.mark_uncertain("shared_markdown")
        raise

    react.reload(wait_until="domcontentloaded", timeout=120_000)
    react.get_by_role("textbox", name="Message", exact=True).wait_for(timeout=60_000)
    mark("shared_markdown", "react_reattached", react, nicegui)
    react.get_by_role("button", name="Send", exact=True).wait_for(timeout=300_000)
    _wait_until(
        lambda: not _nicegui_shape(nicegui).get("external_live"),
        timeout=60,
        label="NiceGUI checkpoint settlement",
    )
    first_react = _react_shape(react)
    first_nicegui = _nicegui_shape(nicegui)
    if not _structurally_same_assistant(first_react, first_nicegui):
        raise LiveParitySafetyError(
            "paired clients did not settle on the same assistant structure"
        )
    mark("shared_markdown", "checkpointed", react, nicegui)


def _run_browser_lane(
    *,
    base: str,
    run: Path,
    title: str,
    report: dict[str, Any],
    budget: AttemptBudget,
    channel: str,
    resume_target: dict[str, str] | None = None,
    resume_after_markdown: bool = False,
    final_combined: bool = False,
) -> None:
    external_count = 0
    console_errors = 0
    page_errors = 0
    screenshots: list[str] = []
    timeline: list[dict[str, Any]] = []
    started = time.monotonic()

    def mark(scenario_id: str, state: str, react: Page, nicegui: Page) -> None:
        timeline.append(
            {
                "scenario_id": scenario_id,
                "state": state,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "react": _react_shape(react),
                "nicegui": _nicegui_shape(nicegui),
            }
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=channel,
            headless=True,
            args=[
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--no-first-run",
            ],
        )
        report["browser"] = {"channel": channel, "version": browser.version}
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            color_scheme="dark",
            reduced_motion="reduce",
            service_workers="block",
            permissions=[],
        )
        allowed_http = base + "/"
        allowed_ws = base.replace("http://", "ws://") + "/"

        def route(route: Any) -> None:
            nonlocal external_count
            url = route.request.url
            if url.startswith((allowed_http, allowed_ws, "data:", "blob:")):
                route.continue_()
            else:
                external_count += 1
                route.abort()

        context.route("**/*", route)

        def configure(page: Page) -> None:
            nonlocal console_errors, page_errors
            def console(event: Any) -> None:
                nonlocal console_errors
                if event.type == "error":
                    console_errors += 1

            page.on("console", console)

            def page_error(_error: Any) -> None:
                nonlocal page_errors
                page_errors += 1

            page.on("pageerror", page_error)

            def websocket(socket: Any) -> None:
                nonlocal external_count
                if not socket.url.startswith(allowed_ws):
                    external_count += 1

            page.on("websocket", websocket)

        react = context.new_page()
        configure(react)
        report["last_stage"] = (
            "react_conversation_resume" if resume_target else "react_conversation_creation"
        )
        if resume_target:
            conversation_id, model = _open_react_existing(react, base, resume_target)
        else:
            conversation_id, model = _open_react_and_create(react, base, title)
        report["conversation_hash"] = transient_hash(conversation_id)
        report["conversation_title"] = title
        report["conversation_created_at"] = (
            resume_target["conversation_created_at"] if resume_target else _utc_now()
        )
        report["model_display"] = model

        nicegui = context.new_page()
        configure(nicegui)
        report["last_stage"] = "nicegui_conversation_open"
        _open_nicegui(nicegui, base, title)
        mark("preflight", "clients_ready", react, nicegui)
        report["last_stage"] = "clients_ready"

        preflight = {
            "schema_version": 1,
            "authorized": True,
            "localhost_urls": [base + "/", base + "/app-v2/"],
            "model_display": model,
            "maximum_generation_attempts": MAX_GENERATION_ATTEMPTS,
            "privacy_rules": [
                "synthetic prompts only",
                "no prompt or response bodies retained",
                (
                    "only the prior runner-owned validation conversation is reopened"
                    if resume_target
                    else "no existing conversation opened for capture"
                ),
                "only the verified local read-only calculator may be exposed",
            ],
            "conversation_hash": transient_hash(conversation_id),
            "conversation_title": title,
            "created_at": report["conversation_created_at"],
        }
        write_redacted_json(run / "live-preflight.json", preflight)
        (run / "live-preflight.md").write_text(
            "# Live chat parity preflight\n\n"
            f"- Configured model: {model}\n"
            f"- Maximum real generation attempts: {MAX_GENERATION_ATTEMPTS}\n"
            f"- Local clients: {base}/ and {base}/app-v2/\n"
            "- Configured remote providers may receive the checked-in synthetic prompts and may incur charges.\n"
            "- Prompt/response bodies, credentials, browser storage, and profile paths are not retained.\n"
            "- The owner-approved live flag is present; no hidden default enables this lane.\n",
            encoding="utf-8",
        )
        print(f"Live parity preflight: maximum {MAX_GENERATION_ATTEMPTS} real generation attempts")
        print(f"Configured model: {model}")
        print(f"Local clients: {base}/ and {base}/app-v2/")
        print("Privacy: synthetic prompts only; no bodies, credentials, storage, or profile paths retained")

        if final_combined:
            report["last_stage"] = "safe_calculator_stop"
            final_react, final_nicegui = _run_final_calculator_stop(
                react=react,
                nicegui=nicegui,
                run=run,
                budget=budget,
                mark=mark,
                screenshots=screenshots,
            )
            microphone_quiesced = (
                react.get_by_role("button", name="Stop dictation", exact=True).count() == 0
                and react.get_by_role("button", name="End call", exact=True).count() == 0
            )
            if not microphone_quiesced:
                raise LiveParitySafetyError("voice recorder is not quiesced")
            if external_count:
                raise LiveParitySafetyError("browser attempted an off-origin request")
            if console_errors:
                raise LiveParitySafetyError("browser console error observed")
            if page_errors:
                raise LiveParitySafetyError("browser page error observed")
            report.update(
                {
                    "last_stage": "complete",
                    "attempt_count": len(budget.admitted),
                    "attempt_cap_respected": (
                        budget.used_before + len(budget.admitted)
                        <= MAX_GENERATION_ATTEMPTS
                    ),
                    "uncertain_attempts": len(budget.uncertain),
                    "external_request_count": external_count,
                    "console_error_count": console_errors,
                    "page_error_count": page_errors,
                    "microphone_quiesced": microphone_quiesced,
                    "stream_quiesced": not final_react["stop_visible"]
                    and not final_nicegui["stop_visible"],
                    "screenshots": screenshots,
                    "timeline": timeline,
                }
            )
            context.close()
            browser.close()
            return

        # Scenario 1: React-owned Markdown stream plus reload/reattach.
        report["last_stage"] = "shared_markdown"
        if resume_after_markdown:
            if not _structurally_same_assistant(
                _react_shape(react),
                _nicegui_shape(nicegui),
            ):
                raise LiveParitySafetyError(
                    "paired clients did not settle on the same assistant structure"
                )
            mark("shared_markdown", "checkpointed_resume", react, nicegui)
        else:
            _run_shared_markdown(
                react=react,
                nicegui=nicegui,
                run=run,
                budget=budget,
                mark=mark,
                screenshots=screenshots,
            )

        # Scenario 2: NiceGUI-owned generation, stopped from React after first token.
        report["last_stage"] = "stop_interruption"
        budget.admit("stop_interruption")
        nice_composer = nicegui.get_by_placeholder("Do anything…")
        before_stop_assistants = sum(
            row["role"] == "assistant" for row in _react_shape(react)["rows"]
        )
        nice_composer.fill(SCENARIO_PROMPTS["stop_interruption"])
        nice_composer.press("Enter")
        try:
            react.get_by_role("button", name="Stop", exact=True).wait_for(timeout=120_000)
            _wait_until(
                lambda: (
                    sum(
                        row["role"] == "assistant"
                        for row in _react_shape(react)["rows"]
                    )
                    > before_stop_assistants
                    and bool(
                        (_last_metric(_react_shape(react), "assistant") or {}).get(
                            "characters"
                        )
                    )
                ),
                timeout=180,
                label="shared stop first token",
            )
            mark("stop_interruption", "first_token", react, nicegui)
            react.get_by_role("button", name="Stop", exact=True).click()
        except (PlaywrightTimeout, LiveParitySafetyError):
            budget.mark_uncertain("stop_interruption")
            raise
        react.get_by_role("button", name="Send", exact=True).wait_for(timeout=180_000)
        _wait_until(
            lambda: not _react_shape(react)["stop_visible"]
            and not _nicegui_shape(nicegui)["stop_visible"],
            timeout=60,
            label="paired stop quiescence",
        )
        mark("stop_interruption", "quiesced", react, nicegui)
        screenshots.append(_capture(react, "react", run / "stop-quiesced-react.png"))
        screenshots.append(_capture(nicegui, "nicegui", run / "stop-quiesced-nicegui.png"))

        # Scenario 3: only the ephemeral calculator allowlist is available.
        report["last_stage"] = "safe_calculator"
        budget.admit("safe_calculator")
        composer = react.get_by_role("textbox", name="Message", exact=True)
        composer.fill(SCENARIO_PROMPTS["safe_calculator"])
        react.get_by_role("button", name="Send", exact=True).click()
        try:
            react.get_by_role("button", name="Stop", exact=True).wait_for(timeout=120_000)

            def tool_or_completion() -> bool:
                return (
                    react.get_by_role("button", name="Review approval", exact=True).count() > 0
                    or react.locator(".trace-group").count() > 0
                    or react.get_by_role("button", name="Send", exact=True).is_visible()
                )

            _wait_until(tool_or_completion, timeout=240, label="calculator decision")
            approval = react.get_by_role("button", name="Review approval", exact=True)
            if approval.count() and approval.is_visible():
                approval.click()
                dialog = react.get_by_role("dialog", name="Approval required")
                dialog.wait_for(timeout=30_000)
                summary = dialog.inner_text().casefold()
                if "calculat" not in summary:
                    dialog.get_by_role("button", name="Reject action", exact=True).click()
                    raise LiveParitySafetyError("provider requested a non-calculator tool")
                mark("safe_calculator", "approval_wait", react, nicegui)
                screenshots.append(_capture(react, "react", run / "tool-pending-react.png"))
                screenshots.append(_capture(nicegui, "nicegui", run / "tool-pending-nicegui.png"))
                dialog.get_by_role("button", name="Approve action", exact=True).click()

            react.get_by_role("button", name="Send", exact=True).wait_for(timeout=300_000)
        except (PlaywrightTimeout, LiveParitySafetyError):
            budget.mark_uncertain("safe_calculator")
            raise
        _wait_until(
            lambda: react.locator('.trace-group[data-trace-status="succeeded"]').count() > 0,
            timeout=60,
            label="settled calculator trace",
        )
        _wait_until(
            lambda: nicegui.locator('[data-docs-id="tool-trace"]').count() > 0,
            timeout=60,
            label="NiceGUI settled calculator trace",
        )
        final_react = _react_shape(react)
        final_nicegui = _nicegui_shape(nicegui)
        if not final_react["traces"] or not final_nicegui["traces"]:
            raise LiveParitySafetyError("paired calculator traces are unavailable")
        if not all(trace["collapsed"] for trace in final_react["traces"]):
            raise LiveParitySafetyError("React tool trace did not remain collapsed")
        mark("safe_calculator", "checkpointed", react, nicegui)
        screenshots.append(_capture(react, "react", run / "tool-settled-react.png"))
        screenshots.append(_capture(nicegui, "nicegui", run / "tool-settled-nicegui.png"))

        microphone_quiesced = (
            react.get_by_role("button", name="Stop dictation", exact=True).count() == 0
            and react.get_by_role("button", name="End call", exact=True).count() == 0
        )
        if not microphone_quiesced:
            raise LiveParitySafetyError("voice recorder is not quiesced")
        if external_count:
            raise LiveParitySafetyError("browser attempted an off-origin request")
        if console_errors:
            raise LiveParitySafetyError("browser console error observed")
        if page_errors:
            raise LiveParitySafetyError("browser page error observed")

        report.update(
            {
                "last_stage": "complete",
                "attempt_count": len(budget.admitted),
                "attempt_cap_respected": (
                    budget.used_before + len(budget.admitted)
                    <= MAX_GENERATION_ATTEMPTS
                ),
                "uncertain_attempts": len(budget.uncertain),
                "external_request_count": external_count,
                "console_error_count": console_errors,
                "page_error_count": page_errors,
                "microphone_quiesced": microphone_quiesced,
                "stream_quiesced": not final_react["stop_visible"]
                and not final_nicegui["stop_visible"],
                "screenshots": screenshots,
                "timeline": timeline,
            }
        )
        context.close()
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default="msedge")
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument(
        "--final-combined",
        action="store_true",
        help="Use the single remaining attempt for calculator trace plus Stop.",
    )
    parser.add_argument(
        "--resume-after-markdown",
        action="store_true",
        help=(
            "Reopen the prior runner-owned chat after one settled Markdown attempt "
            "and run only the remaining Stop and calculator scenarios."
        ),
    )
    options = parser.parse_args()

    # This must remain the first operation capable of preceding profile access.
    require_live_opt_in(os.environ)
    from row_bot.data_paths import get_row_bot_data_dir

    profile = resolve_profile_after_opt_in(
        os.environ, lambda: get_row_bot_data_dir(create=False)
    )
    _assert_profile_quiescent(profile)
    calculator = qualify_saved_calculator(profile / "tools_config.json", CALCULATOR)
    if not calculator.qualified:
        raise LiveParitySafetyError(
            "no already-enabled qualifying local read-only tool is available"
        )

    attempts_before, resume_target = _recorded_live_state(options.evidence)
    if options.final_combined and options.resume_after_markdown:
        raise LiveParitySafetyError("live continuation modes are mutually exclusive")
    if options.final_combined:
        if attempts_before != MAX_GENERATION_ATTEMPTS - 1 or resume_target is None:
            raise LiveParitySafetyError(
                "final combined mode requires exactly one remaining attempt and a prior validation chat"
            )
    elif options.resume_after_markdown:
        if attempts_before != 1 or resume_target is None:
            raise LiveParitySafetyError(
                "Markdown continuation requires exactly one prior settled attempt and its validation chat"
            )
    elif attempts_before:
        raise LiveParitySafetyError(
            "prior live attempts exist; replay is forbidden"
        )

    before = fingerprint()
    run = options.evidence / (
        "live-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    )
    run.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "schema_version": 1,
        "outcome": "failed",
        "started_at": _utc_now(),
        "source_head": before["head"],
        "source_branch": before["branch"],
        "source_digest_before": _source_digest(before),
        "maximum_generation_attempts": MAX_GENERATION_ATTEMPTS,
        "attempts_before_run": attempts_before,
        "calculator_qualification": calculator.reason,
        "localhost_only": True,
        "owned_process_count": 1,
    }
    validate_redacted_report(report)
    budget = AttemptBudget(used_before=attempts_before)
    process: subprocess.Popen[Any] | None = None
    port = _free_loopback_port()
    base = f"http://127.0.0.1:{port}"
    assert_localhost_url(base + "/")
    secret = secrets.token_urlsafe(32)
    title = (
        resume_target["title"]
        if (options.final_combined or options.resume_after_markdown)
        and resume_target is not None
        else VALIDATION_TITLE + " " + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    graceful = False
    code = 1
    try:
        with tempfile.TemporaryDirectory(prefix="live-chat-parity-", dir=ROOT / ".tmp") as temporary:
            log_path = Path(temporary) / "owned-app.log"
            environment = dict(os.environ)
            environment.update(
                {
                    LIVE_OPT_IN: "1",
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "ROW_BOT_HOST": "127.0.0.1",
                    "ROW_BOT_PORT": str(port),
                    "ROW_BOT_NATIVE": "1",
                    "ROW_BOT_LAUNCH_SECRET": secret,
                }
            )
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with log_path.open("w", encoding="utf-8") as output:
                try:
                    process = subprocess.Popen(
                        [sys.executable, str(APP_ENTRY)],
                        cwd=ROOT,
                        env=environment,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        creationflags=flags,
                    )
                    report["readiness_ms"] = _wait_ready(process, base, secret)
                    _run_browser_lane(
                        base=base,
                        run=run,
                        title=title,
                        report=report,
                        budget=budget,
                        channel=options.channel,
                        resume_target=(
                            resume_target
                            if options.final_combined or options.resume_after_markdown
                            else None
                        ),
                        resume_after_markdown=options.resume_after_markdown,
                        final_combined=options.final_combined,
                    )
                finally:
                    graceful = _stop_owned(process, base, secret)
        report["outcome"] = "passed"
        code = 0
    except LiveParitySafetyError as exc:
        report["outcome"] = "blocked" if not budget.admitted else "failed"
        report["failure_code"] = str(exc)
        report["attempt_count"] = len(budget.admitted)
        report["uncertain_attempts"] = len(budget.uncertain)
        code = 2 if not budget.admitted else 1
    except (PlaywrightError, OSError, RuntimeError, ValueError) as exc:
        report["outcome"] = "failed"
        report["failure_code"] = type(exc).__name__
        report["attempt_count"] = len(budget.admitted)
        report["uncertain_attempts"] = len(budget.uncertain)
        code = 1
    finally:
        if process is not None and process.poll() is None:
            graceful = _stop_owned(process, base, secret)
        after = fingerprint()
        report["graceful_shutdown"] = graceful
        report["owned_process_stopped"] = process is None or process.poll() is not None
        report["source_digest_after"] = _source_digest(after)
        report["source_stable"] = before == after
        report["finished_at"] = _utc_now()
        if not report["source_stable"]:
            report["outcome"] = "failed"
            report["failure_code"] = "source_changed_during_live_run"
            code = 1
        write_redacted_json(run / "live-report.json", report)
        print(f"Live parity outcome: {report['outcome']}")
        print(f"Evidence run: {run.name}")
        if report.get("conversation_title"):
            print(
                "Validation conversation: "
                f"{report['conversation_title']} · {report['conversation_created_at']}"
            )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
