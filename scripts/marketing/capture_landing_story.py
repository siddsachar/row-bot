"""End-to-end CLI for the reviewed, real-profile landing story capture."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.marketing.capture_contract import LandingStoryManifest, load_manifest  # noqa: E402
from scripts.marketing.capture_run import (  # noqa: E402
    CaptureSafetyError,
    GenerationBudget,
    RunReceipt,
    require_contained,
    require_exact_normal_profile,
    require_profile_quiescent,
    sha256_file,
)
from scripts.marketing.clients.base import ClientAdapterError  # noqa: E402
from scripts.marketing.clients.nicegui import NiceGuiAdapter  # noqa: E402
from scripts.marketing.media_pipeline import process_run, publish_run, validate_run  # noqa: E402


MANIFEST_PATH = ROOT / "scripts" / "marketing" / "landing_story.yml"
RUN_ROOT = ROOT / "docs-build" / "marketing-capture"
PUBLIC_ROOT = ROOT / "docs" / "media" / "landing-story"
LAUNCH_SECRET_ENV = "ROW_BOT_LAUNCH_SECRET"
FFMPEG_ENV = "ROW_BOT_FFMPEG"


def _normal_profile() -> Path:
    from row_bot.brand import default_data_dir

    return default_data_dir().expanduser().resolve()


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _managed_browser() -> str:
    try:
        from row_bot.mcp_client.requirements import playwright_browser_executable_path

        managed = Path(playwright_browser_executable_path())
        if managed.is_file():
            return str(managed)
    except Exception:
        pass
    candidates = (
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    )
    return next((str(path) for path in candidates if path.is_file()), "")


def _ffmpeg_executable() -> str:
    configured = str(os.environ.get(FFMPEG_ENV) or "").strip()
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    return str(shutil.which("ffmpeg") or "")


def _wait_ready(
    port: int, process: subprocess.Popen[Any], secret: str, timeout: float = 120.0
) -> None:
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/launcher-ping",
        headers={"Authorization": f"Bearer {secret}"},
    )
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CaptureSafetyError(
                f"owned Row-Bot process exited during startup ({process.returncode})"
            )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                if response.status == 200 and b"row-bot" in response.read(512).lower():
                    return
        except Exception:
            time.sleep(0.4)
    raise CaptureSafetyError("owned loopback Row-Bot process did not become ready")


@contextmanager
def _owned_app(
    profile: Path,
    run_dir: Path,
    *,
    network_enabled: bool,
    knowledge_entry_ids: tuple[str, ...] = (),
) -> Iterator[tuple[subprocess.Popen[Any], int]]:
    """Launch one loopback child and stop only that exact process."""

    require_profile_quiescent(profile)
    port = _free_port()
    secret = secrets.token_urlsafe(32)
    log_dir = require_contained(run_dir / "logs", run_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = (log_dir / "app.stdout.log").open("a", encoding="utf-8")
    stderr = (log_dir / "app.stderr.log").open("a", encoding="utf-8")
    launch_marker = (
        f"\n--- owned app launch {datetime.now(timezone.utc).isoformat()} "
        f"network_enabled={str(network_enabled).lower()} ---\n"
    )
    stdout.write(launch_marker)
    stderr.write(launch_marker)
    stdout.flush()
    stderr.flush()
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join(
            [str(SRC), str(ROOT), os.environ.get("PYTHONPATH", "")]
        ),
        "ROW_BOT_HOST": "127.0.0.1",
        "ROW_BOT_PORT": str(port),
        "ROW_BOT_DATA_DIR": str(profile),
        "ROW_BOT_DOCS_CAPTURE": "1",
        "ROW_BOT_DOCS_REAL_DATA": "1",
        "ROW_BOT_DOCS_FAKE_PROVIDERS": "0",
        "ROW_BOT_DOCS_DISABLE_AUTOSTART": "1",
        "ROW_BOT_DOCS_DISABLE_NETWORK": "0" if network_enabled else "1",
        "ROW_BOT_DOCS_REDUCE_MOTION": "1",
        "ROW_BOT_MARKETING_CAPTURE": "1",
        "ROW_BOT_MARKETING_KNOWLEDGE_IDS": ",".join(knowledge_entry_ids),
        LAUNCH_SECRET_ENV: secret,
    }
    process = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=ROOT,
        env=env,
        stdout=stdout,
        stderr=stderr,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        _wait_ready(port, process, secret)
        yield process, port
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stdout.close()
        stderr.close()


def _launch_browser(playwright: Any) -> Any:
    options: dict[str, Any] = {"headless": True}
    executable = _managed_browser()
    if executable:
        options["executable_path"] = executable
    return playwright.chromium.launch(**options)


def _block_external_routes(context: Any) -> None:
    def _route(route: Any) -> None:
        url = str(route.request.url)
        if url.startswith(
            ("http://127.0.0.1:", "ws://127.0.0.1:", "data:", "blob:http://127.0.0.1:")
        ):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    context.route("**/*", _route)


def _thread_id(profile: Path, title: str) -> str:
    path = profile / "threads.db"
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT thread_id FROM thread_meta WHERE name=? ORDER BY updated_at DESC LIMIT 1",
            (title,),
        ).fetchone()
    if not row:
        raise CaptureSafetyError(f"prepared conversation was not persisted: {title}")
    return str(row[0])


def _thread_id_or_none(profile: Path, title: str) -> str:
    try:
        return _thread_id(profile, title)
    except CaptureSafetyError:
        return ""


def _designer_project_id(profile: Path, title: str) -> str:
    projects = profile / "designer" / "projects"
    candidates: list[tuple[float, str]] = []
    for path in projects.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(raw.get("name") or "") == title and str(raw.get("id") or ""):
            candidates.append((path.stat().st_mtime, str(raw["id"])))
    if not candidates:
        raise CaptureSafetyError(
            f"prepared Designer project was not persisted: {title}"
        )
    return max(candidates)[1]


def _designer_project_id_or_none(profile: Path, title: str) -> str:
    try:
        return _designer_project_id(profile, title)
    except CaptureSafetyError:
        return ""


def _task_id(title: str) -> str:
    from row_bot.tasks import list_tasks

    matches = [task for task in list_tasks() if str(task.get("name") or "") == title]
    if not matches:
        raise CaptureSafetyError(f"prepared workflow was not persisted: {title}")
    return str(matches[0]["id"])


def _wait_workflow(title: str, *, timeout: float = 600.0) -> tuple[str, str]:
    from row_bot.tasks import get_recent_runs

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runs = [
            run
            for run in get_recent_runs(limit=50)
            if str(run.get("task_name") or "") == title
        ]
        if runs:
            latest = runs[0]
            status = str(latest.get("status") or "")
            if status.startswith("completed"):
                return str(latest.get("id") or ""), str(latest.get("thread_id") or "")
            if status in {"failed", "stopped", "cancelled", "uncertain"}:
                raise CaptureSafetyError(f"workflow ended with {status}")
        time.sleep(1)
    raise TimeoutError("workflow outcome is uncertain after the bounded wait")


def _durable_assistant_turn_count(thread_id: str) -> int:
    """Count provider responses in one fresh, capture-owned conversation."""

    from row_bot.threads import get_latest_checkpoint_messages

    return sum(
        type(message).__name__ == "AIMessage"
        for message in get_latest_checkpoint_messages(thread_id)
    )


def _knowledge_entry_ids(thread_id: str) -> tuple[str, ...]:
    """Return IDs created by save_memory in one capture-owned conversation."""

    from row_bot.threads import get_latest_checkpoint_messages

    ids: list[str] = []
    for message in get_latest_checkpoint_messages(thread_id):
        if type(message).__name__ != "ToolMessage":
            continue
        if str(getattr(message, "name", "") or "") != "save_memory":
            continue
        match = re.search(r"(?m)^ID:\s*([A-Za-z0-9_.:-]+)\s*$", str(message.content))
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return tuple(ids)


def _create_goal_and_approval(records: dict[str, str]) -> None:
    from row_bot.goals import start_goal
    from row_bot.tasks import create_approval_request

    goal = start_goal(
        records["homepage-implementation-backlog"],
        "Implement the public Row-Bot homepage story with accessibility, privacy, and performance checks.",
        max_turns=3,
    )
    records["homepage-goal"] = str(goal["id"])
    _, approval_id = create_approval_request(
        run_id=f"marketing-{records['campaign-narrative']}",
        task_id=records["weekly-sovereignty-watch"],
        step_id="reviewed-public-write",
        message="Review writing the public launch summary to the Row-Bot marketing workspace.",
        timeout_minutes=24 * 60,
        resume_kind="conversation",
        source_label="Landing story reversible write",
        source_thread_id=records["campaign-narrative"],
        parent_thread_id=records["campaign-narrative"],
        approval_payload_json={
            "description": "Write the reviewed public launch summary",
            "target": "marketing workspace / launch-summary.md",
            "data_summary": "Public-safe Row-Bot campaign brief",
            "reversible": True,
        },
    )
    records["approval-request"] = approval_id
    records["approval-boundary"] = records["campaign-narrative"]


def _preflight_ui(manifest: LandingStoryManifest, profile: Path) -> dict[str, Any]:
    """Open read-only NiceGUI surfaces before any bounded generation begins."""

    from playwright.sync_api import sync_playwright

    run_dir = require_contained(RUN_ROOT / ".preflight", RUN_ROOT)
    run_dir.mkdir(parents=True, exist_ok=True)
    with _owned_app(profile, run_dir, network_enabled=False) as (_process, port):
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            context = browser.new_context(
                viewport={
                    "width": manifest.viewports["desktop"].width,
                    "height": manifest.viewports["desktop"].height,
                },
                reduced_motion="reduce",
            )
            _block_external_routes(context)
            page = context.new_page()
            adapter = NiceGuiAdapter(f"http://127.0.0.1:{port}", page=page, records={})
            try:
                adapter._goto(docs_surface="chat-main")
                try:
                    page.wait_for_selector(
                        '[data-docs-id="chat-composer"]',
                        state="attached",
                        timeout=30_000,
                    )
                except Exception as exc:
                    screenshot = require_contained(
                        run_dir / "ui-preflight-failure.png", run_dir
                    )
                    page.screenshot(path=str(screenshot), full_page=True)
                    body = page.locator("body").inner_text(timeout=5_000)[:500]
                    raise CaptureSafetyError(
                        f"chat surface did not attach at {page.url}: {body}"
                    ) from exc
                adapter.open_model_picker()
                for required_model in (manifest.models.local, manifest.models.frontier):
                    _option, label = adapter.reveal_chat_model_option(required_model)
                    model_options = page.locator(
                        ".q-menu:visible .q-item"
                    ).all_inner_texts()
                    if sum(label in option for option in model_options) != 1:
                        model_id = required_model.split(":", 2)[-1]
                        matching_labels = [
                            option for option in model_options if model_id in option
                        ]
                        raise CaptureSafetyError(
                            "provider-qualified model is absent or ambiguous in the "
                            f"NiceGUI picker: {required_model}; matching labels={matching_labels!r}"
                        )
                    page.keyboard.press("Escape")
                    if required_model != manifest.models.frontier:
                        adapter.open_model_picker()
                page.get_by_role("button", name="＋ New").wait_for(
                    state="visible", timeout=10_000
                )
                adapter.open_home_surface("knowledge")
                adapter.open_home_surface("workflow")
                adapter.open_home_surface("designer")
                return {
                    "ok": True,
                    "surfaces": ["chat", "knowledge", "workflow", "designer"],
                }
            finally:
                context.close()
                browser.close()


def preflight(manifest: LandingStoryManifest) -> dict[str, Any]:
    profile = _normal_profile()
    checks: dict[str, Any] = {
        "manifest": "ok",
        "profile": "normal" if profile.is_dir() else "missing",
        "profile_quiescent": True,
        "browser": bool(_managed_browser()),
        "ffmpeg": bool(_ffmpeg_executable()),
        "models": {},
    }
    try:
        require_profile_quiescent(profile)
    except CaptureSafetyError as exc:
        checks["profile_quiescent"] = False
        checks["profile_busy_reason"] = str(exc)
    prior = os.environ.get("ROW_BOT_DATA_DIR")
    os.environ["ROW_BOT_DATA_DIR"] = str(profile)
    try:
        from row_bot.providers.selection import resolve_catalog_model_selection

        for role, model in (
            ("local", manifest.models.local),
            ("frontier", manifest.models.frontier),
        ):
            try:
                selection = resolve_catalog_model_selection(
                    model,
                    surface="chat",
                    require_agent_ready=True,
                    require_pinned=True,
                )
                checks["models"][role] = {
                    "configured": True,
                    "ref": selection.ref,
                    "provider": selection.provider_id,
                }
            except Exception as exc:
                checks["models"][role] = {"configured": False, "reason": str(exc)}
    finally:
        if prior is None:
            os.environ.pop("ROW_BOT_DATA_DIR", None)
        else:
            os.environ["ROW_BOT_DATA_DIR"] = prior
    checks["ui"] = {"ok": False, "reason": "prerequisites unavailable"}
    if (
        checks["profile"] == "normal"
        and checks["profile_quiescent"]
        and checks["browser"]
        and all(item.get("configured") for item in checks["models"].values())
    ):
        try:
            checks["ui"] = _preflight_ui(manifest, profile)
        except Exception as exc:
            checks["ui"] = {"ok": False, "reason": str(exc)}
    checks["ok"] = bool(
        checks["profile"] == "normal"
        and checks["profile_quiescent"]
        and checks["browser"]
        and checks["ffmpeg"]
        and checks["ui"].get("ok")
        and all(item.get("configured") for item in checks["models"].values())
    )
    return checks


def prepare(
    manifest: LandingStoryManifest,
    *,
    client: str,
    authorize_real_profile: bool,
    selected_profile: Path | None,
    run_id: str | None = None,
    enrich_knowledge_graph: bool = False,
) -> RunReceipt:
    if client != "nicegui":
        raise CaptureSafetyError("prepare currently requires the NiceGUI adapter")
    profile = require_exact_normal_profile(
        selected_profile,
        authorize=authorize_real_profile,
        normal_profile=_normal_profile(),
    )
    require_profile_quiescent(profile)
    os.environ["ROW_BOT_DATA_DIR"] = str(profile)
    models = {"local": manifest.models.local, "frontier": manifest.models.frontier}
    if run_id:
        run_dir = require_contained(RUN_ROOT / run_id, RUN_ROOT)
        receipt = RunReceipt.read(run_dir)
        if receipt.story_id != manifest.story.id or receipt.models != models:
            raise CaptureSafetyError(
                "run receipt does not match the selected story and models"
            )
        if any(
            item.get("status") in {"started", "uncertain"}
            for item in receipt.generation_attempts
        ):
            raise CaptureSafetyError(
                "preparation cannot resume after an uncertain generation outcome"
            )
        allowed_resume_statuses = {"failed", "preparing", "prepared"}
        if enrich_knowledge_graph:
            allowed_resume_statuses.add("published-locally")
        if receipt.status not in allowed_resume_statuses:
            raise CaptureSafetyError(
                f"run cannot resume preparation from {receipt.status}"
            )
    else:
        run_id = (
            time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)
        )
        run_dir = require_contained(RUN_ROOT / run_id, RUN_ROOT)
        run_dir.mkdir(parents=True, exist_ok=False)
        receipt = RunReceipt(
            run_id=run_id,
            story_id=manifest.story.id,
            prompt_version=manifest.story.prompt_version,
            git_commit=_git_commit(),
            client=client,
            models=models,
            max_generation_attempts=manifest.story.max_generation_attempts,
            sources=[source.url for source in manifest.public_sources],
            phase="prepare",
        )
        receipt.write(run_dir)
    budget = GenerationBudget(
        manifest.story.max_generation_attempts,
        attempts=list(receipt.generation_attempts),
    )
    records: dict[str, str] = dict(receipt.records)
    try:
        from playwright.sync_api import sync_playwright

        with _owned_app(profile, run_dir, network_enabled=True) as (_process, port):
            with sync_playwright() as playwright:
                browser = _launch_browser(playwright)
                context = browser.new_context(
                    viewport={
                        "width": manifest.viewports["desktop"].width,
                        "height": manifest.viewports["desktop"].height,
                    },
                    reduced_motion="reduce",
                )
                page = context.new_page()
                adapter = NiceGuiAdapter(
                    f"http://127.0.0.1:{port}", page=page, records=records
                )
                try:
                    for record_key, title in (
                        (
                            "homepage-implementation-backlog",
                            "Homepage implementation backlog",
                        ),
                        ("release-readiness", "Release readiness"),
                    ):
                        if records.get(record_key):
                            continue
                        thread_id = _thread_id_or_none(profile, title)
                        if not thread_id:
                            adapter.create_empty_conversation(title=title)
                            thread_id = _thread_id(profile, title)
                        records[record_key] = thread_id
                        receipt.records = dict(records)
                        receipt.write(run_dir)
                    completed_purposes = {
                        str(item.get("purpose") or "")
                        for item in budget.attempts
                        if item.get("status") in {"succeeded", "artifact_retained"}
                    }
                    failed_safe_purposes = {
                        str(item.get("purpose") or "")
                        for item in budget.attempts
                        if item.get("status") == "failed_safe"
                    }
                    for step in manifest.preparation:
                        if step.id in completed_purposes:
                            continue
                        model = models[step.model_role]
                        if step.record == "landing-visual-direction":
                            if not records.get(step.record):
                                existing = _designer_project_id_or_none(
                                    profile, step.title
                                )
                                if existing:
                                    records[step.record] = existing
                                    records["landing-visual-direction-thread"] = (
                                        _thread_id(profile, f"🎨 {step.title}")
                                    )
                            if records.get(step.record):
                                adapter.records[step.record] = records[step.record]
                                adapter.open_designer_project(step.record)
                                adapter.select_model(model)
                            else:
                                adapter.prepare_designer_project(
                                    title=step.title,
                                    model_ref=model,
                                )
                                records[step.record] = _designer_project_id(
                                    profile, step.title
                                )
                                records["landing-visual-direction-thread"] = _thread_id(
                                    profile, f"🎨 {step.title}"
                                )
                        elif step.record == "weekly-sovereignty-watch":
                            if not records.get(step.record):
                                adapter.create_workflow(
                                    title=step.title,
                                    model_ref=model,
                                    prompt=step.prompt,
                                )
                                records[step.record] = _task_id(step.title)
                        else:
                            if step.id in failed_safe_purposes:
                                records.pop(step.record, None)
                            if records.get(step.record):
                                adapter.records[step.record] = records[step.record]
                                adapter.open_conversation(step.record)
                                adapter.select_model(model)
                            else:
                                adapter.prepare_conversation(
                                    title=step.title,
                                    model_ref=model,
                                )
                                records[step.record] = _thread_id(profile, step.title)
                        receipt.records = dict(records)
                        receipt.write(run_dir)
                        attempt = budget.begin(model=model, purpose=step.id)
                        receipt.status = "preparing"
                        receipt.generation_attempts = list(budget.attempts)
                        receipt.records = dict(records)
                        receipt.write(run_dir)
                        try:
                            if step.record == "landing-visual-direction":
                                adapter.send_prepared_prompt(step.prompt)
                                thread_id = records["landing-visual-direction-thread"]
                                turn_count = _durable_assistant_turn_count(thread_id)
                                budget.finish(
                                    attempt,
                                    status="succeeded",
                                    conversation_id=thread_id,
                                    provider_call_count=turn_count,
                                    durable_assistant_turn_count=turn_count,
                                )
                            elif step.record == "weekly-sovereignty-watch":
                                adapter.run_workflow(step.title)
                                operation_id, thread_id = _wait_workflow(step.title)
                                records["weekly-sovereignty-watch-thread"] = thread_id
                                turn_count = _durable_assistant_turn_count(thread_id)
                                budget.finish(
                                    attempt,
                                    status="succeeded",
                                    operation_id=operation_id,
                                    conversation_id=thread_id,
                                    provider_call_count=turn_count,
                                    durable_assistant_turn_count=turn_count,
                                )
                            else:
                                adapter.send_prepared_prompt(step.prompt)
                                thread_id = records[step.record]
                                turn_count = _durable_assistant_turn_count(thread_id)
                                budget.finish(
                                    attempt,
                                    status="succeeded",
                                    conversation_id=thread_id,
                                    provider_call_count=turn_count,
                                    durable_assistant_turn_count=turn_count,
                                )
                                if step.record == "what-should-stay-local":
                                    for index, entry_id in enumerate(
                                        _knowledge_entry_ids(thread_id), start=1
                                    ):
                                        records[f"knowledge-entry-{index}"] = entry_id
                            receipt.generation_attempts = list(budget.attempts)
                            receipt.records = dict(records)
                            receipt.write(run_dir)
                        except ClientAdapterError as exc:
                            if budget.attempts[attempt - 1].get(
                                "status"
                            ) == "started" and "provider readiness boundary" in str(
                                exc
                            ):
                                budget.finish(
                                    attempt,
                                    status="failed_safe",
                                    conversation_id=(
                                        records.get(
                                            "landing-visual-direction-thread", ""
                                        )
                                        if step.record == "landing-visual-direction"
                                        else records.get(step.record, "")
                                    ),
                                )
                                receipt.status = "failed"
                            else:
                                if (
                                    budget.attempts[attempt - 1].get("status")
                                    == "started"
                                ):
                                    budget.finish(attempt, status="uncertain")
                                receipt.status = "uncertain"
                            receipt.generation_attempts = list(budget.attempts)
                            receipt.records = dict(records)
                            receipt.write(run_dir)
                            raise
                        except Exception:
                            if budget.attempts[attempt - 1].get("status") == "started":
                                budget.finish(attempt, status="uncertain")
                            receipt.generation_attempts = list(budget.attempts)
                            receipt.records = dict(records)
                            receipt.status = "uncertain"
                            receipt.write(run_dir)
                            raise
                    recovery_purpose = "knowledge-graph-connectivity-recovery"
                    if (
                        enrich_knowledge_graph
                        and recovery_purpose not in completed_purposes
                    ):
                        record_key = "what-should-stay-local"
                        adapter.records[record_key] = records[record_key]
                        adapter.open_conversation(record_key)
                        adapter.select_model(models["local"])
                        thread_id = records[record_key]
                        prior_turn_count = _durable_assistant_turn_count(thread_id)
                        attempt = budget.begin(
                            model=models["local"], purpose=recovery_purpose
                        )
                        receipt.status = "preparing"
                        receipt.generation_attempts = list(budget.attempts)
                        receipt.write(run_dir)
                        try:
                            adapter.send_prepared_prompt(
                                "Improve the real public-safe knowledge graph created in this "
                                "conversation. Preserve its three existing memories. Use the "
                                "real save_memory tool to add exactly four concise public-safe "
                                "nodes covering local conversations, knowledge ownership, "
                                "workflow execution boundaries, and explicit hosted-provider "
                                "handoffs. Then use link_memories at least eight times to create "
                                "meaningful directed, snake_case relationships among all seven "
                                "nodes. Do not merely describe a graph and do not use private "
                                "profile material. Finish by using explore_connections to verify "
                                "that the subgraph is connected."
                            )
                            total_turn_count = _durable_assistant_turn_count(thread_id)
                            turn_count = total_turn_count - prior_turn_count
                            if turn_count <= 0:
                                raise CaptureSafetyError(
                                    "knowledge graph recovery produced no durable model turns"
                                )
                            budget.finish(
                                attempt,
                                status="succeeded",
                                conversation_id=thread_id,
                                provider_call_count=turn_count,
                                durable_assistant_turn_count=turn_count,
                            )
                            for key in list(records):
                                if key.startswith("knowledge-entry-"):
                                    records.pop(key)
                            for index, entry_id in enumerate(
                                _knowledge_entry_ids(thread_id), start=1
                            ):
                                records[f"knowledge-entry-{index}"] = entry_id
                            receipt.generation_attempts = list(budget.attempts)
                            receipt.records = dict(records)
                            receipt.write(run_dir)
                        except Exception:
                            if budget.attempts[attempt - 1].get("status") == "started":
                                budget.finish(
                                    attempt,
                                    status="uncertain",
                                    conversation_id=thread_id,
                                )
                            receipt.generation_attempts = list(budget.attempts)
                            receipt.records = dict(records)
                            receipt.status = "uncertain"
                            receipt.write(run_dir)
                            raise
                    if not records.get("approval-request"):
                        _create_goal_and_approval(records)
                finally:
                    context.close()
                    browser.close()
        receipt.records = records
        receipt.generation_attempts = list(budget.attempts)
        receipt.status = "prepared"
        receipt.write(run_dir)
        return receipt
    except Exception:
        receipt.records = records
        receipt.generation_attempts = list(budget.attempts)
        receipt.status = (
            "uncertain" if budget.terminal_status == "uncertain" else "failed"
        )
        receipt.write(run_dir)
        raise


def capture(manifest: LandingStoryManifest, *, client: str, run_id: str) -> RunReceipt:
    if client != "nicegui":
        raise CaptureSafetyError("only the NiceGUI adapter is implemented")
    run_dir = require_contained(RUN_ROOT / run_id, RUN_ROOT)
    receipt = RunReceipt.read(run_dir)
    if receipt.status not in {"prepared", "captured", "processed", "validated"}:
        raise CaptureSafetyError("capture requires a successfully prepared run")
    profile = _normal_profile()
    require_profile_quiescent(profile)
    raw_dir = require_contained(run_dir / "raw", run_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    captures: list[dict[str, Any]] = []
    knowledge_entry_ids = tuple(
        str(value)
        for key, value in receipt.records.items()
        if key.startswith("knowledge-entry-") and value
    )
    with _owned_app(
        profile,
        run_dir,
        network_enabled=False,
        knowledge_entry_ids=knowledge_entry_ids,
    ) as (_process, port):
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            try:
                for scene in manifest.scenes:
                    viewport = manifest.viewports[scene.viewport]
                    video_dir = raw_dir / ".video" / scene.id
                    options: dict[str, Any] = {
                        "viewport": {
                            "width": viewport.width,
                            "height": viewport.height,
                        },
                        "reduced_motion": "reduce",
                    }
                    if "webm" in scene.outputs:
                        video_dir.mkdir(parents=True, exist_ok=True)
                        options.update(
                            record_video_dir=str(video_dir),
                            record_video_size={
                                "width": viewport.width,
                                "height": viewport.height,
                            },
                        )
                    context = browser.new_context(**options)
                    _block_external_routes(context)
                    NiceGuiAdapter.install_capture_privacy_filter(
                        context, receipt.records
                    )
                    page = context.new_page()
                    adapter = NiceGuiAdapter(
                        f"http://127.0.0.1:{port}",
                        page=page,
                        records=receipt.records,
                    )
                    adapter.prepare_scene(scene)
                    result = adapter.capture_scene(scene, raw_dir)
                    video = page.video if "webm" in scene.outputs else None
                    page.close()
                    video_name = ""
                    if video is not None:
                        source = Path(video.path())
                        destination = raw_dir / f"{scene.id}.raw.webm"
                        source.replace(destination)
                        video_name = destination.name
                    context.close()
                    captures.append(
                        {
                            "scene_id": scene.id,
                            "image": result.image_path.name,
                            "video": video_name,
                            "captured_at": result.captured_at,
                            "width": result.width,
                            "height": result.height,
                            "sha256": sha256_file(result.image_path),
                            "masks": list(result.masked_selectors),
                        }
                    )
            finally:
                browser.close()
    receipt.captures = captures
    receipt.client = client
    receipt.phase = "capture"
    receipt.status = "captured"
    receipt.write(run_dir)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    sub = parser.add_subparsers(dest="phase", required=True)
    pre = sub.add_parser("preflight")
    pre.add_argument("--client", default="nicegui", choices=("nicegui", "react"))
    prep = sub.add_parser("prepare")
    prep.add_argument("--client", default="nicegui", choices=("nicegui", "react"))
    prep.add_argument("--profile", type=Path)
    prep.add_argument("--run-id")
    prep.add_argument("--authorize-real-profile", action="store_true")
    prep.add_argument("--enrich-knowledge-graph", action="store_true")
    cap = sub.add_parser("capture")
    cap.add_argument("--client", default="nicegui", choices=("nicegui", "react"))
    cap.add_argument("--run-id", required=True)
    for name in ("process", "validate"):
        child = sub.add_parser(name)
        child.add_argument("--run-id", required=True)
    publish = sub.add_parser("publish")
    publish.add_argument("--run-id", required=True)
    publish.add_argument("--approve-reviewed-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = load_manifest(args.manifest.resolve())
    try:
        if args.phase == "preflight":
            result = preflight(manifest)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["ok"] else 1
        if args.phase == "prepare":
            receipt = prepare(
                manifest,
                client=args.client,
                authorize_real_profile=args.authorize_real_profile,
                selected_profile=args.profile,
                run_id=args.run_id,
                enrich_knowledge_graph=args.enrich_knowledge_graph,
            )
            print(
                json.dumps(
                    {"run_id": receipt.run_id, "status": receipt.status}, indent=2
                )
            )
            return 0
        run_dir = require_contained(RUN_ROOT / args.run_id, RUN_ROOT)
        if args.phase == "capture":
            receipt = capture(manifest, client=args.client, run_id=args.run_id)
            result = {"run_id": receipt.run_id, "captures": len(receipt.captures)}
        elif args.phase == "process":
            ffmpeg = _ffmpeg_executable()
            if not ffmpeg:
                raise CaptureSafetyError(
                    f"ffmpeg is required; set {FFMPEG_ENV} to a reviewed executable"
                )
            result = process_run(manifest, run_dir, ffmpeg=ffmpeg)
        elif args.phase == "validate":
            result = validate_run(manifest, run_dir)
        else:
            result = publish_run(
                manifest,
                run_dir,
                PUBLIC_ROOT,
                approve_reviewed_run=args.approve_reviewed_run,
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok", True) else 1
    except (CaptureSafetyError, TimeoutError) as exc:
        print(f"landing capture refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
