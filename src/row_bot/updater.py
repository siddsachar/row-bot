"""Row-Bot's release check, update preferences and background update schedule.

Design principles
-----------------
- **Silent-on-failure**: any network/parse/IO error during a background check
  is logged at DEBUG and swallowed. Never blocks startup, never surfaces a
  modal error for a routine poll.
- **Preferences are honoured**: ``auto_check`` turns the background check off;
  ``auto_download`` (D2, on by default) lets a found release be downloaded and
  prepared in the background. Users pick a channel (stable/beta) and can skip
  the release on offer. Installing always needs a restart the person chooses,
  or happens when they next quit.
- **Stdlib-only networking** for the check via ``urllib.request``.
- **Thread-based scheduler** — avoids touching the server's event loop.

Preparing and applying an update is the engine's job (:mod:`row_bot.updates`):
it only runs for installs that update themselves (``install_layout``).
Installs whose host applies updates (``ROW_BOT_UPDATES=external``) run no
checks at all.

Release body manifest
~~~~~~~~~~~~~~~~~~~~~
We look for a fenced block of the form::

    <!-- row-bot-update-manifest -->
    ```manifest
    schema: 1
    files:
      Row-Bot-4.0.0-Windows-x64.exe: sha256=<hex>
      row-bot-update-4.0.0.json: sha256=<hex>
    ```

It names the SHA-256 of every release file, among them the update
description ``row-bot-update-<version>.json`` that the engine starts from.
Without it a release is still shown, but never prepared.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import pathlib
import platform
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from packaging.version import InvalidVersion, Version

from row_bot.brand import (
    APP_DISPLAY_NAME,
    APP_RELEASES_LATEST_URL,
    APP_RELEASES_URL,
    LEGACY_WINDOWS_INSTALLER_BASENAME,
    UPDATE_MANIFEST_MARKER,
    UPDATER_USER_AGENT,
    WINDOWS_INSTALLER_BASENAME,
)
from row_bot import install_layout
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.updates.external import external_mode
from row_bot.version import __version__

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════════════════════════

_DATA_DIR = get_row_bot_data_dir(create=False)
_CONFIG_PATH = _DATA_DIR / "update_config.json"

_GITHUB_API_HOST = "api.github.com"
_GITHUB_DOWNLOAD_HOST = "github.com"
_RELEASES_LATEST_URL = APP_RELEASES_LATEST_URL
_RELEASES_URL = APP_RELEASES_URL
#: A loopback release feed for rehearsing an update before it is published
#: (``http://127.0.0.1:<port>/`` serving ``releases.json`` and the files).
#: It replaces GitHub as the trust root, so it also needs the test-only
#: opt-in below; Settings and the log say when it is in use.
UPDATE_FEED_ENV = "ROW_BOT_UPDATE_FEED"
UPDATE_FEED_TESTING_ENV = "ROW_BOT_UPDATE_FEED_TESTING"

_USER_AGENT = UPDATER_USER_AGENT
_HTTP_TIMEOUT = 15

_CHECK_STARTUP_DELAY_SEC = 30
_CHECK_INTERVAL_SEC = 6 * 60 * 60      # 6 hours between scheduler ticks
_CHECK_DEBOUNCE_SEC = 24 * 60 * 60     # min 24h between actual network calls

# Platform → asset name pattern
_VERSION_ASSET_PART = r"[0-9A-Za-z][0-9A-Za-z.-]*"
_DISPLAY_ASSET_NAME = re.escape(APP_DISPLAY_NAME)
_WIN_ASSET_RE = re.compile(
    rf"^(?:"
    rf"{re.escape(WINDOWS_INSTALLER_BASENAME)}-{_VERSION_ASSET_PART}-Windows-(?:x64|x86_64|arm64|aarch64)"
    rf"|{re.escape(LEGACY_WINDOWS_INSTALLER_BASENAME)}_{_VERSION_ASSET_PART}"
    rf")\.exe$"
)
_MAC_ARM_ASSET_RE = re.compile(rf"^{_DISPLAY_ASSET_NAME}-{_VERSION_ASSET_PART}-macOS-arm64\.dmg$")
_MAC_X86_ASSET_RE = re.compile(rf"^{_DISPLAY_ASSET_NAME}-{_VERSION_ASSET_PART}-macOS-x86_64\.dmg$")
_LINUX_X64_ASSET_RE = re.compile(rf"^{_DISPLAY_ASSET_NAME}-{_VERSION_ASSET_PART}-Linux-x86_64\.tar\.gz$")
_LINUX_ARM64_ASSET_RE = re.compile(rf"^{_DISPLAY_ASSET_NAME}-{_VERSION_ASSET_PART}-Linux-aarch64\.tar\.gz$")
_RELEASE_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,159}$")

# Manifest fenced-block parser
_MANIFEST_BLOCK_RE = re.compile(
    rf"<!--\s*{re.escape(UPDATE_MANIFEST_MARKER)}\s*-->\s*```manifest\s*(.*?)\s*```",
    re.DOTALL | re.IGNORECASE,
)
_MANIFEST_FILE_LINE_RE = re.compile(
    r"^\s*([A-Za-z0-9._+-]+):\s*sha256\s*=\s*([0-9a-fA-F]{64})\s*$"
)


# ════════════════════════════════════════════════════════════════════════════
# DATA TYPES
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class UpdateInfo:
    """A parsed release that is newer than this version."""

    version: str
    channel: str  # 'stable' or 'beta'
    published_at: str  # ISO-8601
    notes_md: str
    notes_summary: str
    asset_name: str  # this platform's full installer
    asset_url: str
    asset_size: int
    sha256: str  # the installer's, may be "" if the manifest is missing
    html_url: str
    is_prerelease: bool
    # The background update description, row-bot-update-<version>.json.
    update_manifest_url: str = ""
    update_manifest_sha256: str = ""
    update_manifest_size: int = 0
    # Every release file: name -> [download URL, size].
    files: dict[str, list[Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class UpdateState:
    """Persisted updater preferences + runtime state."""

    channel: str = "stable"
    auto_check: bool = True                      # the background check (honoured)
    auto_download: bool = True                   # prepare found releases in the background (D2)
    check_interval_hours: int = 24
    last_check: Optional[str] = None             # ISO-8601
    last_success: Optional[str] = None
    skipped_versions: list[str] = field(default_factory=list)
    dismissed_banner_versions: list[str] = field(default_factory=list)
    # Runtime-only, not persisted:
    available: Optional[UpdateInfo] = None
    current_version: str = __version__
    current_channel: str = "stable"

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d.pop("available", None)
        d.pop("current_version", None)
        d.pop("current_channel", None)
        return d


# ════════════════════════════════════════════════════════════════════════════
# STATE PERSISTENCE
# ════════════════════════════════════════════════════════════════════════════

_state_lock = threading.RLock()
_state: Optional[UpdateState] = None
_listeners: list[Callable[[UpdateState], None]] = []
_checking = threading.Event()


def _load_state() -> UpdateState:
    """Load persisted state from disk; return defaults on any error."""
    state = UpdateState()
    if not _CONFIG_PATH.exists():
        return state
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key in ("channel", "last_check", "last_success"):
                if isinstance(data.get(key), str):
                    setattr(state, key, data[key])
            for key in ("auto_check", "auto_download"):
                if isinstance(data.get(key), bool):
                    setattr(state, key, data[key])
            if isinstance(data.get("check_interval_hours"), int):
                state.check_interval_hours = max(1, data["check_interval_hours"])
            for key in ("skipped_versions", "dismissed_banner_versions"):
                val = data.get(key)
                if isinstance(val, list):
                    setattr(state, key, [str(v) for v in val if isinstance(v, str)])
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to load update_config.json (using defaults): %s", exc)
    # Normalize channel
    if state.channel not in ("stable", "beta"):
        state.channel = "stable"
    state.current_channel = state.channel
    return state


def _save_state(state: UpdateState) -> None:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(
            json.dumps(state.to_dict(), indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.debug("Failed to save update_config.json: %s", exc)


def get_update_state() -> UpdateState:
    """Return the (cached) update state. Safe to call from any thread."""
    global _state
    with _state_lock:
        if _state is None:
            _state = _load_state()
        return _state


def reload_saved_update_state(config_path: pathlib.Path) -> None:
    """Refresh cached preferences after another client saves the shared file."""
    global _state
    with _state_lock:
        if _CONFIG_PATH.resolve() != config_path.resolve():
            return
        previous = _state
        current = _load_state()
        if previous is not None and current.channel == previous.channel:
            current.available = previous.available
        _state = current
    _notify()


def set_preference(name: str, enabled: bool) -> None:
    """Turn ``auto_check`` or ``auto_download`` on or off (saved at once)."""
    if name not in {"auto_check", "auto_download"} or not isinstance(enabled, bool):
        raise ValueError(f"unknown update preference {name!r}")
    with _state_lock:
        st = get_update_state()
        setattr(st, name, enabled)
        _save_state(st)
    _notify()


def skip_version(version: str) -> None:
    """Skip the offered *version* and clear any pending update for it.

    A skip only holds back the release on offer, so it replaces any earlier
    skip (B261).
    """
    with _state_lock:
        st = get_update_state()
        if version:
            st.skipped_versions = [version]
        if st.available and st.available.version == version:
            st.available = None
        _save_state(st)
    _notify()


def skipped_release() -> Optional[str]:
    """The skipped release this channel would otherwise offer, if any.

    Checks and skips keep only that one skip. More than one, or one that is
    not newer than this version, is left over from before and goes with the
    next check, so it is not reported.
    """
    skipped = get_update_state().skipped_versions
    if len(skipped) == 1 and compare_versions(__version__, skipped[0]) > 0:
        return skipped[0]
    return None


def clear_skipped_versions() -> None:
    """Make skipped releases eligible for a future explicit check."""
    with _state_lock:
        st = get_update_state()
        st.skipped_versions.clear()
        _save_state(st)
    _notify()


def dismiss_banner(version: str) -> None:
    with _state_lock:
        st = get_update_state()
        if version and version not in st.dismissed_banner_versions:
            st.dismissed_banner_versions.append(version)
            _save_state(st)
    _notify()


def subscribe(callback: Callable[[UpdateState], None]) -> Callable[[], None]:
    """Register a listener; returns an unsubscribe callable."""
    with _state_lock:
        _listeners.append(callback)

    def _unsub() -> None:
        with _state_lock:
            try:
                _listeners.remove(callback)
            except ValueError:
                pass

    return _unsub


def _notify() -> None:
    with _state_lock:
        listeners = list(_listeners)
        st = get_update_state()
    for cb in listeners:
        try:
            cb(st)
        except Exception:
            logger.exception("Updater listener raised")


def is_checking() -> bool:
    return _checking.is_set()


# ════════════════════════════════════════════════════════════════════════════
# HELPERS — WHAT KIND OF INSTALL
# ════════════════════════════════════════════════════════════════════════════

def is_dev_install() -> bool:
    """True unless this is a per-user install that manages its own versions.

    A source checkout, a container (its host applies updates) and anything
    unrecognised never update themselves (``install_layout.detect``).
    """
    try:
        return not install_layout.detect().self_updating
    except Exception:
        logger.warning("Could not read the install layout; updates stay off", exc_info=True)
        return True


def update_mode() -> str:
    """``external`` (the host updates), ``self`` (the engine) or ``unavailable``."""
    if external_mode():
        return "external"
    return "unavailable" if is_dev_install() else "self"


_engine_lock = threading.Lock()
_engine: Any = None


def get_engine() -> Any:
    """The background update engine for this install, or None if it has none."""
    global _engine
    if update_mode() != "self":
        return None
    with _engine_lock:
        if _engine is None:
            from row_bot.updates.downloader import HttpsTransport, loopback_feed_origin
            from row_bot.updates.engine import UpdateEngine

            feed = feed_url()
            origin = loopback_feed_origin(feed) if feed else None
            try:
                _engine = UpdateEngine(
                    install_layout.detect(),
                    transport=HttpsTransport(
                        extra_origins=[origin] if origin else [],
                        # A rehearsal feed is loopback: never through a proxy.
                        proxies=not origin,
                    ),
                    on_change=_notify,
                )
            except Exception:
                logger.warning("The background updater is unavailable", exc_info=True)
                return None
        return _engine


def release_files(info: UpdateInfo) -> Any:
    """What the engine needs to fetch *info*'s parts."""
    from row_bot.updates.engine import ReleaseFiles

    files = {
        name: (str(entry[0]), int(entry[1]) if len(entry) > 1 and isinstance(entry[1], int) and entry[1] > 0 else None)
        for name, entry in info.files.items()
        if isinstance(entry, (list, tuple)) and entry
    }
    return ReleaseFiles(
        version=info.version,
        manifest_url=info.update_manifest_url,
        manifest_sha256=info.update_manifest_sha256,
        manifest_size=info.update_manifest_size or None,
        files=files,
        page_url=info.html_url,
        installer_url=info.asset_url,
    )


def held_back_update() -> Optional[tuple[str, str]]:
    """The version Row-Bot went back from (rolled back or Repair), and why."""
    if update_mode() != "self":
        return None
    try:
        from row_bot.updates.switcher import held_back

        return held_back(install_layout.detect())
    except Exception:
        logger.debug("Could not read the install's rollback", exc_info=True)
        return None


def start_background_update(*, explicit: bool = False) -> bool:
    """Prepare the release on offer, if the preferences and the engine allow it.

    Never by itself for the version Row-Bot went back from: it did not start,
    or the person chose to leave it. Only an explicit Download prepares it.
    """
    engine = get_engine()
    st = get_update_state()
    info = st.available
    if engine is None or info is None:
        return False
    if not explicit and not st.auto_download:
        return False
    held = held_back_update()
    if not explicit and held is not None and held[0] == info.version:
        logger.info("Not preparing %s by itself: Row-Bot went back from it (%s)", info.version, held[1])
        return False
    status = engine.status()
    if status.phase in {"downloading", "staging", "applying"}:
        return False
    if status.phase == "ready" and status.version == info.version:
        return False
    if status.phase == "failed" and status.version == info.version and not explicit and not status.retryable:
        return False
    if not info.update_manifest_url or not info.update_manifest_sha256:
        engine.offer_full_installer(release_files(info), "the release has no background update")
        return False
    return bool(engine.start(release_files(info)))


# ════════════════════════════════════════════════════════════════════════════
# HELPERS — ASSET + MANIFEST PARSING
# ════════════════════════════════════════════════════════════════════════════

def _platform_asset_re() -> Optional[re.Pattern[str]]:
    sys_name = platform.system()
    if sys_name == "Windows":
        return _WIN_ASSET_RE
    if sys_name == "Darwin":
        mach = platform.machine().lower()
        if mach in ("arm64", "aarch64"):
            return _MAC_ARM_ASSET_RE
        return _MAC_X86_ASSET_RE
    if sys_name == "Linux":
        mach = platform.machine().lower()
        if mach in ("arm64", "aarch64"):
            return _LINUX_ARM64_ASSET_RE
        return _LINUX_X64_ASSET_RE
    return None


def parse_manifest(body: str) -> dict[str, str]:
    """Extract ``{filename: sha256-hex}`` from a release body. Returns {} if
    the fenced block is missing or malformed. Never raises.
    """
    if not body:
        return {}
    match = _MANIFEST_BLOCK_RE.search(body)
    if not match:
        return {}
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        m = _MANIFEST_FILE_LINE_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2).lower()
    return out


def _summarize_notes(markdown: str, max_chars: int = 280) -> str:
    """Return a one-paragraph summary of the release notes for status-bar
    tooltips and banners."""
    if not markdown:
        return ""
    cleaned = _MANIFEST_BLOCK_RE.sub("", markdown)
    # Strip remaining fenced code blocks (``` … ```) — they're noise in a summary.
    cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)
    # Strip HTML comments, headings, bold/italic markers.
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"^\s*[-*]\s+", "• ", cleaned, flags=re.MULTILINE)
    # Collapse blank lines.
    cleaned = re.sub(r"\n{2,}", "\n", cleaned).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "…"


def _asset_url(asset: dict[str, Any], base: str | None) -> str:
    url = str(asset.get("browser_download_url") or "")
    if base and url and "://" not in url:
        # A rehearsal feed lists its files relative to itself.
        return urllib.parse.urljoin(base, url)
    return url


def _parse_release(release: dict[str, Any], channel: str, *, base: str | None = None) -> Optional[UpdateInfo]:
    """Turn a GitHub release JSON payload into an UpdateInfo for *this*
    platform, or return None if no suitable asset exists."""
    asset_re = _platform_asset_re()
    if asset_re is None:
        return None
    assets = [asset for asset in (release.get("assets") or []) if isinstance(asset, dict)]
    chosen = None
    for asset in assets:
        name = asset.get("name") or ""
        if asset_re.match(name):
            chosen = asset
            break
    if chosen is None:
        return None

    tag = (release.get("tag_name") or "").lstrip("v")
    if not tag:
        return None

    manifest = parse_manifest(release.get("body") or "")
    sha256 = manifest.get(chosen.get("name", ""), "")
    files: dict[str, list[Any]] = {}
    for asset in assets:
        name = str(asset.get("name") or "")
        url = _asset_url(asset, base)
        if _RELEASE_FILE_NAME_RE.fullmatch(name) and url:
            size = asset.get("size")
            files[name] = [url, size if type(size) is int and size > 0 else 0]
    update_name = f"row-bot-update-{tag}.json"
    update_entry = files.get(update_name)

    notes_md = release.get("body") or ""
    return UpdateInfo(
        version=tag,
        channel=channel,
        published_at=release.get("published_at") or "",
        notes_md=notes_md,
        notes_summary=_summarize_notes(notes_md),
        asset_name=chosen.get("name") or "",
        asset_url=_asset_url(chosen, base),
        asset_size=int(chosen.get("size") or 0),
        sha256=sha256,
        html_url=release.get("html_url") or "",
        is_prerelease=bool(release.get("prerelease")),
        update_manifest_url=update_entry[0] if update_entry and manifest.get(update_name) else "",
        update_manifest_sha256=manifest.get(update_name, "") if update_entry else "",
        update_manifest_size=int(update_entry[1]) if update_entry else 0,
        files=files,
    )


def compare_versions(current: str, candidate: str) -> int:
    """Return >0 if candidate > current, <0 if less, 0 if equal. Invalid
    versions compare as 0 (treat as no update)."""
    try:
        c = Version(current)
        n = Version(candidate)
    except InvalidVersion:
        return 0
    if n > c:
        return 1
    if n < c:
        return -1
    return 0


# ════════════════════════════════════════════════════════════════════════════
# NETWORKING — GITHUB API
# ════════════════════════════════════════════════════════════════════════════

def _http_get(url: str, *, accept: str = "application/vnd.github+json") -> Optional[bytes]:
    """Minimal HTTPS GET. Returns response body bytes or None on any error."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        logger.debug("Refusing non-https URL: %s", url)
        return None
    if parsed.hostname not in (_GITHUB_API_HOST, _GITHUB_DOWNLOAD_HOST,
                               "objects.githubusercontent.com",
                               "codeload.github.com"):
        logger.debug("Refusing unknown host: %s", parsed.hostname)
        return None
    req = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": _USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("ROW_BOT_UPDATER_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # nosec
            if resp.status == 200:
                return resp.read()
            logger.debug("GitHub HTTP %s for %s", resp.status, url)
            return None
    except urllib.error.HTTPError as exc:
        logger.debug("GitHub HTTPError %s for %s: %s", exc.code, url, exc.reason)
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("GitHub request failed for %s: %s", url, exc)
        return None


_feed_warned: set[str] = set()


def feed_url() -> Optional[str]:
    """The loopback rehearsal feed in use instead of GitHub, if any (testing only)."""
    from row_bot.updates.downloader import loopback_feed_origin

    feed = os.environ.get(UPDATE_FEED_ENV, "").strip()
    if not feed:
        return None
    if os.environ.get(UPDATE_FEED_TESTING_ENV, "").strip() != "1" or loopback_feed_origin(feed) is None:
        if feed not in _feed_warned:
            _feed_warned.add(feed)
            logger.warning("Ignoring %s: it needs %s=1 and a loopback http address", UPDATE_FEED_ENV,
                           UPDATE_FEED_TESTING_ENV)
        return None
    feed = feed if feed.endswith("/") else feed + "/"
    if feed not in _feed_warned:
        _feed_warned.add(feed)
        logger.warning("Updates come from the test feed %s, not GitHub (%s)", feed, UPDATE_FEED_ENV)
    return feed


def _fetch_feed_releases(feed: str) -> Optional[list[dict[str, Any]]]:
    """Releases from a loopback rehearsal feed (no token, no proxy, checked redirects)."""
    from row_bot.updates.downloader import DownloadError, HttpsTransport, loopback_feed_origin

    origin = loopback_feed_origin(feed)
    if origin is None:
        return None
    try:
        response = HttpsTransport(extra_origins=[origin], proxies=False).open(
            feed + "releases.json", headers={"Accept": "application/json"}, timeout=_HTTP_TIMEOUT
        )
        try:
            payload = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
        finally:
            response.close()
    except (DownloadError, OSError, ValueError) as exc:
        logger.debug("Update feed unavailable: %s", exc)
        return None
    if not isinstance(payload, list):
        return None
    return [r for r in payload if isinstance(r, dict) and not r.get("draft")]


def _fetch_releases_payload(channel: str) -> Optional[list[dict[str, Any]]]:
    """Fetch the relevant release(s) for *channel*. Returns a list so the
    caller can iterate (stable → length 1, beta → up to 10)."""
    feed = feed_url()
    if feed:
        return _fetch_feed_releases(feed)
    if channel == "stable":
        data = _http_get(_RELEASES_LATEST_URL)
        if not data:
            return None
        try:
            payload = json.loads(data.decode("utf-8"))
            return [payload] if isinstance(payload, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.debug("Failed to parse stable release JSON: %s", exc)
            return None
    # beta: take top 10 releases (including pre-releases, excluding drafts)
    data = _http_get(f"{_RELEASES_URL}?per_page=10")
    if not data:
        return None
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.debug("Failed to parse releases JSON: %s", exc)
        return None
    if not isinstance(payload, list):
        return None
    return [r for r in payload if isinstance(r, dict) and not r.get("draft")]


# ════════════════════════════════════════════════════════════════════════════
# CORE CHECK
# ════════════════════════════════════════════════════════════════════════════

def check_for_updates(*, force: bool = False) -> Optional[UpdateInfo]:
    """Poll GitHub for a newer release on the user's channel.

    Returns the UpdateInfo when an applicable newer release exists, else
    None. Never raises — all failures are swallowed and logged at DEBUG.
    Results are cached on ``UpdateState.available``.
    """
    if external_mode():
        return None
    st = get_update_state()
    now = datetime.now(timezone.utc)
    if not force and st.last_check:
        try:
            last = datetime.fromisoformat(st.last_check)
            if (now - last).total_seconds() < _CHECK_DEBOUNCE_SEC:
                logger.debug("Skipping update check — debounce (%s)", st.last_check)
                return st.available
        except ValueError:
            pass

    with _state_lock:
        st.last_check = now.isoformat()
        _save_state(st)

    _checking.set()
    _notify()
    try:
        releases = _fetch_releases_payload(st.channel)
    finally:
        _checking.clear()
    if releases is None:
        logger.debug("Update check: no network / rate-limited / parse failure")
        _notify()
        return st.available

    base = feed_url()
    newest: Optional[UpdateInfo] = None
    for r in releases:
        info = _parse_release(r, st.channel, base=base)
        if info is None:
            continue
        if st.channel == "stable" and info.is_prerelease:
            continue
        if compare_versions(__version__, info.version) <= 0:
            continue
        if newest is None or compare_versions(newest.version, info.version) > 0:
            newest = info
    # A skip holds back only the release on offer; nothing older is offered
    # instead, and every other skip no longer matters (B261).
    skipped = newest is not None and newest.version in st.skipped_versions
    best = None if skipped else newest

    with _state_lock:
        st.last_success = now.isoformat()
        st.available = best
        st.skipped_versions = [newest.version] if skipped else []
        _save_state(st)
    engine = get_engine()
    if engine is not None:
        engine.acknowledge()
    _notify()
    if best:
        logger.info("Update available: v%s (%s channel)", best.version, best.channel)
    else:
        logger.debug("No update available")
    return best


# ════════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ════════════════════════════════════════════════════════════════════════════

_scheduler_thread: Optional[threading.Thread] = None
_scheduler_stop = threading.Event()


def scheduler_tick() -> None:
    """One background round: check if allowed, then prepare if allowed."""
    st = get_update_state()
    if st.auto_check:
        check_for_updates()
    start_background_update()


def start_update_scheduler() -> None:
    """Start the background poll loop. Idempotent. Silent on all failures."""
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return
    if external_mode():
        logger.info("Updater: the host applies updates — scheduler disabled")
        return
    if is_dev_install():
        logger.info("Updater: dev install detected — scheduler disabled")
        return

    _scheduler_stop.clear()

    def _loop() -> None:
        # Startup delay so we don't compete with heavy init
        if _scheduler_stop.wait(_CHECK_STARTUP_DELAY_SEC):
            return
        engine = get_engine()
        if engine is not None:
            # Old versions go once this start is verified (never on the switch path).
            threading.Thread(
                target=engine.cleanup_after_verified_start, name="row-bot-update-cleanup", daemon=True
            ).start()
        while not _scheduler_stop.is_set():
            try:
                scheduler_tick()
            except Exception:
                logger.debug("Updater scheduler tick failed", exc_info=True)
            if _scheduler_stop.wait(_CHECK_INTERVAL_SEC):
                return

    t = threading.Thread(target=_loop, name="row-bot-updater", daemon=True)
    t.start()
    _scheduler_thread = t
    logger.info("Updater scheduler started (channel=%s)",
                get_update_state().channel)


def stop_update_scheduler() -> None:
    _scheduler_stop.set()


def shutdown_background_update(timeout: float = 15.0) -> bool:
    """Row-Bot is stopping: end the scheduler and stop preparing an update."""
    _scheduler_stop.set()
    engine = _engine
    if engine is None:
        return True
    try:
        return bool(engine.shutdown(timeout))
    except Exception:
        logger.debug("Stopping the update engine failed", exc_info=True)
        return False


# ════════════════════════════════════════════════════════════════════════════
# CONVENIENCE
# ════════════════════════════════════════════════════════════════════════════

def ready_update() -> Optional[str]:
    """The version that is prepared and waits for a restart, if any."""
    if update_mode() != "self":
        return None
    try:
        from row_bot.updates.switcher import ready_version

        return ready_version(install_layout.detect())
    except Exception:
        logger.debug("Could not read the ready update", exc_info=True)
        return None


def summary_for_status() -> dict[str, Any]:
    """Return a JSON-serializable summary for the row_bot_status tool."""
    st = get_update_state()
    info = st.available
    engine = get_engine()
    status = engine.status() if engine is not None else None
    return {
        "current_version": __version__,
        "channel": st.channel,
        "auto_check": st.auto_check,
        "auto_download": st.auto_download,
        "last_check": st.last_check,
        "last_success": st.last_success,
        "update_available": info is not None,
        "available_version": info.version if info else None,
        "available_notes_summary": info.notes_summary if info else None,
        "skipped_versions": list(st.skipped_versions),
        "dev_install": is_dev_install(),
        "mode": update_mode(),
        "phase": status.phase if status else None,
        "ready_version": ready_update(),
    }
