"""
Row-Bot – Tunnel Manager
=======================
Provider-agnostic tunnel infrastructure for exposing local webhook
ports to the internet.  Channels that need inbound webhooks (e.g. SMS /
Twilio) call ``tunnel_manager.start_tunnel(port)`` to obtain a public
HTTPS URL, and ``tunnel_manager.stop_tunnel(port)`` on shutdown.

Architecture
------------
- **TunnelProvider** – ABC that each backend (ngrok, cloudflare, …)
  implements.
- **NgrokProvider** – Concrete provider using *pyngrok*.
- **TunnelManager** – Thread-safe singleton that delegates to the
  active provider.

The module-level ``tunnel_manager`` instance is created at import time
but stays dormant (no process spawned) until ``start_tunnel()`` is
called.

Every exit closes what Row-Bot opened (decision 15): a normal quit and an
unquiesced shutdown stop the tunnels first, the ngrok agent is recorded as
Row-Bot's own in the data folder (and, on Windows, dies with the server
process), the launcher's forced stop cleans up the agent of the server it
killed, and the next start stops agents left by a crash. Only recorded
agents are touched; an ngrok agent Row-Bot did not start is never stopped.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from row_bot import owned_processes

log = logging.getLogger(__name__)


# ── Owned ngrok agents ───────────────────────────────────────────────

_OWNED_AGENTS_FILE = "ngrok-agents.json"
_job_handle = None  # Windows job object that ends agents with this process


def owned_agents_path() -> Path:
    return owned_processes.ledger_path(_OWNED_AGENTS_FILE)


def record_owned_agent(pid: int, *, path: Path | None = None) -> None:
    """Remember an ngrok agent this process started, so any exit can stop it."""
    if owned_processes.record(path or owned_agents_path(), pid):
        _contain_agent(pid)


def forget_owned_agents(*, path: Path | None = None) -> None:
    """Drop this process's records once its agents are stopped."""
    owned_processes.forget(path or owned_agents_path())


def cleanup_owned_agents(
    *, dead_owner: int | Iterable[int] | None = None, path: Path | None = None
) -> int:
    """Stop recorded ngrok agents whose Row-Bot process is gone.

    ``dead_owner`` is the server process (or process tree: on Windows a venv
    ``python.exe`` starts the real interpreter as its child) the launcher
    has just stopped by force. Agents of a Row-Bot process that is still
    running are kept, and a process that is not the recorded agent (a reused
    pid, anything that is not ngrok) is never touched. Returns how many
    agents were stopped.
    """
    stopped = owned_processes.cleanup(path or owned_agents_path(), dead_owner=dead_owner, name="ngrok")
    if stopped:
        log.info("Stopped %d ngrok agent(s) left by an earlier Row-Bot run", stopped)
    return stopped


def _contain_agent(pid: int) -> bool:
    """On Windows, tie the agent's life to this process (a crash included)."""
    global _job_handle
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if _job_handle is None:
            class _BasicLimits(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class _IoCounters(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class _ExtendedLimits(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return False
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                kernel32.CloseHandle(handle)
                return False
            _job_handle = handle
        kernel32.OpenProcess.restype = wintypes.HANDLE
        process = kernel32.OpenProcess(0x0100 | 0x0001, False, int(pid))  # SET_QUOTA | TERMINATE
        if not process:
            return False
        try:
            return bool(kernel32.AssignProcessToJobObject(_job_handle, process))
        finally:
            kernel32.CloseHandle(process)
    except Exception:
        log.debug("Could not tie the ngrok agent to this process", exc_info=True)
        return False


# ── Errors in words ──────────────────────────────────────────────────

def describe_tunnel_error(error: object) -> str:
    """What went wrong with a tunnel, for people; details stay in the log."""
    text = str(error or "")
    lowered = text.lower()
    if "err_ngrok_108" in lowered or "simultaneous ngrok agent sessions" in lowered:
        return ("ngrok refused a new tunnel: your ngrok account already has as many agents running as it "
                "allows (Row-Bot on another computer, or another app). Stop one in the ngrok dashboard, "
                "then try again.")
    if any(code in lowered for code in ("err_ngrok_105", "err_ngrok_107", "err_ngrok_4018")) \
            or "authtoken" in lowered and ("invalid" in lowered or "not valid" in lowered):
        return "ngrok didn't accept the saved authtoken. Replace it in Settings › Access › Tunnel."
    if "not_set" in lowered or "ngrok_authtoken not set" in lowered:
        return "No ngrok authtoken is saved. Add one in Settings › Access › Tunnel."
    if "pyngrok is not installed" in lowered:
        return "The tunnel support isn't installed in this copy of Row-Bot."
    if "err_ngrok_334" in lowered or "already online" in lowered:
        return "Another ngrok agent is already serving this address. Stop it, then try again."
    if "access policy" in lowered:
        return "The tunnel address was refused by Row-Bot's access rules, so it was closed."
    return "ngrok couldn't open the tunnel. Check the internet connection and the tunnel settings, then try again."


def _ngrok_authtoken() -> str:
    """Return the ngrok token from Row-Bot's normal secret sources."""
    try:
        from row_bot.api_keys import get_key

        return get_key("NGROK_AUTHTOKEN").strip()
    except Exception as exc:
        log.debug("Unable to read NGROK_AUTHTOKEN from key store: %s", exc)
        return os.environ.get("NGROK_AUTHTOKEN", "").strip()


def ngrok_configuration_status() -> tuple[str, str]:
    """Return ``(status_code, detail)`` for ngrok readiness diagnostics."""
    try:
        import pyngrok  # noqa: F401
    except ImportError:
        return ("error", "pyngrok is not installed. Run: pip install pyngrok")

    try:
        from row_bot.api_keys import get_key, key_status

        if get_key("NGROK_AUTHTOKEN").strip():
            return ("ok", "ngrok available")
        status = key_status("NGROK_AUTHTOKEN")
        if status.get("configured"):
            return (
                "error",
                "ngrok authtoken metadata exists, but the keyring secret is unreadable. "
                "Clear and re-save the authtoken in Settings > System > Tunnel Settings.",
            )
    except Exception as exc:
        log.debug("Unable to inspect NGROK_AUTHTOKEN status: %s", exc)

    if os.environ.get("NGROK_AUTHTOKEN"):
        return ("ok", "ngrok available")
    return ("inactive", "NGROK_AUTHTOKEN is not set")


# ── Exceptions ───────────────────────────────────────────────────────

class TunnelError(Exception):
    """Raised when the tunnel provider cannot start or encounters a
    fatal configuration / connectivity problem."""


class ManagedOriginRegistrar(Protocol):
    """Runtime access-policy boundary used by managed tunnel lifecycles."""

    def register_managed_origin(self, url: object) -> str: ...

    def unregister_managed_origin(self, url: object) -> bool: ...


# ── Provider ABC ─────────────────────────────────────────────────────

class TunnelProvider(ABC):
    """Abstract base for tunnel providers (ngrok, cloudflare, tailscale, …)."""

    @abstractmethod
    def start(self, port: int, label: str = "") -> str:
        """Open a tunnel to *port*.  Return the public HTTPS URL."""

    @abstractmethod
    def stop(self, port: int) -> None:
        """Close the tunnel for *port*."""

    @abstractmethod
    def stop_all(self) -> None:
        """Close every tunnel managed by this provider."""

    @abstractmethod
    def get_url(self, port: int) -> str | None:
        """Current public URL for *port*, or ``None``."""

    @abstractmethod
    def is_available(self) -> bool:
        """``True`` when this provider is configured and ready."""

    @abstractmethod
    def active_tunnels(self) -> dict[int, str]:
        """Map of port → public URL for all active tunnels."""


# ── Ngrok provider ───────────────────────────────────────────────────

class NgrokProvider(TunnelProvider):
    """Uses *pyngrok* to auto-manage ngrok tunnels.

    ``pyngrok`` auto-downloads the ngrok binary on first use, so no
    manual install is required.
    """

    def __init__(self) -> None:
        self._tunnels: dict = {}  # port → pyngrok NgrokTunnel object
        self._agent_started = False  # an agent may be running for this process

    # ── Provider interface ───────────────────────────────────────────

    def is_available(self) -> bool:
        try:
            import pyngrok  # noqa: F401
            return bool(_ngrok_authtoken())
        except ImportError:
            return False

    def _track_agent(self) -> None:
        try:
            from pyngrok import ngrok

            process = ngrok.get_ngrok_process()
            pid = int(getattr(getattr(process, "proc", None), "pid", 0) or 0)
            if pid:
                record_owned_agent(pid)
        except Exception:
            log.debug("Could not record the ngrok agent as Row-Bot's own", exc_info=True)

    def _end_agent(self) -> None:
        if not self._agent_started:
            return
        try:
            from pyngrok import ngrok

            ngrok.kill()
        except Exception as exc:
            log.warning("Error stopping the ngrok agent: %s", exc)
        self._agent_started = False
        forget_owned_agents()

    def start(self, port: int, label: str = "") -> str:
        if port in self._tunnels:
            return self._tunnels[port].public_url

        try:
            from pyngrok import ngrok, conf
        except ImportError:
            raise TunnelError(
                "pyngrok is not installed. Run: pip install pyngrok"
            )

        token = _ngrok_authtoken()
        if not token:
            raise TunnelError(
                "NGROK_AUTHTOKEN not set — configure it in "
                "Settings → System → Tunnel Settings"
            )

        try:
            pyngrok_config = conf.get_default()
            pyngrok_config.auth_token = token
            log.info("Starting ngrok tunnel: port %d%s", port, f" [{label}]" if label else "")
            self._agent_started = True
            tunnel = ngrok.connect(port, bind_tls=True)
            self._tunnels[port] = tunnel
            self._track_agent()
            log.info("ngrok tunnel opened: port %d → %s%s",
                     port, tunnel.public_url,
                     f" [{label}]" if label else "")
            return tunnel.public_url
        except Exception as exc:
            log.warning("ngrok failed to open a tunnel on port %d: %s", port, exc)
            if not self._tunnels:
                # A refused start can leave an idle agent holding a session.
                self._end_agent()
            raise TunnelError(describe_tunnel_error(exc)) from exc

    def stop(self, port: int) -> None:
        tunnel = self._tunnels.pop(port, None)
        if tunnel:
            try:
                from pyngrok import ngrok
                ngrok.disconnect(tunnel.public_url)
                log.info("ngrok tunnel closed: port %d", port)
            except Exception as exc:
                log.warning("Error closing ngrok tunnel on port %d: %s", port, exc)
            if not self._tunnels:
                # An idle agent still counts against the account's sessions.
                self._end_agent()

    def stop_all(self) -> None:
        self._end_agent()
        self._tunnels.clear()
        log.info("All ngrok tunnels closed")

    def get_url(self, port: int) -> str | None:
        t = self._tunnels.get(port)
        return t.public_url if t else None

    def active_tunnels(self) -> dict[int, str]:
        return {p: t.public_url for p, t in self._tunnels.items()}


# ── Tunnel Manager (singleton wrapper) ───────────────────────────────

class TunnelManager:
    """Thread-safe facade wrapping the active :class:`TunnelProvider`.

    Channels interact with this class — never with providers directly.
    """

    def __init__(
        self,
        *,
        managed_origin_registrar: ManagedOriginRegistrar | None = None,
    ) -> None:
        self._provider: TunnelProvider | None = None
        self._managed_origin_registrar = managed_origin_registrar
        self._registered_origins: dict[int, str] = {}
        self._lock = threading.Lock()
        # The last start failure, in words, until a start succeeds (B106).
        self._last_error: str | None = None

    # ── Provider management ──────────────────────────────────────────

    def set_provider(self, provider: TunnelProvider) -> None:
        with self._lock:
            if self._registered_origins:
                raise TunnelError(
                    "cannot replace the tunnel provider while managed tunnels are active"
                )
            self._provider = provider

    def set_managed_origin_registrar(
        self,
        registrar: ManagedOriginRegistrar,
    ) -> None:
        """Inject the process-local access policy before any tunnel can start."""

        with self._lock:
            if self._registered_origins:
                raise TunnelError(
                    "cannot replace the access policy while managed tunnels are active"
                )
            self._managed_origin_registrar = registrar

    def _ensure_provider(self) -> None:
        """Lazy-init: create the default provider if none set yet."""
        if self._provider is not None:
            return
        try:
            from row_bot.channels import config as ch_config
            name = ch_config.get("tunnel", "provider", "ngrok")
        except Exception:
            name = "ngrok"
        if name == "ngrok":
            self._provider = NgrokProvider()
        else:
            log.warning("Unknown tunnel provider '%s', falling back to ngrok", name)
            self._provider = NgrokProvider()

    # ── Public API ───────────────────────────────────────────────────

    def start_tunnel(self, port: int, label: str = "") -> str:
        """Open a tunnel for *port*.  Returns the public HTTPS URL.

        Raises :class:`TunnelError` on failure.
        """
        with self._lock:
            self._ensure_provider()
            registrar = self._managed_origin_registrar
            if registrar is None:
                raise TunnelError(
                    "managed tunnel access policy is unavailable; restart Row-Bot"
                )

            existing_origin = self._registered_origins.get(port)
            existing_url = self._provider.get_url(port)
            if existing_origin is not None and existing_url is not None:
                return existing_origin

            started_new = existing_url is None
            try:
                public_url = self._provider.start(port, label)
            except TunnelError as exc:
                self._last_error = str(exc)
                raise
            except Exception as exc:
                self._last_error = describe_tunnel_error(exc)
                raise TunnelError(self._last_error) from exc
            try:
                origin = registrar.register_managed_origin(public_url)
            except Exception as exc:
                if started_new:
                    try:
                        self._provider.stop(port)
                    except Exception:
                        log.warning(
                            "Unable to close tunnel after access-policy rejection "
                            "on port %d",
                            port,
                            exc_info=True,
                        )
                self._last_error = describe_tunnel_error("access policy")
                raise TunnelError(
                    "managed tunnel URL was rejected by the access policy"
                ) from exc

            previous = self._registered_origins.get(port)
            self._registered_origins[port] = origin
            self._last_error = None
            if previous is not None and previous != origin:
                registrar.unregister_managed_origin(previous)
            return origin

    def stop_tunnel(self, port: int) -> None:
        """Close the tunnel for *port* (no-op if not open)."""
        with self._lock:
            origin = self._registered_origins.pop(port, None)
            try:
                if self._provider:
                    self._provider.stop(port)
            finally:
                if origin is not None and self._managed_origin_registrar is not None:
                    self._managed_origin_registrar.unregister_managed_origin(origin)

    def stop_all(self) -> None:
        """Close **all** active tunnels and kill the provider process."""
        with self._lock:
            origins = tuple(self._registered_origins.values())
            self._registered_origins.clear()
            try:
                if self._provider:
                    self._provider.stop_all()
            finally:
                registrar = self._managed_origin_registrar
                if registrar is not None:
                    for origin in origins:
                        registrar.unregister_managed_origin(origin)

    def get_url(self, port: int) -> str | None:
        """Return the current public URL for *port*, or ``None``."""
        with self._lock:
            return self._registered_origins.get(port)

    def is_available(self) -> bool:
        """``True`` if the provider is configured and ready to create
        tunnels (e.g. authtoken is set and library is installed)."""
        with self._lock:
            self._ensure_provider()
            return self._provider.is_available()

    def active_tunnels(self) -> dict[int, str]:
        """Map of port → public URL for every open tunnel."""
        with self._lock:
            return dict(self._registered_origins)

    @property
    def last_error(self) -> str | None:
        """The last start failure in words, cleared by the next success."""
        with self._lock:
            return self._last_error

    def status(self) -> tuple[str, str]:
        """Return ``(status_code, detail)`` for health-check display.

        *status_code* is one of ``"ok"``, ``"inactive"``, ``"error"``. A
        start that failed is an error until a start succeeds, never "Ready".
        """
        try:
            with self._lock:
                self._ensure_provider()
                active = dict(self._registered_origins)
                if active:
                    urls = ", ".join(f"{p}→{u}" for p, u in active.items())
                    return ("ok", f"{len(active)} active: {urls}")
                if self._last_error:
                    return ("error", f"Not running: {self._last_error}")
                config_status, config_detail = ngrok_configuration_status()
                if config_status != "ok":
                    return (config_status, config_detail)
                return ("inactive", "Ready (no active tunnels)")
        except Exception as exc:
            return ("error", describe_tunnel_error(exc))

    def holds_tunnels(self) -> bool:
        """``True`` while a tunnel or an agent of this process may be open."""
        with self._lock:
            return bool(self._registered_origins) or bool(
                getattr(self._provider, "_agent_started", False)
                or (self._provider is not None and self._provider.active_tunnels())
            )

    def runtime_state(self) -> dict:
        """What the tunnel is doing now, read from this process (no network)."""
        with self._lock:
            active = len(self._registered_origins)
            error = self._last_error
        if active:
            state = "active"
        elif error:
            state = "failed"
        elif ngrok_configuration_status()[0] != "ok":
            state = "not_configured"
        else:
            state = "idle"
        return {"runtime_state": state, "active_count": active, "last_error": error}


# ── Module-level singleton ───────────────────────────────────────────

tunnel_manager = TunnelManager()


def close_tunnels_on_exit(reason: str = "exit") -> None:
    """Close every tunnel this process opened; safe to call more than once."""
    try:
        if not tunnel_manager.holds_tunnels():
            return
        tunnel_manager.stop_all()
        log.info("Tunnels closed (%s)", reason)
    except Exception:
        log.warning("Closing tunnels failed (%s)", reason, exc_info=True)
