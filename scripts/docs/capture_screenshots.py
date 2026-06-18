"""Capture and validate public docs screenshots with Playwright."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageStat

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


MANIFEST = ROOT / "docs-content" / "metadata" / "screenshots.yml"
OUTPUT_ROOT = ROOT / "docs-site" / "static" / "img" / "screenshots" / "generated"
REPORT = ROOT / "docs-build" / "reports" / "screenshots.json"

VIEWPORTS = {
    "desktop": {"width": 1440, "height": 960},
    "wide": {"width": 1680, "height": 1050},
    "mobile": {"width": 390, "height": 844},
}

SECRET_PATTERNS = [
    "sk-",
    "ghp_",
    "xoxb-",
    "AIza",
    "BEGIN PRIVATE KEY",
    "C:\\Users\\",
    "/Users/",
]


def _load_manifest() -> dict[str, Any]:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    screenshots = data.get("screenshots", {})
    return screenshots if isinstance(screenshots, dict) else {}


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ping(port: int, proc: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/launcher-ping"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"app exited during startup with code {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                body = response.read(512).decode("utf-8", errors="replace")
            if response.status == 200 and "row-bot" in body.lower():
                return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"/api/launcher-ping did not respond on port {port}")


def _launch_app(port: int, data_dir: Path, stack: ExitStack) -> subprocess.Popen:
    stdout_path = data_dir / "docs_capture_stdout.log"
    stderr_path = data_dir / "docs_capture_stderr.log"
    stdout_file = stack.enter_context(stdout_path.open("w", encoding="utf-8"))
    stderr_file = stack.enter_context(stderr_path.open("w", encoding="utf-8"))
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join([str(SRC), str(ROOT), os.environ.get("PYTHONPATH", "")]),
        "ROW_BOT_PORT": str(port),
        "ROW_BOT_HOST": "127.0.0.1",
        "ROW_BOT_DATA_DIR": str(data_dir),
        "ROW_BOT_DOCS_MODE": "1",
        "ROW_BOT_DOCS_FIXED_NOW": "2026-06-18T09:00:00Z",
        "ROW_BOT_DOCS_DISABLE_NETWORK": "1",
        "ROW_BOT_DOCS_DISABLE_AUTOSTART": "1",
        "ROW_BOT_DOCS_REDUCE_MOTION": "1",
        "ROW_BOT_DOCS_FAKE_PROVIDERS": "1",
    }
    return subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=str(ROOT),
        env=env,
        stdout=stdout_file,
        stderr=stderr_file,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _seed(data_dir: Path, scenario: str) -> None:
    subprocess.run(
        [sys.executable, "scripts/docs/seed_docs_data.py", "--data-dir", str(data_dir), "--scenario", scenario],
        cwd=str(ROOT),
        check=True,
    )


def _validate_image(path: Path, shot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"missing image {path}"]
    if path.stat().st_size < 8_000:
        errors.append("image file is unexpectedly small")
    try:
        with Image.open(path) as image:
            width, height = image.size
            if width < 500 or height < 300:
                errors.append(f"image dimensions too small: {width}x{height}")
            stat = ImageStat.Stat(image.convert("RGB"))
            variance = sum(stat.var) / max(1, len(stat.var))
            if variance < 12:
                errors.append(f"image appears blank or flat (variance {variance:.2f})")
    except Exception as exc:
        errors.append(f"could not inspect image: {exc}")
    combined = " ".join(str(shot.get(key, "")) for key in ("id", "title", "output", "alt"))
    for pattern in SECRET_PATTERNS:
        if pattern.lower() in combined.lower():
            errors.append(f"metadata contains blocked pattern {pattern}")
    return errors


def validate_committed(manifest: dict[str, Any]) -> dict[str, Any]:
    records = []
    for shot_id, shot in manifest.items():
        if not isinstance(shot, dict):
            continue
        output = OUTPUT_ROOT / str(shot.get("output") or f"{shot_id}.png")
        errors = []
        if shot.get("status") == "deferred":
            if not shot.get("reason"):
                errors.append("deferred screenshot is missing reason")
            if not shot.get("follow_up"):
                errors.append("deferred screenshot is missing follow_up")
        else:
            errors.extend(_validate_image(output, {"id": shot_id, **shot}))
        if not shot.get("alt"):
            errors.append("missing alt text")
        records.append(
            {
                "id": shot_id,
                "status": "ok" if not errors else "failed",
                "output": str(output.relative_to(ROOT)).replace("\\", "/"),
                "errors": errors,
                "deferred": shot.get("status") == "deferred",
            }
        )
    return _write_report(records, mode="validate")


def _browser_type(pw):
    executable = _managed_chromium()
    launch_options: dict[str, Any] = {"headless": True}
    if executable:
        launch_options["executable_path"] = executable
    try:
        return pw.chromium.launch(**launch_options)
    except Exception:
        if executable:
            return pw.chromium.launch(headless=True)
        raise


def _managed_chromium() -> str:
    try:
        from row_bot.mcp_client.requirements import playwright_browser_executable_path

        path = Path(playwright_browser_executable_path())
        if path.is_file():
            return str(path)
    except Exception:
        pass
    for candidate in (
        Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return ""


def capture(manifest: dict[str, Any], *, timeout: float = 90.0) -> dict[str, Any]:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    temp_dir = tempfile.TemporaryDirectory(prefix="row_bot_docs_capture_", ignore_cleanup_errors=True)
    data_dir = Path(temp_dir.name)
    port = _free_port()
    if _port_open(port):
        raise RuntimeError(f"selected port {port} is already in use")

    records: list[dict[str, Any]] = []
    proc: subprocess.Popen | None = None
    try:
        _seed(data_dir, "full")
        with ExitStack() as stack:
            proc = _launch_app(port, data_dir, stack)
            _wait_ping(port, proc, timeout)

            try:
                from playwright.sync_api import sync_playwright
            except Exception as exc:
                for shot_id, shot in manifest.items():
                    records.append(_blocked_record(shot_id, shot, f"Playwright import failed: {exc}"))
                return _write_report(records, mode="capture")

            with sync_playwright() as pw:
                try:
                    browser = _browser_type(pw)
                except Exception as exc:
                    for shot_id, shot in manifest.items():
                        records.append(_blocked_record(shot_id, shot, f"Chromium launch failed: {exc}"))
                    return _write_report(records, mode="capture")
                try:
                    for shot_id, shot in manifest.items():
                        if not isinstance(shot, dict):
                            continue
                        if shot.get("status") == "deferred":
                            records.append(_blocked_record(shot_id, shot, str(shot.get("reason") or "deferred")))
                            continue
                        records.append(_capture_one(browser, port, shot_id, shot))
                finally:
                    browser.close()
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        temp_dir.cleanup()
    return _write_report(records, mode="capture")


def _blocked_record(shot_id: str, shot: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "id": shot_id,
        "title": shot.get("title", shot_id),
        "status": "deferred",
        "deferred": True,
        "reason": reason,
        "output": str((OUTPUT_ROOT / str(shot.get("output") or f"{shot_id}.png")).relative_to(ROOT)).replace("\\", "/"),
        "errors": [],
    }


def _capture_one(browser, port: int, shot_id: str, shot: dict[str, Any]) -> dict[str, Any]:
    viewport = VIEWPORTS.get(str(shot.get("viewport") or "desktop"), VIEWPORTS["desktop"])
    page = browser.new_page(viewport=viewport)
    output = OUTPUT_ROOT / str(shot.get("output") or f"{shot_id}.png")
    errors: list[str] = []
    try:
        url = f"http://127.0.0.1:{port}{shot.get('route')}"
        page.goto(url, wait_until="networkidle", timeout=30_000)
        wait_for = str(shot.get("wait_for") or shot.get("capture_selector") or "body")
        page.wait_for_selector(wait_for, timeout=15_000)
        for text in shot.get("expected_text") or []:
            if not page.get_by_text(str(text)).count():
                errors.append(f"expected visible text not found: {text}")
        masks = []
        for selector in shot.get("masks") or []:
            masks.extend(page.locator(str(selector)).all())
        selector = str(shot.get("capture_selector") or "body")
        locator = page.locator(selector).first
        locator.screenshot(path=str(output), animations="disabled", mask=masks)
        errors.extend(_validate_image(output, {"id": shot_id, **shot}))
    except Exception as exc:
        errors.append(str(exc))
    finally:
        page.close()
    return {
        "id": shot_id,
        "title": shot.get("title", shot_id),
        "status": "ok" if not errors else "failed",
        "deferred": False,
        "output": str(output.relative_to(ROOT)).replace("\\", "/"),
        "errors": errors,
    }


def _write_report(records: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "mode": mode,
        "total": len(records),
        "captured": sum(1 for item in records if item.get("status") == "ok" and not item.get("deferred")),
        "failed": sum(1 for item in records if item.get("status") == "failed"),
        "deferred": sum(1 for item in records if item.get("deferred")),
        "records": records,
    }
    REPORT.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote screenshot report to {REPORT}")
    if summary["failed"]:
        for item in records:
            if item.get("errors"):
                print(f"ERROR {item['id']}: {'; '.join(item['errors'])}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture or validate docs screenshots")
    parser.add_argument("--validate-only", action="store_true", help="Validate committed screenshot files without launching the app")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    manifest = _load_manifest()
    summary = validate_committed(manifest) if args.validate_only else capture(manifest, timeout=args.timeout)
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
