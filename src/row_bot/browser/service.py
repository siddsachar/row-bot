"""Managed Browser service: Playwright lifecycle, task pages, actions, recovery."""

from __future__ import annotations

import base64
import concurrent.futures
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import platform
import queue
import shutil
import threading
import time
from typing import Any
from urllib.parse import urlparse
import uuid

from row_bot.automation.contracts import (
    ActionReceipt,
    AutomationSurface,
    sanitize_automation_error,
)
from row_bot.browser.observation import (
    BrowserObservation,
    BrowserObservationRegistry,
    StaleBrowserObservation,
    target_fingerprint,
)
from row_bot.browser.local_access import (
    GRANT_SOURCES,
    GrantSource,
    LocalAddressGrant,
    address_label,
    is_row_bot_origin,
    local_development_origin,
    socket_origin,
)
from row_bot.browser.network_security import (
    MAX_LOCAL_DEVELOPMENT_GRANT_SECONDS,
    BrowserNetworkSecurity,
    LocalDevelopmentGrant,
)
from row_bot.browser.network_proxy import PinnedNetworkProxy
from row_bot.browser.policy import history_url
from row_bot.browser.recording import PROTECTED_FIELDS, PageRecorder
from row_bot.browser.runtime import (
    check_managed_browser_runtime,
    check_packaged_browser_runtime,
    ensure_profile_engine,
)
from row_bot.cancellation import current_cancellation_scope
from row_bot.data_paths import get_row_bot_data_dir


logger = logging.getLogger(__name__)
PROFILE_DIR = get_row_bot_data_dir() / "browser_profile"
VIEWPORT = {"width": 1280, "height": 900}
# How long a local address stays allowed in a conversation (the grant's maximum).
LOCAL_ADDRESS_SECONDS = MAX_LOCAL_DEVELOPMENT_GRANT_SECONDS
# Page sizes people check a design at (CSS pixels, device scale, touch screen).
VIEWPORT_PRESETS: dict[str, tuple[int, int, float, bool]] = {
    "phone": (390, 844, 3.0, True),
    "tablet": (820, 1180, 2.0, True),
    "desktop": (1280, 800, 1.0, False),
}
# A full-page screenshot stops here on very long pages (CSS pixels, and device pixels).
MAX_FULL_PAGE_CSS = 10_000
MAX_FULL_PAGE_DEVICE = 16_384
# Screenshots larger than this are saved as JPEG instead, and refused beyond it.
MAX_SCREENSHOT_BYTES = 15 * 1024 * 1024


def _cover(image: bytes, boxes: list[Any], *, jpeg: bool) -> bytes:
    """Paint over protected fields (CSS-pixel boxes with their device ratio) in a screenshot."""

    import io

    from PIL import Image, ImageDraw

    picture = Image.open(io.BytesIO(image)).convert("RGB")
    draw = ImageDraw.Draw(picture)
    for box in boxes:
        try:
            x, y, width, height, ratio = (float(value) for value in box)
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        draw.rectangle(
            [x * ratio - 2, y * ratio - 2, (x + width) * ratio + 2, (y + height) * ratio + 2],
            fill=(38, 40, 46),
        )
    output = io.BytesIO()
    picture.save(output, format="JPEG" if jpeg else "PNG", **({"quality": 80} if jpeg else {"optimize": False}))
    return output.getvalue()


def browser_runs_headless() -> bool:
    return str(os.environ.get("ROW_BOT_BROWSER_HEADLESS") or "").strip().casefold() in {"1", "true", "yes", "on"}


def _installed_channel() -> str | None:
    """Read-only discovery; never launches a probe browser."""

    requested = str(os.environ.get("ROW_BOT_BROWSER_CHANNEL") or "").strip().casefold()
    if requested in {"chrome", "msedge"}:
        return requested
    system = platform.system().casefold()
    candidates: tuple[tuple[str, str], ...]
    if system == "windows":
        # Per-machine installs in either Program Files, and Chrome's per-user install.
        program_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
        program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
        local_app_data = os.environ.get("LOCALAPPDATA") or ""
        candidates = (
            ("chrome", str(Path(program_files, "Google", "Chrome", "Application", "chrome.exe"))),
            ("chrome", str(Path(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"))),
            ("chrome", str(Path(local_app_data, "Google", "Chrome", "Application", "chrome.exe")) if local_app_data else ""),
            ("msedge", str(Path(program_files_x86, "Microsoft", "Edge", "Application", "msedge.exe"))),
            ("msedge", str(Path(program_files, "Microsoft", "Edge", "Application", "msedge.exe"))),
        )
    elif system == "darwin":
        candidates = (("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),)
    else:
        candidates = (("chrome", shutil.which("google-chrome") or ""),)
    return next((channel for channel, path in candidates if path and Path(path).is_file()), None)


def _active_generation_thread_ids() -> set[str]:
    """Return the conversations whose generation is still running."""

    from row_bot.runtime import executions

    return {str(handle.conversation_id) for handle in executions.generation_registry.active()}


@dataclass
class BrowserWorkItem:
    fn: Any
    future: concurrent.futures.Future
    scope: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _cancelled: bool = False
    _dispatched: bool = False

    def cancel(self) -> None:
        with self._lock:
            if not self._dispatched:
                self._cancelled = True
                self.future.cancel()

    def begin_dispatch(self) -> bool:
        with self._lock:
            if self._cancelled or self.future.cancelled() or (self.scope is not None and self.scope.is_cancelled()):
                self._cancelled = True
                self.future.cancel()
                return False
            self._dispatched = True
            return True


class ManagedBrowserService:
    """One persistent Row-Bot context with exact per-task page ownership."""

    _BLANK_URLS = frozenset({"", "about:blank", "chrome://newtab/", "edge://newtab/"})

    def __init__(
        self,
        *,
        network_security: BrowserNetworkSecurity | None = None,
    ) -> None:
        self._pw = None
        self._context = None
        self._launched = False
        self._closed = False
        self._launch_error: BaseException | None = None
        self._context_generation = 0
        self._page_sequence = 0
        self._page_identities: dict[Any, str] = {}
        self._navigation_generations: dict[Any, int] = {}
        self._page_owners: dict[Any, str] = {}
        self._thread_pages: dict[str, Any] = {}
        self._thread_pages_last_used: dict[str, float] = {}
        self._work_q: queue.Queue = queue.Queue()
        self._pw_thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._observations = BrowserObservationRegistry()
        self._activity_lock = threading.RLock()
        self._activity_by_thread: dict[str, dict[str, Any]] = {}
        self._activity_revision = 0
        self._activity_listeners: list[Any] = []
        self._preview_by_thread: dict[str, bytes] = {}
        self._preview_shielded: set[str] = set()
        # LangGraph resumes an interrupted tool call by re-running its function
        # from the beginning.  Preserve only the exact target proof needed to
        # re-bind that one approved action; never store typed text here.
        self._pending_approval_proofs: dict[str, dict[str, Any]] = {}
        self.browser_tool_calls = 0
        self._preview_capture_count = 0
        self._network_security = network_security or BrowserNetworkSecurity()
        self._browser_security_session_id = uuid.uuid4().hex
        self._network_proxy: PinnedNetworkProxy | None = None
        self._local_lock = threading.RLock()
        self._local_grants: dict[str, LocalAddressGrant] = {}
        # Recordings of a conversation's page (F2) and pages' chosen sizes, owned by the
        # Playwright thread.
        self._recorders: dict[str, PageRecorder] = {}
        self._viewports: dict[Any, tuple[Any, str, float]] = {}

    def grant_local_development_origin(
        self,
        origin: str,
        *,
        intended_use: str,
        ttl_seconds: float,
        scope: str | None = None,
    ) -> LocalDevelopmentGrant:
        """Grant one exact local-development origin for this browser session.

        With *scope*, only that conversation's pages may reach it.
        """

        return self._network_security.grant_local_development(
            browser_session_id=self._browser_security_session_id,
            origin=origin,
            intended_use=intended_use,
            ttl_seconds=ttl_seconds,
            scope=scope,
        )

    def revoke_local_development_grant(self, grant_id: str) -> bool:
        return self._network_security.revoke_grant(grant_id)

    # ---- Local addresses (F4) ---------------------------------------------------------

    def allow_local_address(
        self,
        conversation_id: str,
        origin: str,
        *,
        source: GrantSource,
    ) -> LocalAddressGrant:
        """Let one conversation's pages open one address on this computer, for an hour.

        It covers the server's pages and its live-reload WebSocket (same host
        and port); a new grant for the same address replaces the old one.
        Row-Bot's own app is never granted.
        """

        conversation = str(conversation_id or "").strip()
        canonical = local_development_origin(origin)
        if not conversation or canonical is None or canonical != origin:
            raise ValueError("an exact local address and a conversation are required")
        if is_row_bot_origin(canonical):
            raise ValueError("Row-Bot's own app cannot be opened in its browser")
        if source not in GRANT_SOURCES:
            raise ValueError("unknown grant source")
        label = address_label(canonical)
        with self._local_lock:
            for existing in [
                grant for grant in self._local_grants.values()
                if grant.conversation_id == conversation and grant.origin == canonical
            ]:
                self._drop_local_grant(existing)
            network_ids: list[str] = []
            try:
                for target in (canonical, socket_origin(canonical)):
                    network_ids.append(self.grant_local_development_origin(
                        target,
                        intended_use=f"Open {label} in one conversation",
                        ttl_seconds=LOCAL_ADDRESS_SECONDS,
                        scope=conversation,
                    ).id)
            except BaseException:
                for grant_id in network_ids:
                    self._network_security.revoke_grant(grant_id)
                raise
            now = time.time()
            grant = LocalAddressGrant(
                id=uuid.uuid4().hex,
                conversation_id=conversation,
                origin=canonical,
                address=label,
                source=source,
                granted_at=now,
                expires_at=now + LOCAL_ADDRESS_SECONDS,
                network_grant_ids=tuple(network_ids),
            )
            self._local_grants[grant.id] = grant
        logger.info("Local address allowed in one conversation (%s)", source)
        return grant

    def _drop_local_grant(self, grant: LocalAddressGrant) -> None:
        self._local_grants.pop(grant.id, None)
        for grant_id in grant.network_grant_ids:
            self._network_security.revoke_grant(grant_id)

    def local_address_grants(self, conversation_id: str) -> list[LocalAddressGrant]:
        """The local addresses one conversation may open now, oldest first."""

        conversation = str(conversation_id or "")
        live = {
            grant.id
            for grant in self._network_security.active_grants(
                self._browser_security_session_id, conversation
            )
        }
        with self._local_lock:
            stale = [
                item for item in self._local_grants.values()
                if item.conversation_id == conversation and not set(item.network_grant_ids) <= live
            ]
            for grant in stale:
                # Expired or revoked underneath: gone for the person too.
                self._drop_local_grant(grant)
            return sorted(
                (grant for grant in self._local_grants.values() if grant.conversation_id == conversation),
                key=lambda grant: grant.granted_at,
            )

    def local_address_grant(self, conversation_id: str, origin: str) -> LocalAddressGrant | None:
        return next(
            (grant for grant in self.local_address_grants(conversation_id) if grant.origin == origin),
            None,
        )

    def revoke_local_address(self, conversation_id: str, grant_id: str) -> bool:
        with self._local_lock:
            grant = self._local_grants.get(str(grant_id or ""))
            if grant is None or grant.conversation_id != str(conversation_id or ""):
                return False
            self._drop_local_grant(grant)
        return True

    def revoke_local_addresses(self, conversation_id: str) -> int:
        with self._local_lock:
            grants = [grant for grant in self._local_grants.values() if grant.conversation_id == str(conversation_id)]
            for grant in grants:
                self._drop_local_grant(grant)
        self._network_security.revoke_scope(self._browser_security_session_id, str(conversation_id))
        return len(grants)

    def performance_snapshot(self) -> dict[str, int]:
        return {
            "browser_tool_calls": self.browser_tool_calls,
            "semantic_observations": self._observations.observation_count,
            "preview_captures": self._preview_capture_count,
            "vision_calls": 0,
        }

    def _page_identity(self, page: Any) -> str:
        identity = self._page_identities.get(page)
        if identity is None:
            self._page_sequence += 1
            identity = f"page-{self._page_sequence}"
            self._page_identities[page] = identity
            self._navigation_generations.setdefault(page, 0)
        return identity

    def _dispose_observations_on_owner(
        self,
        observations: BrowserObservation | list[BrowserObservation] | None,
    ) -> None:
        if observations is None:
            return
        pending = observations if isinstance(observations, list) else [observations]
        if not pending:
            return

        def dispose() -> None:
            for observation in pending:
                observation.dispose()

        owner = self._pw_thread
        if owner is None or threading.current_thread() is owner:
            dispose()
            return
        if owner.is_alive() and self._launched and not self._closed:
            self._work_q.put(
                BrowserWorkItem(dispose, concurrent.futures.Future(), None)
            )

    def _invalidate(self, thread_id: str) -> None:
        previous = self._observations.invalidate(thread_id, dispose=False)
        self._dispose_observations_on_owner(previous)

    def _invalidate_all(self) -> None:
        previous = self._observations.invalidate_all(dispose=False)
        self._dispose_observations_on_owner(previous)

    def _bump_navigation(self, page: Any) -> None:
        self._navigation_generations[page] = self._navigation_generations.get(page, 0) + 1
        owner = self._page_owners.get(page)
        if owner:
            self._invalidate(owner)

    def _observe(self, page: Any, thread_id: str) -> str:
        try:
            observation = self._observations.observe(
                page,
                task_id=thread_id,
                page_identity=self._page_identity(page),
                navigation_generation=self._navigation_generations.get(page, 0),
                context_generation=self._context_generation,
            )
            return self._observations.format(observation)
        except AttributeError:
            # Compatibility for small deterministic fakes that model only tabs.
            return f"URL: {str(getattr(page, 'url', '') or '')}\nTitle: {self._safe_title(page)}\nInteractive elements (0):"

    @staticmethod
    def _safe_title(page: Any) -> str:
        try:
            return str(page.title() or "")[:160]
        except Exception:
            return ""

    @staticmethod
    def _dom_signature(page: Any) -> tuple[int, int, int]:
        """Cheap non-content change signal; never serializes values or page text."""

        try:
            value = page.evaluate(
                """() => [
                  document.querySelectorAll('a[href],button,input,textarea,select,[role]').length,
                  document.querySelectorAll('dialog[open],[role="dialog"],[role="menu"]').length,
                  document.body ? document.body.childElementCount : 0
                ]"""
            )
            if isinstance(value, list) and len(value) == 3:
                return tuple(max(0, int(item)) for item in value)
        except Exception:
            pass
        return (0, 0, 0)

    def _resolve(self, token: str, thread_id: str):
        page = self._get_page_for_thread(thread_id)
        target = self._observations.resolve(
            str(token),
            task_id=thread_id,
            page_identity=self._page_identity(page),
            navigation_generation=self._navigation_generations.get(page, 0),
            context_generation=self._context_generation,
        )
        return page, target

    def _reprove_approved_target(
        self,
        expected_metadata: dict[str, Any],
        thread_id: str,
    ):
        """Re-observe once and bind one exact target after an approval wait."""

        page = self._get_page_for_thread(thread_id)
        observation = self._observations.observe(
            page,
            task_id=thread_id,
            page_identity=self._page_identity(page),
            navigation_generation=self._navigation_generations.get(page, 0),
            context_generation=self._context_generation,
        )
        expected = target_fingerprint(dict(expected_metadata or {}))
        matches = [
            target
            for target in observation.targets.values()
            if target.fingerprint == expected
        ]
        if len(matches) != 1:
            self._invalidate(thread_id)
            return page, None
        return page, matches[0]

    @staticmethod
    def _error(action: str, exc: BaseException | str) -> str:
        error = sanitize_automation_error(AutomationSurface.BROWSER, action, exc)
        return f"ERROR [{error.code}]: {error.remediation}"

    @staticmethod
    def _receipt(action: str, revision: int, *, effect: str = "confirmed", verified: bool | None = None) -> str:
        receipt = ActionReceipt(
            surface=AutomationSurface.BROWSER,
            target_id="managed-page",
            action_family=action,
            revision=revision,
            backend_effect=effect,
            route="python-playwright",
            delivery="exact_handle",
            verified_outcome=verified,
        )
        return "Action receipt: " + json.dumps(receipt.to_dict(compatibility=False), sort_keys=True)

    def _changed_page_receipt(self, page: Any, revision: int) -> str:
        """Confirm a click-driven page change without creating target handles.

        The next semantic observation must be explicit.  Returning only
        non-interactive page facts here keeps the common navigate/search/open
        journey at two bounded observations while still making a navigation
        race a truthful success.
        """

        page_facts = {
            "title": self._safe_title(page),
            "url": history_url(str(getattr(page, "url", "") or "")),
        }
        return (
            self._receipt("click", revision, effect="confirmed", verified=True)
            + "\nPage-change receipt: "
            + json.dumps(page_facts, sort_keys=True)
        )

    @staticmethod
    def _blocked_navigation(url: str) -> str:
        origin = local_development_origin(url)
        if origin:
            return (
                f"BLOCKED: {address_label(origin)} isn't allowed in this chat (or its hour ran out). "
                "Open it again with browser_navigate to ask the person."
            )
        return (
            "BLOCKED: The page was blocked: Row-Bot's browser doesn't open addresses on your local network "
            "(other computers, routers, cloud metadata). Only addresses on this computer can be allowed."
        )

    @staticmethod
    def _approval_target_changed() -> str:
        return (
            "ERROR [approval_target_changed]: The approved page target changed or became ambiguous. "
            "Review the current page and choose the intended target again."
        )

    # ---- Local metadata/live control -------------------------------------------------

    def add_activity_listener(self, callback: Any) -> Any:
        with self._activity_lock:
            self._activity_listeners.append(callback)
        return lambda: self._remove_activity_listener(callback)

    def _remove_activity_listener(self, callback: Any) -> None:
        with self._activity_lock:
            if callback in self._activity_listeners:
                self._activity_listeners.remove(callback)

    @staticmethod
    def _site_label(url: str) -> str:
        return str(urlparse(str(url or "")).hostname or "New tab")[:120]

    def _publish_activity(self, thread_id: str, *, state: str, action: str = "", page: Any = None, active: bool = True) -> None:
        thread_id = str(thread_id or "default")
        with self._activity_lock:
            previous = dict(self._activity_by_thread.get(thread_id) or {})
            url = str(previous.get("url") or "")
            title = str(previous.get("title") or "")
            if page is not None:
                url = str(getattr(page, "url", "") or "")
                title = self._safe_title(page)
            if url != str(previous.get("url") or ""):
                self._preview_by_thread.pop(thread_id, None)
                self._preview_shielded.discard(thread_id)
            self._activity_revision += 1
            value = {
                "engine": "browser", "surface": "browser", "active": bool(active),
                "paused": state == "waiting_user", "thread_id": thread_id, "state": state,
                "target": title or self._site_label(url), "site": self._site_label(url), "url": url,
                "last_action": str(action or previous.get("last_action") or "")[:160],
                "has_thumbnail": thread_id in self._preview_by_thread,
                "preview_shielded": thread_id in self._preview_shielded,
                "revision": self._activity_revision,
            }
            if active:
                self._activity_by_thread[thread_id] = value
            else:
                self._activity_by_thread.pop(thread_id, None)
            listeners = list(self._activity_listeners)
        for callback in listeners:
            try:
                callback(dict(value))
            except Exception:
                logger.debug("Browser activity listener failed", exc_info=True)

    def status_snapshot(self, thread_id: str) -> dict[str, Any]:
        with self._activity_lock:
            value = self._activity_by_thread.get(str(thread_id or "default"))
            if value:
                return dict(value)
            return {
                "engine": "browser", "surface": "browser", "active": False, "paused": False,
                "thread_id": str(thread_id or "default"), "state": "idle", "target": "", "site": "",
                "url": "", "last_action": "", "has_thumbnail": False, "preview_shielded": False,
                "revision": self._activity_revision,
            }

    def end_activity(self, thread_id: str, *, preserve_takeover: bool = False) -> None:
        if preserve_takeover and self.status_snapshot(thread_id).get("state") == "waiting_user":
            return
        with self._activity_lock:
            self._preview_by_thread.pop(str(thread_id or "default"), None)
            self._preview_shielded.discard(str(thread_id or "default"))
        self._publish_activity(thread_id, state="idle", active=False)

    def ephemeral_screenshot(self, thread_id: str) -> bytes | None:
        with self._activity_lock:
            return self._preview_by_thread.get(str(thread_id or "default"))

    def ephemeral_preview(self, thread_id: str) -> tuple[dict[str, Any], bytes | None]:
        """Read one in-memory frame with its shield state under the same lock."""
        thread_id = str(thread_id or "default")
        with self._activity_lock:
            status = dict(self._activity_by_thread.get(thread_id) or {})
            shielded = (
                thread_id in self._preview_shielded
                or status.get("state") in {"waiting_user", "waiting_approval"}
            )
            frame = self._preview_by_thread.get(thread_id) if status.get("active") and not shielded else None
            return status, bytes(frame) if frame else None

    def _set_preview_frame(self, thread_id: str, image: bytes | None, *, shielded: bool) -> None:
        thread_id = str(thread_id or "default")
        with self._activity_lock:
            if image:
                self._preview_by_thread[thread_id] = bytes(image)
            else:
                self._preview_by_thread.pop(thread_id, None)
            if shielded:
                self._preview_shielded.add(thread_id)
            else:
                self._preview_shielded.discard(thread_id)
            snapshot = dict(self._activity_by_thread.get(thread_id) or {})
        if snapshot.get("active"):
            self._publish_activity(thread_id, state=str(snapshot.get("state") or "observing"), action=str(snapshot.get("last_action") or ""))

    def mark_waiting_approval(self, thread_id: str, action: str) -> None:
        self._invalidate(thread_id)
        self._publish_activity(thread_id, state="waiting_approval", action=action)

    def stage_approval_proof(
        self,
        thread_id: str,
        action: str,
        ref: str,
        metadata: dict[str, Any],
    ) -> None:
        """Retain one text-free exact-target proof across graph replay."""

        with self._activity_lock:
            self._pending_approval_proofs[str(thread_id)] = {
                "action": str(action),
                "ref": str(ref),
                "metadata": dict(metadata),
            }

    def pending_approval_proof(
        self,
        thread_id: str,
        action: str,
        ref: str,
    ) -> dict[str, Any] | None:
        with self._activity_lock:
            proof = dict(self._pending_approval_proofs.get(str(thread_id)) or {})
        if proof.get("action") != str(action) or proof.get("ref") != str(ref):
            return None
        metadata = proof.get("metadata")
        return dict(metadata) if isinstance(metadata, dict) else None

    def clear_approval_proof(self, thread_id: str) -> None:
        with self._activity_lock:
            self._pending_approval_proofs.pop(str(thread_id), None)

    def _run_activity(self, thread_id: str, action: str, fn: Any) -> Any:
        self.browser_tool_calls += 1
        self._publish_activity(thread_id, state="acting", action=action)
        try:
            result = self._run_on_pw_thread(fn)
            if result == "Browser action stopped by user.":
                self._invalidate(thread_id)
            return result
        except BaseException as exc:
            self._publish_activity(thread_id, state="needs_attention", action=action)
            return self._error(action, exc)

    # ---- Playwright lifecycle ---------------------------------------------------------

    def _launch_context(self) -> None:
        from playwright.sync_api import sync_playwright

        ensure_profile_engine(PROFILE_DIR)
        proxy = PinnedNetworkProxy(
            self._network_security,
            browser_session_id=self._browser_security_session_id,
        )
        proxy.start()
        self._network_proxy = proxy
        self._pw = sync_playwright().start()
        headless = browser_runs_headless()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-pings",
            # Chromium bypasses proxies for loopback by default. The managed
            # Browser must send loopback through the pinned policy boundary too.
            "--proxy-bypass-list=<-loopback>",
        ]
        if not headless:
            launch_args.extend(
                ["--start-maximized", "--window-position=0,0"]
            )
        common: dict[str, Any] = {
            "user_data_dir": str(PROFILE_DIR),
            "headless": headless,
            "args": launch_args,
            # Playwright routing cannot observe network requests already
            # intercepted by a service worker. Disable them so every actual
            # request crosses BrowserNetworkSecurity's engine hook.
            "service_workers": "block",
            "proxy": proxy.playwright_config,
        }
        if headless:
            common["viewport"] = VIEWPORT
        else:
            common["no_viewport"] = True
        channel = _installed_channel()
        attempts: list[dict[str, Any]] = []
        if channel:
            attempts.append({**common, "channel": channel})
        readiness = check_packaged_browser_runtime()
        if not readiness.ready:
            readiness = check_managed_browser_runtime()
        if readiness.ready:
            attempts.append({**common, "executable_path": readiness.executable_path})
        if not attempts:
            raise RuntimeError("Managed Chromium is not installed; use the explicit Browser Automation installer.")
        last: BaseException | None = None
        for launch_kwargs in attempts[:2]:
            try:
                self._context = self._pw.chromium.launch_persistent_context(**launch_kwargs)
                break
            except BaseException as exc:
                last = exc
        if self._context is None:
            raise RuntimeError("No reviewed browser runtime could be launched.") from last
        self._network_security.install_context(
            self._context,
            browser_session_id=self._browser_security_session_id,
            on_allowed=proxy.approve_request,
            # Each request is authorized for the conversation whose page sent it.
            page_scope=lambda page: self._page_owners.get(page),
        )
        self._context_generation += 1
        self._launched = True
        self._register_context_handlers()

    def _register_context_handlers(self) -> None:
        def on_page(page: Any) -> None:
            owner = None
            try:
                opener = page.opener()
                owner = self._page_owners.get(opener)
            except Exception:
                owner = None
            if owner:
                self._page_owners[page] = owner
                self._thread_pages[owner] = page
                self._page_identity(page)
                self._invalidate(owner)

        try:
            self._context.on("page", on_page)
        except Exception:
            pass
        try:
            self._context.browser.on("disconnected", self._on_disconnected)
        except Exception:
            pass

    def _on_disconnected(self, *_args: Any) -> None:
        self._launched = False
        # The browser is already gone. Drop exact handles logically without
        # asking a disconnected Playwright transport to dispose them.
        for recorder in list(self._recorders.values()):
            recorder.request_stop("closed")
        self._observations.invalidate_all(dispose=False)
        self._thread_pages.clear()
        self._page_owners.clear()
        self._page_identities.clear()
        self._navigation_generations.clear()
        with self._activity_lock:
            self._preview_by_thread.clear()
            self._preview_shielded.clear()
        try:
            self._work_q.put(None)
        except Exception:
            pass

    def _pw_loop(self) -> None:
        self._launch_error = None
        try:
            self._launch_context()
            self._ready.set()
        except BaseException as exc:
            self._launch_error = exc
            self._ready.set()
            try:
                if self._pw:
                    self._pw.stop()
            except Exception:
                pass
            self._pw = None
            self._context = None
            if self._network_proxy is not None:
                self._network_proxy.close()
                self._network_proxy = None
            return
        while True:
            if self._recorders:
                # A recording's frames arrive only while Playwright runs: keep it running.
                try:
                    item = self._work_q.get(timeout=0.05)
                except queue.Empty:
                    self._pump_recorders()
                    continue
            else:
                item = self._work_q.get()
            if item is None:
                break
            if not item.begin_dispatch():
                continue
            try:
                result = item.fn()
                if not item.future.cancelled():
                    item.future.set_result(result)
            except BaseException as exc:
                if not item.future.cancelled():
                    item.future.set_exception(exc)
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._context = None
        self._pw = None
        if self._network_proxy is not None:
            self._network_proxy.close()
            self._network_proxy = None
        self._launched = False
        for recorder in list(self._recorders.values()):
            recorder.request_stop("closed")
            recorder.detach()
        self._recorders.clear()
        self._viewports.clear()

    def _start(self) -> None:
        if self._pw_thread is not None and self._pw_thread.is_alive() and self._launched:
            return
        if self._pw_thread is not None and self._pw_thread.is_alive():
            self._pw_thread.join(timeout=10)
        self._invalidate_all()
        self._thread_pages.clear()
        self._page_owners.clear()
        self._ready.clear()
        self._work_q = queue.Queue()
        self._pw_thread = threading.Thread(target=self._pw_loop, daemon=True, name="row-bot-pw")
        self._pw_thread.start()
        if not self._ready.wait(timeout=60) or not self._launched:
            raise RuntimeError("Browser failed to launch.") from self._launch_error

    def _run_on_pw_thread(self, fn: Any) -> Any:
        if self._closed:
            raise RuntimeError("BrowserSession is closed")
        scope = current_cancellation_scope()
        if scope is not None and scope.is_cancelled():
            return "Browser action stopped by user."
        self._start()
        future: concurrent.futures.Future = concurrent.futures.Future()
        work = BrowserWorkItem(fn, future, scope)
        self._work_q.put(work)
        unregister = scope.register(work.cancel, "browser_work.cancel") if scope is not None else None
        try:
            deadline = time.monotonic() + 120
            while True:
                if scope is not None and scope.is_cancelled():
                    work.cancel()
                    return "Browser action stopped by user."
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("browser work timeout")
                try:
                    return future.result(timeout=min(0.1, remaining))
                except concurrent.futures.TimeoutError:
                    continue
        finally:
            if unregister:
                unregister()

    # ---- Task page ownership ----------------------------------------------------------

    def _get_page_for_thread(self, thread_id: str):
        page = self._thread_pages.get(thread_id)
        if page is not None:
            try:
                if not page.is_closed():
                    self._thread_pages_last_used[thread_id] = time.monotonic()
                    return page
            except Exception:
                pass
            self._thread_pages.pop(thread_id, None)
            self._invalidate(thread_id)
        for candidate in self._context.pages:
            try:
                if candidate not in self._page_owners and not candidate.is_closed() and candidate.url in self._BLANK_URLS:
                    self._thread_pages[thread_id] = candidate
                    self._page_owners[candidate] = thread_id
                    self._thread_pages_last_used[thread_id] = time.monotonic()
                    self._page_identity(candidate)
                    return candidate
            except Exception:
                continue
        page = self._context.new_page()
        self._thread_pages[thread_id] = page
        self._page_owners[page] = thread_id
        self._thread_pages_last_used[thread_id] = time.monotonic()
        self._page_identity(page)
        return page

    @property
    def page(self):
        return self._get_page_for_thread("default")

    def get_page_for_screenshot(self, thread_id: str | None = None):
        if thread_id:
            page = self._thread_pages.get(thread_id)
            return page if page is not None and not page.is_closed() else None
        for page in reversed(list(self._thread_pages.values())):
            try:
                if not page.is_closed():
                    return page
            except Exception:
                pass
        return None

    def release_thread(self, thread_id: str) -> None:
        self._invalidate(thread_id)
        self.clear_approval_proof(thread_id)
        recorder = self._recorders.get(thread_id)
        if recorder is not None:
            recorder.request_stop("closed")
        if not self._launched or self._closed:
            self.end_activity(thread_id)
            return
        def release() -> None:
            pages = [page for page, owner in list(self._page_owners.items()) if owner == thread_id]
            self._thread_pages.pop(thread_id, None)
            self._thread_pages_last_used.pop(thread_id, None)
            for page in pages:
                self._page_owners.pop(page, None)
                try:
                    if page.is_closed():
                        continue
                    # A persistent Chromium context may exit when its final page
                    # closes.  Keep the context alive with a fresh blank page,
                    # but always close the released task's page so its URL and
                    # document cannot be reclaimed by a later task.
                    if len([candidate for candidate in self._context.pages if not candidate.is_closed()]) <= 1:
                        replacement = self._context.new_page()
                        self._page_identity(replacement)
                    page.close()
                except Exception:
                    pass
        try:
            self._run_on_pw_thread(release)
        except Exception:
            pass
        self.end_activity(thread_id)

    def evict_idle(self, ttl_seconds: float = 600.0) -> int:
        if not self._launched or self._closed:
            return 0
        # A recording keeps its conversation's page (copied: the Playwright thread owns the map).
        active = _active_generation_thread_ids() | set(dict(self._recorders))
        cutoff = time.monotonic() - ttl_seconds
        expired = [task for task, used in self._thread_pages_last_used.items() if task not in active and used < cutoff]
        for task in expired:
            self.release_thread(task)
        return len(expired)

    def close(self) -> None:
        # Recordings finish first, so their videos are complete before the browser goes.
        recorders = list(self._recorders.values())
        for recorder in recorders:
            recorder.request_stop("quit")
        deadline = time.monotonic() + 15
        for recorder in recorders:
            recorder.join(max(0.0, deadline - time.monotonic()))
        self._invalidate_all()
        self._closed = True
        self._network_security.revoke_session(self._browser_security_session_id)
        with self._local_lock:
            self._local_grants.clear()
        with self._activity_lock:
            self._pending_approval_proofs.clear()
        for task in list(self._activity_by_thread):
            self.end_activity(task)
        try:
            self._work_q.put(None)
        except Exception:
            pass
        if self._pw_thread and self._pw_thread.is_alive():
            self._pw_thread.join(timeout=25)
            if self._pw_thread.is_alive():
                logger.warning("Managed Browser owner thread did not stop within the shutdown deadline")
        self._thread_pages.clear()
        self._page_owners.clear()
        self._page_identities.clear()
        self._navigation_generations.clear()

    # ---- Public actions ---------------------------------------------------------------

    def current_url(self, thread_id: str = "default") -> str:
        if not self._launched or self._closed:
            return ""
        return str(self._run_on_pw_thread(lambda: getattr(self._thread_pages.get(thread_id), "url", "")))

    def bring_to_front(self, thread_id: str = "default") -> bool:
        if not self._launched or self._closed:
            return False
        def front() -> bool:
            page = self._thread_pages.get(thread_id)
            if page is None or page.is_closed():
                return False
            self._invalidate(thread_id)
            page.bring_to_front()
            if not browser_runs_headless():
                try:
                    visibility = page.evaluate(
                        "() => { window.focus(); return document.visibilityState; }"
                    )
                except AttributeError:
                    visibility = "visible"  # deterministic minimal-page fake
                except Exception:
                    visibility = "hidden"
                if str(visibility or "").casefold() != "visible":
                    self._publish_activity(
                        thread_id,
                        state="needs_attention",
                        action="Could not expose the managed browser window",
                        page=page,
                    )
                    return False
            self._publish_activity(thread_id, state="waiting_user", action="You took over this tab", page=page)
            return True
        return bool(self._run_on_pw_thread(front))

    take_over = bring_to_front

    def describe_ref(self, ref: str, thread_id: str = "default") -> dict[str, Any]:
        def describe() -> dict[str, Any]:
            try:
                _page, target = self._resolve(str(ref), thread_id)
                return dict(target.metadata)
            except StaleBrowserObservation:
                return {}
        return dict(self._run_on_pw_thread(describe) or {})

    def navigate(self, url: str, thread_id: str = "default") -> str:
        def action() -> str:
            page = self._get_page_for_thread(thread_id)
            page.bring_to_front()
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                self._bump_navigation(page)
                result = self._observe(page, thread_id)
                self._publish_activity(thread_id, state="observing", action="Opened website", page=page)
                origin = local_development_origin(url)
                if origin and getattr(response, "status", 0) == 502:
                    # The pinned proxy answers 502 when nothing accepts the connection.
                    result = (
                        f"Nothing answered at {address_label(origin)}: the server may not be running yet.\n"
                        + result
                    )
                return result
            except BaseException as exc:
                if "ERR_BLOCKED_BY_CLIENT" in str(exc):
                    return self._blocked_navigation(url)
                return self._error("navigate", exc)
        return self._run_activity(thread_id, "Open website", action)

    def _click_resolved(self, page: Any, target: Any, thread_id: str) -> str:
        observation = self._observations.current(thread_id)
        revision = observation.revision if observation else 0
        old_url = str(getattr(page, "url", "") or "")
        old_pages = len(self._context.pages)
        old_dom = self._dom_signature(page)
        click_error: BaseException | None = None
        try:
            target.handle.click(timeout=5_000)
        except BaseException as exc:
            click_error = exc
        current_page = self._thread_pages.get(thread_id, page)
        changed = (
            current_page is not page
            or old_url != str(getattr(current_page, "url", "") or "")
            or len(self._context.pages) != old_pages
            or self._dom_signature(current_page) != old_dom
        )
        self._invalidate(thread_id)
        if click_error is not None and not changed:
            return self._error("click", click_error)
        if changed:
            self._bump_navigation(current_page)
            result = self._changed_page_receipt(current_page, revision)
        else:
            result = self._receipt("click", revision)
        self._publish_activity(thread_id, state="observing", action="Clicked a page control", page=current_page)
        return result

    def click(self, ref: str, thread_id: str = "default") -> str:

        def action() -> str:
            try:
                page, target = self._resolve(str(ref), thread_id)
            except BaseException as exc:
                return self._error("click", exc)
            return self._click_resolved(page, target, thread_id)
        return self._run_activity(thread_id, "Click page control", action)

    def click_after_approval(
        self,
        expected_metadata: dict[str, Any],
        thread_id: str = "default",
    ) -> str:
        def action() -> str:
            page, target = self._reprove_approved_target(expected_metadata, thread_id)
            if target is None:
                return self._approval_target_changed()
            return self._click_resolved(page, target, thread_id)

        return self._run_activity(thread_id, "Click approved page control", action)

    def _type_resolved(
        self,
        page: Any,
        target: Any,
        text: str,
        submit: bool,
        thread_id: str,
    ) -> str:
        observation = self._observations.current(thread_id)
        revision = observation.revision if observation else 0
        try:
            target.handle.fill(text, timeout=5_000)
            if submit:
                target.handle.press("Enter", timeout=5_000)
        except BaseException as exc:
            self._invalidate(thread_id)
            return self._error("type", exc)
        self._invalidate(thread_id)
        if submit:
            self._bump_navigation(page)
            result = self._observe(self._thread_pages.get(thread_id, page), thread_id)
        else:
            result = self._receipt("type", revision)
        self._publish_activity(thread_id, state="observing", action="Entered text (value hidden)", page=page)
        return result

    def type_text(self, ref: str, text: str, submit: bool = False, thread_id: str = "default") -> str:
        def action() -> str:
            try:
                page, target = self._resolve(str(ref), thread_id)
            except BaseException as exc:
                return self._error("type", exc)
            return self._type_resolved(page, target, text, submit, thread_id)
        return self._run_activity(thread_id, "Enter text (value hidden)", action)

    def type_text_after_approval(
        self,
        expected_metadata: dict[str, Any],
        text: str,
        submit: bool = False,
        thread_id: str = "default",
    ) -> str:
        def action() -> str:
            page, target = self._reprove_approved_target(expected_metadata, thread_id)
            if target is None:
                return self._approval_target_changed()
            return self._type_resolved(page, target, text, submit, thread_id)

        return self._run_activity(thread_id, "Enter approved text (value hidden)", action)

    def scroll(self, direction: str = "down", amount: int = 3, thread_id: str = "default") -> str:
        def action() -> str:
            page = self._get_page_for_thread(thread_id)
            try:
                page.mouse.wheel(0, (-1 if direction == "up" else 1) * max(1, min(int(amount), 20)) * 400)
            except BaseException as exc:
                return self._error("scroll", exc)
            self._invalidate(thread_id)
            result = self._observe(page, thread_id)
            self._publish_activity(thread_id, state="observing", action="Scrolled page", page=page)
            return result
        return self._run_activity(thread_id, "Scroll page", action)

    def snapshot(self, thread_id: str = "default") -> str:
        def action() -> str:
            page = self._get_page_for_thread(thread_id)
            result = self._observe(page, thread_id)
            self._publish_activity(thread_id, state="observing", action="Checked current page", page=page)
            return result
        return self._run_activity(thread_id, "Check current page", action)

    def go_back(self, thread_id: str = "default") -> str:
        def action() -> str:
            page = self._get_page_for_thread(thread_id)
            try:
                page.go_back(wait_until="domcontentloaded", timeout=10_000)
            except BaseException as exc:
                return self._error("back", exc)
            self._bump_navigation(page)
            result = self._observe(page, thread_id)
            self._publish_activity(thread_id, state="observing", action="Went back", page=page)
            return result
        return self._run_activity(thread_id, "Go back", action)

    def tab_action(self, action: str = "list", tab_id: int | None = None, url: str | None = None, thread_id: str = "default") -> str:
        def perform() -> str:
            current = self._get_page_for_thread(thread_id)
            pages = [page for page, owner in self._page_owners.items() if owner == thread_id and not page.is_closed()]
            if action == "list":
                lines = [f"Open tabs ({len(pages)}):"]
                for index, page in enumerate(pages):
                    marker = " <- active" if page == current else ""
                    lines.append(f"  [{index}] {history_url(str(page.url))[:2048]} - {self._safe_title(page)}{marker}")
                return "\n".join(lines)
            if action in {"switch", "close"} and (tab_id is None or tab_id < 0 or tab_id >= len(pages)):
                return f"Invalid tab_id. Use 0-{len(pages) - 1}."
            if action == "switch":
                selected = pages[int(tab_id)]
                self._thread_pages[thread_id] = selected
                self._invalidate(thread_id)
                selected.bring_to_front()
                return f"Switched to tab [{tab_id}].\n\n{self._observe(selected, thread_id)}"
            if action == "new":
                page = self._context.new_page()
                self._page_owners[page] = thread_id
                self._thread_pages[thread_id] = page
                self._page_identity(page)
                self._invalidate(thread_id)
                page.bring_to_front()
                if url:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                        self._bump_navigation(page)
                    except BaseException as exc:
                        return self._error("new_tab", exc)
                return f"Opened new tab [{len(pages)}].\n\n{self._observe(page, thread_id)}"
            if action == "close":
                if len(pages) <= 1:
                    return "Cannot close the last tab."
                selected = pages[int(tab_id)]
                self._page_owners.pop(selected, None)
                if self._thread_pages.get(thread_id) == selected:
                    self._thread_pages.pop(thread_id, None)
                self._invalidate(thread_id)
                selected.close()
                active = self._get_page_for_thread(thread_id)
                return f"Closed tab [{tab_id}].\n\n{self._observe(active, thread_id)}"
            return "Unknown tab action. Use list/switch/new/close."
        return self._run_activity(thread_id, f"Tab action: {action}", perform)

    # ---- Page size, screenshots and recordings (F4, F2) ---------------------------------

    def _owned_page(self, thread_id: str) -> Any:
        page = self._thread_pages.get(thread_id)
        if page is None or page.is_closed() or str(getattr(page, "url", "") or "") in self._BLANK_URLS:
            return None
        return page

    def set_viewport(self, preset: str, device_scale: float | None = None, thread_id: str = "default") -> str:
        """Show this conversation's page at a phone, tablet or desktop size (optionally another device scale)."""

        if preset not in VIEWPORT_PRESETS:
            return "Choose phone, tablet or desktop."
        width, height, scale, touch = VIEWPORT_PRESETS[preset]
        if device_scale is not None:
            try:
                scale = float(device_scale)
            except (TypeError, ValueError):
                return "The device scale must be a number from 1 to 3."
            if not 1.0 <= scale <= 3.0:
                return "The device scale must be a number from 1 to 3."

        def action() -> str:
            page = self._get_page_for_thread(thread_id)
            previous = self._viewports.pop(page, None)
            if previous is not None:
                try:
                    previous[0].detach()
                except Exception:
                    pass
            cdp = self._context.new_cdp_session(page)
            cdp.send("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": scale, "mobile": touch,
            })
            cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": touch, "maxTouchPoints": 5 if touch else 0})
            self._viewports[page] = (cdp, preset, scale)
            self._invalidate(thread_id)
            self._publish_activity(thread_id, state="observing", action=f"Showing the page at {preset} size", page=page)
            return (
                f"The page now shows at {preset} size: {width}×{height} at {scale:g}× scale"
                + (", as a touch screen" if touch else "")
                + ". Observe the page again before acting on it."
            )

        return self._run_activity(thread_id, f"Show the page at {preset} size", action)

    def capture_page(self, thread_id: str = "default", *, full_page: bool = False) -> tuple[bytes | None, str]:
        """A PNG (JPEG when very large) of this conversation's page, and what it shows in words.

        Captured through Chromium's own screenshot (CDP), so a phone or tablet size stays as it
        is (Playwright's screenshot would put its own size back). Password, one-time-code and
        card fields are covered. A full-page picture of a very long page stops at a bounded height.
        """

        def action() -> tuple[bytes | None, str]:
            page = self._owned_page(thread_id)
            if page is None:
                return None, "No page is open in this chat yet. Open one with browser_navigate first."
            try:
                width, height, scroll_height, ratio = page.evaluate(
                    "() => [window.innerWidth, window.innerHeight, "
                    "Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0), "
                    "window.devicePixelRatio || 1]"
                )
                width, height, scroll_height = int(width), int(height), int(scroll_height)
                ratio = float(ratio) or 1.0
            except Exception:
                width, height, scroll_height, ratio = 0, 0, 0, 1.0
            cut = 0
            clip: dict[str, Any] | None = None
            if full_page and width:
                limit = max(height, min(MAX_FULL_PAGE_CSS, int(MAX_FULL_PAGE_DEVICE / max(ratio, 1.0))))
                cut = limit if scroll_height > limit else 0
                clip = {"x": 0, "y": 0, "width": width, "height": min(scroll_height, limit) or height, "scale": 1}
            image = self._cdp_screenshot(page, clip=clip, document=full_page)
            if image is not None and len(image) > MAX_SCREENSHOT_BYTES:
                image = self._cdp_screenshot(page, clip=clip, document=full_page, jpeg=True)
            if image is None:
                return None, "The screenshot couldn't be taken. Try again in a moment."
            if len(image) > MAX_SCREENSHOT_BYTES:
                return None, "The page is too large to save as one picture; take a screenshot of what's on screen instead."
            viewport = self._viewports.get(page)
            size = f"{viewport[1]} size" if viewport else (f"{width}×{height}" if width else "its current size")
            what = "the whole page" if full_page and not cut else (
                f"the first {cut:,} pixels of a longer page" if cut else "what's on screen")
            self._publish_activity(thread_id, state="observing", action="Took a screenshot", page=page)
            return image, (
                f"Screenshot of {history_url(str(page.url))} ({what}, {size} at {ratio:g}× scale). "
                "Password and card fields are covered."
            )

        result = self._run_activity(thread_id, "Take a screenshot", action)
        # A failure or a stop comes back as words.
        return result if isinstance(result, tuple) else (None, str(result))

    def _cdp_screenshot(
        self,
        page: Any,
        *,
        clip: dict[str, Any] | None = None,
        document: bool = False,
        jpeg: bool = False,
        cover: bool = True,
    ) -> bytes | None:
        """Chromium's own screenshot of a page (device pixels), with protected fields covered."""

        held = self._viewports.get(page)
        cdp = held[0] if held else self._context.new_cdp_session(page)
        try:
            params: dict[str, Any] = {"format": "jpeg" if jpeg else "png"}
            if jpeg:
                params["quality"] = 80
            if clip is not None:
                params["clip"] = clip
                params["captureBeyondViewport"] = True
            data = base64.b64decode(str(cdp.send("Page.captureScreenshot", params).get("data") or ""))
        except Exception:
            logger.debug("A page screenshot failed", exc_info=True)
            return None
        finally:
            if not held:
                try:
                    cdp.detach()
                except Exception:
                    pass
        if not data or not cover:
            return data or None
        try:
            boxes = page.evaluate(
                "([selector, documentCoordinates]) => Array.from(document.querySelectorAll(selector)).map(element => {"
                " const r = element.getBoundingClientRect();"
                " const x = documentCoordinates ? r.left + window.scrollX : r.left;"
                " const y = documentCoordinates ? r.top + window.scrollY : r.top;"
                " return [x, y, r.width, r.height, window.devicePixelRatio || 1]; })",
                [PROTECTED_FIELDS, bool(document)],
            )
        except Exception:
            return None  # Unknown: never save a picture that might show one uncovered.
        if not isinstance(boxes, list) or len(boxes) > 200:
            return None  # Too many to be sure every one is covered: no picture.
        return _cover(data, boxes, jpeg=jpeg) if boxes else data

    def start_recording(
        self,
        path: Path,
        *,
        max_seconds: float,
        size_limit: int,
        on_end: Any,
        thread_id: str = "default",
    ) -> PageRecorder:
        """Start recording this conversation's page (it follows the conversation's tab)."""

        def action() -> PageRecorder:
            if thread_id in self._recorders:
                raise RuntimeError("already_recording")
            page = self._owned_page(thread_id)
            if page is None:
                raise LookupError("no_page")
            recorder = PageRecorder(path, max_seconds=max_seconds, size_limit=size_limit, on_end=on_end)
            recorder.attach(page, self._context.new_cdp_session(page))
            recorder.shielded = self._shielded(page, thread_id)
            recorder.checked_at = time.monotonic()
            self._recorders[thread_id] = recorder
            recorder.start()
            return recorder

        return self._run_on_pw_thread(action)

    def stop_recording(self, thread_id: str, reason: str) -> bool:
        recorder = self._recorders.get(thread_id)
        if recorder is None:
            return False
        recorder.request_stop(reason)
        return True

    def _shielded(self, page: Any, thread_id: str) -> bool:
        with self._activity_lock:
            taken_over = (self._activity_by_thread.get(thread_id) or {}).get("state") == "waiting_user"
        if taken_over:
            return True
        try:
            return page.query_selector(PROTECTED_FIELDS) is not None
        except Exception:
            return True

    def _pump_recorders(self) -> None:
        """On the Playwright thread: follow each recording's tab, hide protected pages, take frames."""

        now = time.monotonic()
        pump_page = None
        for thread_id, recorder in list(self._recorders.items()):
            if recorder.stopping:
                recorder.detach()
                self._recorders.pop(thread_id, None)
                continue
            page = self._thread_pages.get(thread_id)
            try:
                closed = page is None or page.is_closed()
            except Exception:
                closed = True
            if closed:
                recorder.detach()  # The last picture stays until a page is back.
            elif page is not recorder.page:
                recorder.detach()
                try:
                    recorder.attach(page, self._context.new_cdp_session(page))
                except Exception:
                    logger.debug("A recording could not follow its tab", exc_info=True)
            if recorder.page is not None and now - recorder.checked_at >= 1.0:
                recorder.shielded = self._shielded(recorder.page, thread_id)
                recorder.checked_at = now
            pump_page = pump_page or recorder.page
        if pump_page is not None:
            try:
                pump_page.wait_for_timeout(1)
            except Exception:
                pass

    def take_screenshot(self, thread_id: str | None = None) -> bytes | None:
        """UI preview only; actions and observations never call this method."""

        if not self._launched or self._closed:
            return None
        try:
            def capture():
                page = self.get_page_for_screenshot(thread_id)
                if page is None:
                    return None, True
                protected = page.query_selector(
                    'input[type="password"], input[autocomplete="one-time-code"], '
                    'input[autocomplete="cc-number"], input[autocomplete="cc-csc"]'
                )
                if protected is not None:
                    return None, True
                self._preview_capture_count += 1
                if page in self._viewports:
                    # Playwright's screenshot would put its own page size back.
                    return self._cdp_screenshot(page, cover=False), False
                return page.screenshot(type="png"), False
            image, shielded = self._run_on_pw_thread(capture)
            if thread_id is not None:
                self._set_preview_frame(thread_id, image, shielded=bool(shielded))
            return image
        except Exception:
            if thread_id is not None:
                self._set_preview_frame(thread_id, None, shielded=True)
            return None


BrowserSession = ManagedBrowserService


class BrowserSessionManager:
    def __init__(self) -> None:
        self._shared_session: BrowserSession | None = None
        self._lock = threading.Lock()
        self._activity_listeners: list[Any] = []

    @property
    def _sessions(self) -> dict[str, BrowserSession]:
        return {"__shared__": self._shared_session} if self._shared_session else {}

    def has_active_session(self) -> bool:
        return self._shared_session is not None

    def get_session(self, thread_id: str = "") -> BrowserSession:
        with self._lock:
            if self._shared_session is None:
                self._shared_session = BrowserSession()
                for listener in self._activity_listeners:
                    self._shared_session.add_activity_listener(listener)
            return self._shared_session

    def add_activity_listener(self, callback: Any) -> Any:
        with self._lock:
            self._activity_listeners.append(callback)
            session = self._shared_session
            if session:
                session.add_activity_listener(callback)
        def remove() -> None:
            with self._lock:
                if callback in self._activity_listeners:
                    self._activity_listeners.remove(callback)
                current = self._shared_session
            if current:
                current._remove_activity_listener(callback)
        return remove

    def status_snapshot(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._shared_session
        return session.status_snapshot(thread_id) if session else BrowserSession().status_snapshot(thread_id)

    def end_activity(self, thread_id: str, *, preserve_takeover: bool = False) -> None:
        with self._lock:
            session = self._shared_session
        if session:
            session.end_activity(thread_id, preserve_takeover=preserve_takeover)

    def take_over(self, thread_id: str) -> bool:
        with self._lock:
            session = self._shared_session
        return bool(session and session.take_over(thread_id))

    def take_screenshot(self, thread_id: str) -> bytes | None:
        with self._lock:
            session = self._shared_session
        return session.take_screenshot(thread_id) if session else None

    def ephemeral_screenshot(self, thread_id: str) -> bytes | None:
        with self._lock:
            session = self._shared_session
        return session.ephemeral_screenshot(thread_id) if session else None

    def ephemeral_preview(self, thread_id: str) -> tuple[dict[str, Any], bytes | None]:
        with self._lock:
            session = self._shared_session
        return session.ephemeral_preview(thread_id) if session else ({}, None)

    def evict_idle(self, ttl_seconds: float = 600.0) -> int:
        """Delegate scheduled cleanup to the shared managed session."""

        with self._lock:
            session = self._shared_session
        return session.evict_idle(ttl_seconds=ttl_seconds) if session else 0

    def kill_session(self, thread_id: str) -> None:
        with self._lock:
            session = self._shared_session
        if session:
            session.release_thread(thread_id)

    # Local addresses (F4): passive reads never start a browser.
    def local_address_grants(self, conversation_id: str) -> list[LocalAddressGrant]:
        with self._lock:
            session = self._shared_session
        return session.local_address_grants(conversation_id) if session else []

    def revoke_local_address(self, conversation_id: str, grant_id: str) -> bool:
        with self._lock:
            session = self._shared_session
        return bool(session and session.revoke_local_address(conversation_id, grant_id))

    def revoke_local_addresses(self, conversation_id: str) -> int:
        with self._lock:
            session = self._shared_session
        return session.revoke_local_addresses(conversation_id) if session else 0

    def kill_all(self) -> None:
        with self._lock:
            session, self._shared_session = self._shared_session, None
        if session:
            session.close()
