"""Real-world MCP end-to-end checks for maintainers.

This script intentionally uses live public MCP servers. It is not part of the
normal CI suite because network availability and third-party endpoints are not
deterministic enough for every PR. Run it before MCP-heavy releases.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(frozen=True)
class Target:
    name: str
    catalog_id: str
    server_name: str
    call_tool: str | None
    call_args: dict[str, Any] = field(default_factory=dict)
    expect: tuple[str, ...] = ()
    preflight_command: str | None = None
    default: bool = True


@dataclass
class TargetResult:
    name: str
    status: str
    details: str
    tool_count: int = 0
    enabled_tool_count: int = 0
    output_preview: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "pass"


TARGETS: dict[str, Target] = {
    "microsoft-learn": Target(
        name="microsoft-learn",
        catalog_id="microsoftdocs-mcp",
        server_name="microsoftdocs-mcp",
        call_tool="microsoft_docs_search",
        call_args={"query": "Azure Functions Python quickstart"},
        expect=("Azure", "Functions"),
    ),
    "context7": Target(
        name="context7",
        catalog_id="upstash-context7",
        server_name="upstash-context7",
        call_tool="resolve-library-id",
        call_args={"libraryName": "fastapi", "query": "FastAPI routing basics"},
        expect=("FastAPI", "Context7-compatible library ID"),
    ),
    "playwright": Target(
        name="playwright",
        catalog_id="microsoft-playwright",
        server_name="microsoft-playwright",
        call_tool=None,
        preflight_command="npx",
        default=False,
    ),
}


def _load_modules(data_dir: Path):
    os.environ["THOTH_DATA_DIR"] = str(data_dir)
    import mcp_client.config as mcp_config
    import mcp_client.marketplace as marketplace
    import mcp_client.runtime as runtime
    import ui.mcp_settings as mcp_settings
    from mcp_client.safety import prefixed_tool_name

    mcp_config = importlib.reload(mcp_config)
    marketplace = importlib.reload(marketplace)
    runtime.shutdown()
    runtime = importlib.reload(runtime)
    mcp_settings = importlib.reload(mcp_settings)
    return mcp_config, marketplace, runtime, mcp_settings, prefixed_tool_name


def _preview(text: str, max_chars: int = 500) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars] + "..."


def _wait_for_connected(runtime: Any, server_name: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    summary = runtime.get_status_summary()
    while time.monotonic() < deadline:
        summary = runtime.get_status_summary()
        server_status = summary.get("servers", {}).get(server_name, {})
        status = server_status.get("status")
        if status == "connected" and summary.get("tools", {}).get(server_name):
            return summary
        if status in {"failed", "dependency_missing"}:
            raise RuntimeError(f"{server_name} entered {status}: {server_status.get('last_error')}")
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {server_name}: {summary.get('servers', {}).get(server_name, {})}")


def _select_targets(names: list[str], include_stdio: bool) -> list[Target]:
    if names:
        missing = sorted(set(names) - set(TARGETS))
        if missing:
            raise ValueError(f"Unknown MCP E2E target(s): {', '.join(missing)}")
        return [TARGETS[name] for name in names]
    return [target for target in TARGETS.values() if target.default or include_stdio]


def _run_target(target: Target, args: argparse.Namespace) -> TargetResult:
    if target.preflight_command and shutil.which(target.preflight_command) is None:
        return TargetResult(target.name, "skip", f"Missing command on PATH: {target.preflight_command}")

    with tempfile.TemporaryDirectory(prefix="thoth_mcp_e2e_") as tmp:
        data_dir = Path(tmp)
        mcp_config, marketplace, runtime, mcp_settings, prefixed_tool_name = _load_modules(data_dir)
        try:
            if not runtime.sdk_available():
                return TargetResult(target.name, "fail", "Python package 'mcp' is not installed")

            entry = next((item for item in marketplace.CURATED_STARTER_CATALOG if item.id == target.catalog_id), None)
            if entry is None:
                return TargetResult(target.name, "fail", f"Catalog entry not found: {target.catalog_id}")

            imported_cfg = marketplace.entry_to_server_config(entry)
            if imported_cfg.get("enabled"):
                return TargetResult(target.name, "fail", "Imported catalog config was not disabled by default")
            source = imported_cfg.get("source") or {}
            if not source.get("not_verified_by_thoth"):
                return TargetResult(target.name, "fail", "Imported config did not preserve not-audited metadata")

            imported_cfg["connect_timeout"] = args.connect_timeout
            imported_cfg["tool_timeout"] = args.tool_timeout
            imported_cfg["output_limit"] = args.output_limit

            probe = runtime.probe_server(target.server_name, imported_cfg, timeout=args.connect_timeout + 10)
            if not probe.get("ok"):
                return TargetResult(target.name, "fail", f"Probe failed: {probe.get('error')}")
            if int(probe.get("tool_count") or 0) <= 0:
                return TargetResult(target.name, "fail", "Probe returned no tools")

            reviewed_cfg = mcp_settings._apply_probe_defaults(dict(imported_cfg), probe)
            enabled_defaults = dict((reviewed_cfg.get("tools") or {}).get("enabled") or {})
            if source.get("overlaps_native") and any(enabled_defaults.values()):
                return TargetResult(target.name, "fail", "Overlapping server enabled tools before manual selection")

            reviewed_cfg["enabled"] = True
            if target.call_tool:
                reviewed_cfg.setdefault("tools", {}).setdefault("enabled", {})[target.call_tool] = True

            next_config = mcp_config.load_config()
            next_config["enabled"] = True
            next_config.setdefault("servers", {})[target.server_name] = mcp_config.normalize_server_config(target.server_name, reviewed_cfg)
            mcp_config.save_config(next_config)

            runtime.discover_enabled_servers()
            summary = _wait_for_connected(runtime, target.server_name, args.connect_timeout + 15)
            tool_count = len(summary.get("tools", {}).get(target.server_name, []))
            enabled_tool_count = sum(1 for item in summary.get("tools", {}).get(target.server_name, []) if item.get("enabled"))

            if not target.call_tool:
                return TargetResult(target.name, "pass", "Connected and discovered tools", tool_count, enabled_tool_count)

            prefixed = prefixed_tool_name(target.server_name, target.call_tool)
            wrappers = {tool.name: tool for tool in runtime.get_langchain_tools()}
            if prefixed not in wrappers:
                return TargetResult(target.name, "fail", f"Enabled wrapper not found: {prefixed}", tool_count, enabled_tool_count)

            output = wrappers[prefixed].invoke(target.call_args)
            if output.startswith("MCP tool error:"):
                return TargetResult(target.name, "fail", output, tool_count, enabled_tool_count, _preview(output))
            missing = [needle for needle in target.expect if needle not in output]
            if missing:
                return TargetResult(target.name, "fail", f"Output missing expected text: {missing}", tool_count, enabled_tool_count, _preview(output))

            destructive = runtime.get_destructive_tool_names()
            if prefixed in destructive:
                return TargetResult(target.name, "fail", f"Read-only call tool unexpectedly requires approval: {prefixed}", tool_count, enabled_tool_count)
            return TargetResult(target.name, "pass", "Connected, discovered, enabled, and invoked via Thoth wrapper", tool_count, enabled_tool_count, _preview(output))
        except Exception as exc:
            return TargetResult(target.name, "fail", str(exc))
        finally:
            runtime.shutdown()


def run(args: argparse.Namespace) -> list[TargetResult]:
    targets = _select_targets(args.targets, args.include_stdio)
    return [_run_target(target, args) for target in targets]


def _print_results(results: list[TargetResult]) -> None:
    print("MCP real-world E2E results")
    print("=" * 31)
    for result in results:
        print(f"[{result.status.upper()}] {result.name}: {result.details}")
        if result.tool_count:
            print(f"  tools={result.tool_count}, enabled_tools={result.enabled_tool_count}")
        if result.output_preview:
            print(f"  preview={result.output_preview}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run real-world MCP end-to-end checks through Thoth's MCP runtime.")
    parser.add_argument("--target", dest="targets", action="append", choices=sorted(TARGETS), help="Target to run. Repeat to run several. Defaults to public no-auth HTTP targets.")
    parser.add_argument("--include-stdio", action="store_true", help="Also run stdio targets such as Playwright when their launcher is available.")
    parser.add_argument("--connect-timeout", type=float, default=30.0, help="Connection/probe timeout per server.")
    parser.add_argument("--tool-timeout", type=float, default=45.0, help="Tool invocation timeout per server.")
    parser.add_argument("--output-limit", type=int, default=50000, help="Runtime output cap used for live tool calls.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of text.")
    args = parser.parse_args(argv)

    results = run(args)
    if args.json:
        print(json.dumps([result.__dict__ for result in results], indent=2))
    else:
        _print_results(results)

    failed = [result for result in results if result.status == "fail"]
    passed = [result for result in results if result.status == "pass"]
    if failed or not passed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())