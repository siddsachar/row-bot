"""End-to-end CLI for the reviewed, real-profile landing story capture."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
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
from scripts.marketing.clients.nicegui import NiceGuiAdapter  # noqa: E402
from scripts.marketing.media_pipeline import process_run, publish_run, validate_run  # noqa: E402


MANIFEST_PATH = ROOT / "scripts" / "marketing" / "landing_story.yml"
RUN_ROOT = ROOT / "docs-build" / "marketing-capture"
PUBLIC_ROOT = ROOT / "docs" / "media" / "landing-story"
LAUNCH_SECRET_ENV = "ROW_BOT_LAUNCH_SECRET"


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


def _wait_ready(port: int, process: subprocess.Popen[Any], secret: str, timeout: float = 120.0) -> None:
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
) -> Iterator[tuple[subprocess.Popen[Any], int]]:
    """Launch one loopback child and stop only that exact process."""

    require_profile_quiescent(profile)
    port = _free_port()
    secret = secrets.token_urlsafe(32)
    log_dir = require_contained(run_dir / "logs", run_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = (log_dir / "app.stdout.log").open("w", encoding="utf-8")
    stderr = (log_dir / "app.stderr.log").open("w", encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join([str(SRC), str(ROOT), os.environ.get("PYTHONPATH", "")]),
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
        if url.startswith(("http://127.0.0.1:", "ws://127.0.0.1:", "data:", "blob:http://127.0.0.1:")):
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
        raise CaptureSafetyError(f"prepared Designer project was not persisted: {title}")
    return max(candidates)[1]


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
        runs = [run for run in get_recent_runs(limit=50) if str(run.get("task_name") or "") == title]
        if runs:
            latest = runs[0]
            status = str(latest.get("status") or "")
            if status.startswith("completed"):
                return str(latest.get("id") or ""), str(latest.get("thread_id") or "")
            if status in {"failed", "stopped", "cancelled", "uncertain"}:
                raise CaptureSafetyError(f"workflow ended with {status}")
        time.sleep(1)
    raise TimeoutError("workflow outcome is uncertain after the bounded wait")


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


def preflight(manifest: LandingStoryManifest) -> dict[str, Any]:
    profile = _normal_profile()
    checks: dict[str, Any] = {
        "manifest": "ok",
        "profile": "normal" if profile.is_dir() else "missing",
        "profile_quiescent": True,
        "browser": bool(_managed_browser()),
        "ffmpeg": bool(shutil.which("ffmpeg")),
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

        for role, model in (("local", manifest.models.local), ("frontier", manifest.models.frontier)):
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
    checks["ok"] = bool(
        checks["profile"] == "normal"
        and checks["profile_quiescent"]
        and checks["browser"]
        and checks["ffmpeg"]
        and all(item.get("configured") for item in checks["models"].values())
    )
    return checks


def prepare(
    manifest: LandingStoryManifest,
    *,
    client: str,
    authorize_real_profile: bool,
    selected_profile: Path | None,
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
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)
    run_dir = require_contained(RUN_ROOT / run_id, RUN_ROOT)
    run_dir.mkdir(parents=True, exist_ok=False)
    models = {"local": manifest.models.local, "frontier": manifest.models.frontier}
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
    budget = GenerationBudget(manifest.story.max_generation_attempts)
    records: dict[str, str] = {}
    try:
        from playwright.sync_api import sync_playwright

        with _owned_app(profile, run_dir, network_enabled=True) as (_process, port):
            with sync_playwright() as playwright:
                browser = _launch_browser(playwright)
                context = browser.new_context(
                    viewport={"width": manifest.viewports["desktop"].width, "height": manifest.viewports["desktop"].height},
                    reduced_motion="reduce",
                )
                page = context.new_page()
                adapter = NiceGuiAdapter(f"http://127.0.0.1:{port}", page=page, records=records)
                try:
                    for step in manifest.preparation:
                        model = models[step.model_role]
                        attempt = budget.begin(model=model, purpose=step.id)
                        try:
                            if step.record == "landing-visual-direction":
                                adapter.create_designer_project(
                                    title=step.title,
                                    model_ref=model,
                                    prompt=step.prompt,
                                )
                                record_id = _designer_project_id(profile, step.title)
                                records[step.record] = record_id
                                designer_thread = _thread_id(profile, f"🎨 {step.title}")
                                records["landing-visual-direction-thread"] = designer_thread
                                budget.finish(attempt, status="succeeded", conversation_id=designer_thread)
                            elif step.record == "weekly-sovereignty-watch":
                                adapter.create_workflow(title=step.title, model_ref=model, prompt=step.prompt)
                                task_id = _task_id(step.title)
                                records[step.record] = task_id
                                adapter.run_workflow(step.title)
                                operation_id, thread_id = _wait_workflow(step.title)
                                records["weekly-sovereignty-watch-thread"] = thread_id
                                budget.finish(
                                    attempt,
                                    status="succeeded",
                                    operation_id=operation_id,
                                    conversation_id=thread_id,
                                )
                            else:
                                adapter.create_conversation(
                                    title=step.title,
                                    model_ref=model,
                                    prompt=step.prompt,
                                )
                                thread_id = _thread_id(profile, step.title)
                                records[step.record] = thread_id
                                budget.finish(attempt, status="succeeded", conversation_id=thread_id)
                            receipt.generation_attempts = list(budget.attempts)
                            receipt.records = dict(records)
                            receipt.write(run_dir)
                        except TimeoutError:
                            budget.finish(attempt, status="uncertain")
                            raise
                    adapter.create_empty_conversation(title="Homepage implementation backlog")
                    records["homepage-implementation-backlog"] = _thread_id(profile, "Homepage implementation backlog")
                    adapter.create_empty_conversation(title="Release readiness")
                    records["release-readiness"] = _thread_id(profile, "Release readiness")
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
        receipt.status = "uncertain" if budget.terminal_status == "uncertain" else "failed"
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
    with _owned_app(profile, run_dir, network_enabled=False) as (_process, port):
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            try:
                for scene in manifest.scenes:
                    viewport = manifest.viewports[scene.viewport]
                    video_dir = raw_dir / ".video" / scene.id
                    options: dict[str, Any] = {
                        "viewport": {"width": viewport.width, "height": viewport.height},
                        "reduced_motion": "reduce",
                    }
                    if "webm" in scene.outputs:
                        video_dir.mkdir(parents=True, exist_ok=True)
                        options.update(
                            record_video_dir=str(video_dir),
                            record_video_size={"width": viewport.width, "height": viewport.height},
                        )
                    context = browser.new_context(**options)
                    _block_external_routes(context)
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
                        shutil.move(str(source), destination)
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
    prep.add_argument("--authorize-real-profile", action="store_true")
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
            )
            print(json.dumps({"run_id": receipt.run_id, "status": receipt.status}, indent=2))
            return 0
        run_dir = require_contained(RUN_ROOT / args.run_id, RUN_ROOT)
        if args.phase == "capture":
            receipt = capture(manifest, client=args.client, run_id=args.run_id)
            result = {"run_id": receipt.run_id, "captures": len(receipt.captures)}
        elif args.phase == "process":
            result = process_run(manifest, run_dir)
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
