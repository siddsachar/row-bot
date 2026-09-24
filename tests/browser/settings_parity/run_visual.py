"""Guarded NiceGUI/React Settings parity evidence runner.

The normal mode seeds one isolated synthetic profile, starts one loopback
Row-Bot process, captures every NiceGUI Settings owner first and then the React
routes, and rejects browser writes, external requests, secret rendering, and
meaningful changes to the mounted data directory.  A retained legacy mode can
still validate an explicitly authorized canonical profile, but parity work must
use ``--synthetic-data-dir``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
EVIDENCE_ROOT = (
    ROOT
    / ".local"
    / "evidence"
    / "settings-remaining-parity"
)
AXE_SOURCE = ROOT / "frontend" / "node_modules" / "axe-core" / "axe.min.js"
LAUNCH_SECRET_ENV = "ROW_BOT_LAUNCH_SECRET"
AUTHORIZED_REAL_DATA = Path.home() / ".row-bot"
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "phone": {"width": 390, "height": 844},
}
NICEGUI_PAGES = (
    "Buddy",
    "Voice",
    "System",
    "Tracker",
    "Documents",
    "Tools",
    "Skills",
    "Accounts",
    "Channels",
    "Utilities",
    "MCP",
    "Plugins",
    "Preferences",
)
REACT_PAGES = (
    "Buddy",
    "Goals",
    "Voice",
    "System",
    "Tracker",
    "Documents",
    "Tools",
    "Skills",
    "Accounts",
    "Channels",
    "Utilities",
    "MCP",
    "Plugins",
    "Preferences",
)
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
SENSITIVE_HINT = re.compile(
    r"(?:api.?key|auth.?token|access.?token|client.?secret|credential|password|secret)",
    re.IGNORECASE,
)
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
OBSERVATIONAL_POST_PATHS = frozenset({"/api/v1/handshake"})
DIAGNOSTIC_POST_PATHS = frozenset({"/api/client-error"})


class CaptureSafetyError(RuntimeError):
    """Raised when a real-data capture crosses an observational boundary."""


@dataclass(frozen=True)
class CaptureTarget:
    surface: str
    name: str
    route: str
    root_selector: str

    @property
    def slug(self) -> str:
        return self.name.casefold().replace(" ", "-")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_app(
    port: int, process: subprocess.Popen[Any], secret: str, timeout: float
) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/launcher-ping",
        headers={"Authorization": f"Bearer {secret}"},
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Row-Bot exited during startup with code {process.returncode}"
            )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                body = response.read(512).decode("utf-8", errors="replace")
            if response.status == 200 and "row-bot" in body.casefold():
                return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError(
        f"Row-Bot did not answer its launcher ping within {timeout:.0f}s"
    )


def _canonical_real_data_dir(requested: Path, authorized: bool) -> Path:
    if not authorized:
        raise CaptureSafetyError(
            "real-data capture requires --authorize-real-data-capture"
        )
    sys.path.insert(0, str(SRC))
    from row_bot.data_paths import default_data_dir

    expected = AUTHORIZED_REAL_DATA.resolve()
    default = default_data_dir().resolve()
    actual = requested.expanduser().resolve()
    if expected != default:
        raise CaptureSafetyError(
            f"runner authorization path {expected} does not match default_data_dir() {default}"
        )
    if actual != expected:
        raise CaptureSafetyError(
            "--authorize-real-data-capture is valid only for the canonical configured directory"
        )
    if not actual.is_dir():
        raise CaptureSafetyError(f"configured data directory does not exist: {actual}")
    return actual


def _canonical_synthetic_data_dir(requested: Path) -> Path:
    """Accept only an isolated directory below the repository's ignored roots."""

    actual = requested.expanduser().resolve()
    allowed_roots = (
        (ROOT / ".tmp").resolve(),
        EVIDENCE_ROOT.resolve(),
    )
    if not any(actual == root or root in actual.parents for root in allowed_roots):
        raise CaptureSafetyError(
            "synthetic parity data must be inside .tmp or the ignored evidence root"
        )
    if actual == ROOT.resolve():
        raise CaptureSafetyError("the repository root cannot be used as synthetic data")
    actual.mkdir(parents=True, exist_ok=True)
    return actual


def _seed_synthetic_data(data_dir: Path) -> None:
    """Seed deterministic inert metadata without reading any configured profile."""

    marker = data_dir / ".settings-parity-synthetic-v1"
    if marker.is_file():
        return
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            (str(SRC), str(ROOT), os.environ.get("PYTHONPATH", ""))
        ),
        "ROW_BOT_DATA_DIR": str(data_dir),
        "ROW_BOT_DOCS_CAPTURE": "1",
        "ROW_BOT_DOCS_DISABLE_NETWORK": "1",
        "ROW_BOT_DOCS_FAKE_PROVIDERS": "1",
        "ROW_BOT_DOCS_REAL_DATA": "0",
        "ROW_BOT_TEST_MODE": "1",
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "docs" / "seed_real_app_demo_data.py"),
            "--data-dir",
            str(data_dir),
            "--scenario",
            "full",
        ],
        cwd=str(ROOT),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode:
        detail = _redact((completed.stderr or completed.stdout).strip())
        raise CaptureSafetyError(f"synthetic parity seed failed: {detail}")
    marker.write_text("synthetic settings parity fixture\n", encoding="utf-8")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _data_manifest(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda value: str(value).casefold()):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            row: dict[str, Any] = {
                "path": path.relative_to(root).as_posix(),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
            if stat.st_size <= 16 * 1024 * 1024:
                row["sha256"] = _hash_file(path)
            files.append(row)
        except (OSError, PermissionError) as exc:
            errors.append({"path": str(path.relative_to(root)), "error": str(exc)})
    return {"root": str(root), "files": files, "errors": errors}


def _is_ephemeral_data_path(relative_path: str) -> bool:
    lowered = relative_path.casefold().replace("\\", "/")
    name = lowered.rsplit("/", 1)[-1]
    # The ingestion supervisor records its own lease/heartbeat in jobs.db even
    # with autostart disabled. Browser mutations remain independently blocked
    # and audited, so this operational heartbeat is not profile content.
    return (
        lowered.startswith(("logs/", "crashes/", "plugin_logs/"))
        or lowered == "document_ingestion/jobs.db"
        or name.endswith((".log", ".pid", ".lock", ".tmp", "-shm", "-wal"))
        or "/__pycache__/" in f"/{lowered}/"
    )


def _manifest_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old = {row["path"]: row for row in before.get("files", [])}
    new = {row["path"]: row for row in after.get("files", [])}
    changed: list[dict[str, Any]] = []
    for name in sorted(set(old) | set(new), key=str.casefold):
        prior = old.get(name)
        current = new.get(name)
        if prior == current:
            continue
        metadata_only = bool(
            prior
            and current
            and prior.get("size") == current.get("size")
            and prior.get("sha256")
            and prior.get("sha256") == current.get("sha256")
        )
        changed.append(
            {
                "path": name,
                "kind": "metadata-only"
                if metadata_only
                else "created"
                if prior is None
                else "removed"
                if current is None
                else "changed",
                "ephemeral": metadata_only or _is_ephemeral_data_path(name),
                "before": prior,
                "after": current,
            }
        )
    return {
        "changes": changed,
        "meaningful": [row for row in changed if not row["ephemeral"]],
    }


def _wait_for_synthetic_startup(data_dir: Path, timeout: float = 12.0) -> dict[str, Any]:
    """Wait for startup-owned schema/reconciliation writes before evidence."""

    started = time.monotonic()
    prior: dict[str, tuple[int, str | None]] | None = None
    stable = 0
    while time.monotonic() - started < timeout:
        manifest = _data_manifest(data_dir)
        semantic = {
            row["path"]: (int(row["size"]), row.get("sha256"))
            for row in manifest["files"]
            if not _is_ephemeral_data_path(row["path"])
        }
        if time.monotonic() - started >= 6.0 and semantic == prior:
            stable += 1
            if stable >= 2:
                return manifest
        else:
            stable = 0
        prior = semantic
        time.sleep(0.5)
    return _data_manifest(data_dir)


def _launch_app(
    port: int,
    data_dir: Path,
    stack: ExitStack,
    run_root: Path,
    *,
    synthetic: bool,
) -> tuple[subprocess.Popen[Any], str]:
    stdout = stack.enter_context(
        (run_root / "server.stdout.log").open("w", encoding="utf-8")
    )
    stderr = stack.enter_context(
        (run_root / "server.stderr.log").open("w", encoding="utf-8")
    )
    launch_secret = secrets.token_urlsafe(32)
    environment = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join(
            (str(SRC), str(ROOT), os.environ.get("PYTHONPATH", ""))
        ),
        "ROW_BOT_PORT": str(port),
        "ROW_BOT_HOST": "127.0.0.1",
        "ROW_BOT_DATA_DIR": str(data_dir),
        "ROW_BOT_DOCS_CAPTURE": "1",
        "ROW_BOT_DOCS_DISABLE_NETWORK": "1",
        "ROW_BOT_DOCS_DISABLE_AUTOSTART": "1",
        "ROW_BOT_DOCS_REDUCE_MOTION": "1",
        "ROW_BOT_DOCS_FAKE_PROVIDERS": "1" if synthetic else "0",
        "ROW_BOT_DOCS_REAL_DATA": "0" if synthetic else "1",
        "ROW_BOT_TEST_MODE": "1" if synthetic else "0",
        "ROW_BOT_AUTO_START_OLLAMA": "0",
        "ROW_BOT_NATIVE": "0",
        "ROW_BOT_BROWSER_HEADLESS": "1",
        LAUNCH_SECRET_ENV: launch_secret,
    }
    process = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=str(ROOT),
        env=environment,
        stdout=stdout,
        stderr=stderr,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return process, launch_secret


def _redact(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[MASKED SECRET]", redacted)
    return redacted


def _safe_request_label(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _request_policy(url: str, method: str) -> tuple[bool, str]:
    """Return whether a browser request may continue and its audit category."""

    parsed = urlsplit(url)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    normalized_method = method.upper()
    if not loopback:
        return False, "external"
    if normalized_method in READ_METHODS:
        return True, "loopback read"
    if normalized_method == "POST" and parsed.path in OBSERVATIONAL_POST_PATHS:
        return True, "observational post"
    if normalized_method == "POST" and parsed.path in DIAGNOSTIC_POST_PATHS:
        return False, "diagnostic request"
    return False, "non-read request"


def _scroll_positions(
    scroll_height: int, client_height: int, overlap: int = 80
) -> list[int]:
    """Return monotonically increasing offsets which include each boundary once."""

    height = max(int(client_height), 1)
    total = max(int(scroll_height), height)
    last = max(total - height, 0)
    step = max(height - max(int(overlap), 0), 1)
    return sorted({*range(0, last, step), last})


def _scroll_owner_selector(target: CaptureTarget) -> str:
    """Prefer the Settings shell scroller over unrelated application regions."""

    return ".settings-shell-body" if target.surface == "react" else ""


def _targets(pages: set[str] | None) -> tuple[list[CaptureTarget], list[CaptureTarget]]:
    def selected(name: str) -> bool:
        return pages is None or name.casefold() in pages

    nicegui = [
        CaptureTarget(
            surface="nicegui",
            name=name,
            route=f"/?settings_tab={name}",
            root_selector='[data-docs-id="settings-dialog"]',
        )
        for name in NICEGUI_PAGES
        if selected(name)
    ]
    react = [
        CaptureTarget(
            surface="react",
            name=name,
            route=f"/app-v2/settings/{name.casefold()}",
            root_selector='[data-settings-root="1"], main',
        )
        for name in REACT_PAGES
        if selected(name)
    ]
    return nicegui, react


def _sensitive_masks(page: Any) -> list[Any]:
    selectors = (
        'input[type="password"]',
        '[data-sensitive="true"]',
        'input[name*="token" i]',
        'input[name*="secret" i]',
        'input[name*="key" i]',
        'textarea[name*="token" i]',
        'textarea[name*="secret" i]',
        'textarea[name*="key" i]',
    )
    # Playwright masks matching nodes even when they are inside a closed
    # disclosure, using their latent layout boxes. Restrict the mask set to
    # rendered controls so closed credential panels do not leave synthetic
    # purple bars in the parity evidence.
    return [page.locator(f"{selector}:visible") for selector in selectors]


def _page_snapshot(page: Any, target: CaptureTarget, viewport: str) -> dict[str, Any]:
    payload = page.evaluate(
        r"""({selector, sensitivePattern}) => {
          const root = document.querySelector(selector) || document.body;
          const pattern = new RegExp(sensitivePattern, 'i');
          const styleRecord = (el) => {
            const style = getComputedStyle(el);
            return {
              fontFamily: style.fontFamily,
              fontSize: style.fontSize,
              fontWeight: style.fontWeight,
              lineHeight: style.lineHeight,
              color: style.color,
              backgroundColor: style.backgroundColor,
              borderColor: style.borderColor,
              borderWidth: style.borderWidth,
              borderRadius: style.borderRadius,
              padding: style.padding,
              gap: style.gap,
            };
          };
          const visibleText = (el) => (el.innerText || el.textContent || '').trim();
          const accessibleLabel = (el) => {
            const explicit = el.getAttribute('aria-label') || el.getAttribute('title') || '';
            if (explicit) return explicit;
            if (el.labels?.length) return Array.from(el.labels).map(visibleText).filter(Boolean).join(' ');
            const labelledBy = el.getAttribute('aria-labelledby');
            if (labelledBy) {
              const value = labelledBy.split(/\s+/).map((id) => document.getElementById(id))
                .filter(Boolean).map(visibleText).filter(Boolean).join(' ');
              if (value) return value;
            }
            return visibleText(el) || el.getAttribute('placeholder') || '';
          };
          const sectionLabel = (el) => {
            let cursor = el;
            while (cursor && cursor !== root) {
              const heading = cursor.querySelector?.(':scope > h2, :scope > h3, :scope > h4, :scope > [role="heading"]');
              if (heading && visibleText(heading)) return visibleText(heading).slice(0, 160);
              cursor = cursor.parentElement;
            }
            const headings = Array.from(root.querySelectorAll('h2, h3, h4, [role="heading"]'));
            let prior = '';
            for (const heading of headings) {
              if (heading.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) {
                prior = visibleText(heading) || prior;
              }
            }
            return prior.slice(0, 160);
          };
          const controls = Array.from(root.querySelectorAll(
            'button, input, select, textarea, [role="button"], [role="switch"], [role="checkbox"], [role="radio"], details, summary, a[href]'
          )).map((el, index) => {
            const rect = el.getBoundingClientRect();
            const combined = [el.type, el.name, el.id, el.getAttribute('aria-label'),
              el.getAttribute('autocomplete'), el.getAttribute('placeholder'), el.dataset.sensitive]
              .filter(Boolean).join(' ');
            const sensitive = el.type === 'password' || el.dataset.sensitive === 'true' || pattern.test(combined);
            let value = 'value' in el ? String(el.value ?? '') : '';
            if (sensitive && value) value = '[MASKED]';
            const icon = el.querySelector?.('.q-icon, .material-icons, [data-lucide]');
            return {
              index,
              tag: el.tagName.toLowerCase(),
              role: el.getAttribute('role') || '',
              type: el.type || '',
              name: el.name || '',
              id: el.id || '',
              label: accessibleLabel(el).slice(0, 240),
              tooltip: (el.getAttribute('title') || el.getAttribute('aria-description') || '').slice(0, 240),
              icon: icon ? (icon.getAttribute('data-lucide') || visibleText(icon)).slice(0, 80) : '',
              section: sectionLabel(el),
              value,
              checked: 'checked' in el ? Boolean(el.checked) : null,
              expanded: el.getAttribute('aria-expanded'),
              disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
              visible: rect.width > 0 && rect.height > 0,
              rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
              style: styleRecord(el),
            };
          });
          const rootRect = root.getBoundingClientRect();
          const overflow = Array.from(root.querySelectorAll('*')).flatMap((el) => {
            const rect = el.getBoundingClientRect();
            if (rect.right <= window.innerWidth + 1 && rect.left >= -1) return [];
            return [{tag: el.tagName.toLowerCase(), id: el.id || '',
              className: typeof el.className === 'string' ? el.className.slice(0, 180) : '',
              left: rect.left, right: rect.right, width: rect.width}];
          }).slice(0, 100);
          const scrollCandidates = [root, ...root.querySelectorAll('*')].flatMap((el) => {
            const style = getComputedStyle(el);
            if (el.scrollHeight <= el.clientHeight + 8 &&
                !['auto', 'scroll', 'hidden', 'clip'].includes(style.overflowY)) return [];
            const rect = el.getBoundingClientRect();
            return [{tag: el.tagName.toLowerCase(), id: el.id || '',
              className: typeof el.className === 'string' ? el.className.slice(0, 180) : '',
              overflowY: style.overflowY, clientHeight: el.clientHeight,
              scrollHeight: el.scrollHeight, top: rect.top, bottom: rect.bottom}];
          }).slice(0, 100);
          return {
            title: document.title,
            url: location.pathname + location.search,
            text: (root.innerText || '').slice(0, 200000),
            root: {selector, x: rootRect.x, y: rootRect.y, width: rootRect.width,
              height: rootRect.height, scrollWidth: root.scrollWidth, scrollHeight: root.scrollHeight,
              clientWidth: root.clientWidth, clientHeight: root.clientHeight, style: styleRecord(root)},
            document: {scrollWidth: document.documentElement.scrollWidth,
              scrollHeight: document.documentElement.scrollHeight,
              viewportWidth: window.innerWidth, viewportHeight: window.innerHeight},
            controls,
            overflow,
            scrollCandidates,
          };
        }""",
        {"selector": target.root_selector, "sensitivePattern": SENSITIVE_HINT.pattern},
    )
    payload["surface"] = target.surface
    payload["page"] = target.name
    payload["viewport"] = viewport
    payload["text"] = _redact(str(payload.get("text") or ""))
    for control in payload.get("controls", []):
        control["label"] = _redact(str(control.get("label") or ""))
        control["tooltip"] = _redact(str(control.get("tooltip") or ""))
        control["value"] = _redact(str(control.get("value") or ""))
    return payload


def _axe_snapshot(page: Any, root_selector: str) -> dict[str, Any]:
    if not AXE_SOURCE.is_file():
        return {"error": f"axe source not found: {AXE_SOURCE}"}
    # Chromium evaluates this through the automation world. Unlike an inline
    # script tag, it does not ask the application's CSP to allow test code.
    page.evaluate(AXE_SOURCE.read_text(encoding="utf-8"))
    result = page.evaluate(
        """async (selector) => {
          const root = document.querySelector(selector) || document.body;
          return await window.axe.run(root, {
            runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']},
            resultTypes: ['violations', 'incomplete', 'passes'],
          });
        }""",
        root_selector,
    )
    return {
        "testEngine": result.get("testEngine"),
        "testEnvironment": result.get("testEnvironment"),
        "violations": result.get("violations", []),
        "incomplete": result.get("incomplete", []),
        "pass_count": len(result.get("passes", [])),
    }


def _scroll_segments(
    page: Any, target: CaptureTarget, output_dir: Path, stem: str, masks: list[Any]
) -> list[dict[str, Any]]:
    scroll = page.evaluate(
        """({rootSelector, ownerSelector}) => {
          const root = document.querySelector(rootSelector) || document.body;
          const nodes = [root, ...root.querySelectorAll('*')].filter((el) => {
            const style = getComputedStyle(el);
            return el.scrollHeight > el.clientHeight + 8 &&
              ['auto', 'scroll'].includes(style.overflowY) && el.clientHeight > 100;
          });
          const explicit = ownerSelector ? root.querySelector(ownerSelector) : null;
          const chosen = explicit && explicit.scrollHeight > explicit.clientHeight + 8
            ? explicit
            : nodes.sort((a, b) => b.scrollHeight - a.scrollHeight)[0] ||
              document.scrollingElement;
          if (!chosen) return null;
          chosen.setAttribute('data-settings-parity-scroll-owner', '1');
          chosen.scrollTop = 0;
          return {scrollHeight: chosen.scrollHeight, clientHeight: chosen.clientHeight};
        }""",
        {
            "rootSelector": target.root_selector,
            "ownerSelector": _scroll_owner_selector(target),
        },
    )
    if not scroll:
        return []
    positions = _scroll_positions(scroll["scrollHeight"], scroll["clientHeight"])
    records: list[dict[str, Any]] = []
    for index, position in enumerate(positions):
        actual = page.evaluate(
            """(top) => { const el = document.querySelector('[data-settings-parity-scroll-owner="1"]');
              if (!el) return -1; el.scrollTop = top; return el.scrollTop; }""",
            position,
        )
        page.wait_for_timeout(80)
        path = output_dir / f"{stem}-scroll-{index:03d}.png"
        page.screenshot(
            path=str(path),
            animations="disabled",
            full_page=False,
            mask=masks,
            mask_color="#7b1fa2",
        )
        records.append(
            {"path": path.relative_to(EVIDENCE_ROOT).as_posix(), "scroll_top": actual}
        )
    return records


def _capture_target(
    browser: Any,
    base_url: str,
    target: CaptureTarget,
    viewport_name: str,
    output_dir: Path,
    metrics_dir: Path,
    failure_dir: Path,
) -> dict[str, Any]:
    viewport = VIEWPORTS[viewport_name]
    context = browser.new_context(
        viewport=viewport, reduced_motion="reduce", color_scheme="dark"
    )
    blocked: list[dict[str, str]] = []
    allowed_observational: list[dict[str, str]] = []
    console: list[dict[str, str]] = []
    page_errors: list[str] = []

    def route_request(route: Any, request: Any) -> None:
        method = request.method.upper()
        allowed, reason = _request_policy(request.url, method)
        request_row = {
            "method": method,
            "url": _safe_request_label(request.url),
            "reason": reason,
        }
        if not allowed:
            blocked.append(request_row)
            route.abort("blockedbyclient")
            return
        if reason == "observational post":
            allowed_observational.append(request_row)
        route.continue_()

    context.route("**/*", route_request)
    page = context.new_page()
    page.on(
        "console",
        lambda message: console.append(
            {"type": message.type, "text": _redact(message.text)}
        ),
    )
    page.on("pageerror", lambda error: page_errors.append(_redact(str(error))))
    stem = f"{target.slug}-{viewport_name}"
    record: dict[str, Any] = {
        "surface": target.surface,
        "page": target.name,
        "viewport": viewport_name,
        "route": target.route,
        "status": "failed",
    }
    try:
        page.goto(base_url + target.route, wait_until="networkidle", timeout=45_000)
        page.locator(target.root_selector).first.wait_for(
            state="visible", timeout=30_000
        )
        page.wait_for_timeout(350)
        page.evaluate("window.scrollTo(0, 0)")
        masks = _sensitive_masks(page)
        output_dir.mkdir(parents=True, exist_ok=True)
        full_path = output_dir / f"{stem}-full.png"
        page.screenshot(
            path=str(full_path),
            animations="disabled",
            full_page=target.surface == "react",
            mask=masks,
            mask_color="#7b1fa2",
        )
        snapshot = _page_snapshot(page, target, viewport_name)
        axe = _axe_snapshot(page, target.root_selector)
        segments = _scroll_segments(page, target, output_dir, stem, masks)
        try:
            aria = page.locator(target.root_selector).first.aria_snapshot(timeout=5_000)
        except Exception as exc:
            aria = f"ARIA snapshot unavailable: {exc}"
        _write_json(metrics_dir / f"{stem}-dom.json", snapshot)
        _write_json(metrics_dir / f"{stem}-axe.json", axe)
        _write_text(metrics_dir / f"{stem}-aria.txt", _redact(str(aria)))
        _write_json(
            metrics_dir / f"{stem}-browser.json",
            {
                "allowed_observational": allowed_observational,
                "blocked": blocked,
                "console": console,
                "page_errors": page_errors,
            },
        )
        secret_findings = [
            pattern.pattern
            for pattern in SECRET_PATTERNS
            if pattern.search(snapshot["text"])
        ]
        if secret_findings:
            raise CaptureSafetyError(
                f"secret-shaped text remained after masking: {secret_findings}"
            )
        if any(row["reason"] == "non-read request" for row in blocked):
            raise CaptureSafetyError(
                "page attempted a consequential HTTP request during passive capture"
            )
        record.update(
            {
                "status": "ok",
                "full": full_path.relative_to(EVIDENCE_ROOT).as_posix(),
                "scroll_segments": segments,
                "controls": len(snapshot.get("controls", [])),
                "visible_controls": sum(
                    1 for item in snapshot.get("controls", []) if item.get("visible")
                ),
                "overflow_items": len(snapshot.get("overflow", [])),
                "axe_violations": len(axe.get("violations", [])),
                "axe_incomplete": len(axe.get("incomplete", [])),
                "blocked_external_requests": sum(
                    1 for row in blocked if row["reason"] == "external"
                ),
                "blocked_diagnostic_requests": sum(
                    1 for row in blocked if row["reason"] == "diagnostic request"
                ),
                "observational_posts": len(allowed_observational),
                "console_errors": sum(1 for row in console if row["type"] == "error"),
                "page_errors": len(page_errors),
            }
        )
    except Exception as exc:
        record["error"] = _redact(str(exc))
        try:
            failure_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(failure_dir / f"{target.surface}-{stem}.png"),
                animations="disabled",
                full_page=False,
                mask=_sensitive_masks(page),
                mask_color="#7b1fa2",
            )
            _write_text(
                failure_dir / f"{target.surface}-{stem}.html", _redact(page.content())
            )
            _write_json(
                failure_dir / f"{target.surface}-{stem}.json",
                {
                    "record": record,
                    "allowed_observational": allowed_observational,
                    "blocked": blocked,
                    "console": console,
                    "page_errors": page_errors,
                },
            )
        except Exception as failure_exc:
            record["failure_capture_error"] = _redact(str(failure_exc))
    finally:
        context.close()
    return record


def _parse_pages(raw: str) -> set[str] | None:
    if raw.strip().casefold() == "all":
        return None
    requested = {item.strip().casefold() for item in raw.split(",") if item.strip()}
    known = {item.casefold() for item in REACT_PAGES}
    unknown = requested - known
    if unknown:
        raise ValueError(f"unknown Settings pages: {', '.join(sorted(unknown))}")
    return requested


def _parse_viewports(raw: str) -> list[str]:
    requested = [item.strip().casefold() for item in raw.split(",") if item.strip()]
    unknown = set(requested) - set(VIEWPORTS)
    if unknown:
        raise ValueError(f"unknown viewports: {', '.join(sorted(unknown))}")
    return requested


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.synthetic_data_dir:
        if args.authorize_real_data_capture or args.data_dir:
            raise CaptureSafetyError(
                "--synthetic-data-dir cannot be combined with real-data options"
            )
        synthetic = True
        data_dir = _canonical_synthetic_data_dir(Path(args.synthetic_data_dir))
        _seed_synthetic_data(data_dir)
    else:
        if not args.data_dir:
            raise CaptureSafetyError(
                "parity capture requires --synthetic-data-dir"
            )
        synthetic = False
        data_dir = _canonical_real_data_dir(
            Path(args.data_dir),
            bool(args.authorize_real_data_capture),
        )
    pages = _parse_pages(args.pages)
    viewports = _parse_viewports(args.viewports)
    nicegui_targets, react_targets = _targets(pages)
    if args.stage == "auto":
        before = EVIDENCE_ROOT / "before" / "react"
        stage = (
            "before"
            if not before.exists() or not any(before.glob("*.png"))
            else "candidate"
        )
    else:
        stage = args.stage
    run_id = f"{stage}-{_timestamp()}"
    run_root = EVIDENCE_ROOT / "metrics" / "runs" / run_id
    failure_root = EVIDENCE_ROOT / "failures" / run_id
    reference_root = EVIDENCE_ROOT / "reference" / "nicegui"
    react_root = EVIDENCE_ROOT / stage / "react"
    metrics_root = EVIDENCE_ROOT / "metrics" / stage
    for directory in (run_root, failure_root, reference_root, react_root, metrics_root):
        directory.mkdir(parents=True, exist_ok=True)
    launch_pre = _data_manifest(data_dir)
    _write_json(run_root / "data-manifest-launch-pre.json", launch_pre)
    port = _free_port()
    process: subprocess.Popen[Any] | None = None
    records: list[dict[str, Any]] = []
    pre: dict[str, Any] | None = None
    post: dict[str, Any] | None = None
    try:
        with ExitStack() as stack:
            process, launch_secret = _launch_app(
                port, data_dir, stack, run_root, synthetic=synthetic
            )
            _wait_for_app(port, process, launch_secret, args.timeout)
            pre = (
                _wait_for_synthetic_startup(data_dir)
                if synthetic
                else _data_manifest(data_dir)
            )
            _write_json(run_root / "data-manifest-pre.json", pre)
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                if args.engine != "chromium":
                    raise ValueError(
                        "the authorized runner currently permits only the Chromium engine"
                    )
                browser = playwright.chromium.launch(
                    headless=True, channel=args.channel
                )
                try:
                    base_url = f"http://127.0.0.1:{port}"
                    for target in nicegui_targets:
                        for viewport in viewports:
                            records.append(
                                _capture_target(
                                    browser,
                                    base_url,
                                    target,
                                    viewport,
                                    reference_root,
                                    metrics_root / "nicegui",
                                    failure_root,
                                )
                            )
                    after_nicegui = _data_manifest(data_dir)
                    _write_json(
                        run_root / "data-manifest-after-nicegui.json", after_nicegui
                    )
                    for target in react_targets:
                        for viewport in viewports:
                            records.append(
                                _capture_target(
                                    browser,
                                    base_url,
                                    target,
                                    viewport,
                                    react_root,
                                    metrics_root / "react",
                                    failure_root,
                                )
                            )
                finally:
                    browser.close()
            post = _data_manifest(data_dir)
            _write_json(run_root / "data-manifest-post.json", post)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    if pre is None:
        pre = launch_pre
    if post is None:
        post = _data_manifest(data_dir)
        _write_json(run_root / "data-manifest-post.json", post)
    change_report = _manifest_changes(pre, post)
    _write_json(run_root / "data-manifest-diff.json", change_report)
    summary = {
        "run_id": run_id,
        "stage": stage,
        "data_dir": str(data_dir),
        "process_count": 1,
        "binding": "127.0.0.1",
        "seeded": synthetic,
        "profile_kind": "synthetic" if synthetic else "authorized-real",
        "network_disabled": True,
        "autostart_disabled": True,
        "native_outputs_suppressed": True,
        "records": records,
        "ok": sum(1 for row in records if row["status"] == "ok"),
        "failed": sum(1 for row in records if row["status"] != "ok"),
        "meaningful_data_changes": change_report["meaningful"],
    }
    _write_json(run_root / "summary.json", summary)
    if summary["failed"]:
        raise CaptureSafetyError(
            f"{summary['failed']} capture(s) failed; inspect {failure_root.relative_to(ROOT)}"
        )
    if change_report["meaningful"]:
        raise CaptureSafetyError(
            f"capture changed configured data; inspect {run_root / 'data-manifest-diff.json'}"
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorize-real-data-capture", action="store_true")
    parser.add_argument("--data-dir")
    parser.add_argument("--synthetic-data-dir")
    parser.add_argument("--engine", default="chromium")
    parser.add_argument("--channel", default="msedge")
    parser.add_argument("--viewports", default="desktop,phone")
    parser.add_argument("--pages", default="all")
    parser.add_argument(
        "--stage", choices=("auto", "before", "candidate"), default="auto"
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        summary = run(args)
    except Exception as exc:
        print(f"settings parity capture failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"settings parity capture {summary['run_id']}: "
        f"{summary['ok']} ok, {summary['failed']} failed, no meaningful data changes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
