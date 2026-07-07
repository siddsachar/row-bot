"""Optional Tailscale detection for Row-Bot mobile access."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import shutil
import subprocess
from typing import Any, Callable


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class TailscaleState:
    installed: bool = False
    logged_in: bool = False
    tailnet_name: str = ""
    device_name: str = ""
    tailscale_ips: tuple[str, ...] = ()
    magicdns_url_candidates: tuple[str, ...] = ()
    serve_enabled: bool = False
    serve_url: str = ""
    funnel_enabled: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "logged_in": self.logged_in,
            "tailnet_name": self.tailnet_name,
            "device_name": self.device_name,
            "tailscale_ips": list(self.tailscale_ips),
            "magicdns_url_candidates": list(self.magicdns_url_candidates),
            "serve_enabled": self.serve_enabled,
            "serve_url": self.serve_url,
            "funnel_enabled": self.funnel_enabled,
            "error": self.error,
        }


Runner = Callable[[list[str]], CommandResult]


def _run(argv: list[str], *, timeout: float) -> CommandResult:
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def parse_tailscale_ips(output: str) -> tuple[str, ...]:
    ips: list[str] = []
    for line in str(output or "").splitlines():
        text = line.strip()
        if text and text not in ips:
            ips.append(text)
    return tuple(ips)


def parse_tailscale_status_json(payload: str | dict[str, Any], *, port: int | None = None) -> TailscaleState:
    try:
        data = json.loads(payload) if isinstance(payload, str) else dict(payload)
    except Exception as exc:
        return TailscaleState(installed=True, error=f"Could not parse tailscale status: {exc}")

    self_info = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    backend_state = str(data.get("BackendState") or "")
    logged_in = bool(self_info) and backend_state.lower() not in {"stopped", "needslogin", "no state"}
    tailnet = data.get("CurrentTailnet") if isinstance(data.get("CurrentTailnet"), dict) else {}
    tailnet_name = str(tailnet.get("Name") or tailnet.get("MagicDNSSuffix") or "")
    device_name = str(self_info.get("HostName") or self_info.get("DNSName") or "").strip(".")
    ips = tuple(str(ip) for ip in self_info.get("TailscaleIPs") or [] if str(ip).strip())
    dns_name = str(self_info.get("DNSName") or "").strip(".")
    magicdns: list[str] = []
    if dns_name:
        if port:
            magicdns.append(f"http://{dns_name}:{port}")
        else:
            magicdns.append(f"http://{dns_name}")
    return TailscaleState(
        installed=True,
        logged_in=logged_in,
        tailnet_name=tailnet_name,
        device_name=device_name,
        tailscale_ips=ips,
        magicdns_url_candidates=tuple(magicdns),
    )


def parse_tailscale_serve_status(output: str | dict[str, Any]) -> tuple[bool, str, bool]:
    text = json.dumps(output) if isinstance(output, dict) else str(output or "")
    urls = re.findall(r"https://[A-Za-z0-9_.-]+(?:/[^\s\"']*)?", text)
    funnel_enabled = "funnel" in text.lower()
    return (bool(urls), urls[0] if urls else "", funnel_enabled)


def detect_tailscale(
    *,
    port: int | None = None,
    timeout: float = 2.0,
    runner: Runner | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> TailscaleState:
    """Detect Tailscale without making it a hard dependency."""
    binary = which("tailscale")
    if not binary:
        return TailscaleState(installed=False, error="tailscale command not found")
    run = runner or (lambda argv: _run(argv, timeout=timeout))
    try:
        status_result = run([binary, "status", "--json"])
    except Exception as exc:
        return TailscaleState(installed=True, error=str(exc))
    if status_result.returncode != 0:
        detail = (status_result.stderr or status_result.stdout or "tailscale status failed").strip()
        return TailscaleState(installed=True, error=detail)
    state = parse_tailscale_status_json(status_result.stdout, port=port)

    ips = state.tailscale_ips
    try:
        ip_result = run([binary, "ip", "-4"])
        if ip_result.returncode == 0:
            ips = parse_tailscale_ips(ip_result.stdout) or ips
    except Exception:
        pass

    serve_enabled = False
    serve_url = ""
    funnel_enabled = False
    try:
        serve_result = run([binary, "serve", "status", "--json"])
        if serve_result.returncode == 0:
            serve_enabled, serve_url, funnel_enabled = parse_tailscale_serve_status(serve_result.stdout)
    except Exception:
        pass

    return TailscaleState(
        installed=True,
        logged_in=state.logged_in,
        tailnet_name=state.tailnet_name,
        device_name=state.device_name,
        tailscale_ips=ips,
        magicdns_url_candidates=state.magicdns_url_candidates,
        serve_enabled=serve_enabled,
        serve_url=serve_url,
        funnel_enabled=funnel_enabled,
        error=state.error,
    )
