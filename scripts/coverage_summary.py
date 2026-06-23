from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


DEFAULT_XML_PATH = Path(".tmp/coverage/migrated-subsystems.xml")


@dataclass(frozen=True)
class ModuleCoverage:
    name: str
    statements: int
    missed: int

    @property
    def percent(self) -> float:
        if self.statements <= 0:
            return 100.0
        return ((self.statements - self.missed) / self.statements) * 100.0


def _stable_module_name(raw_name: str, filename: str) -> str:
    candidate = raw_name.strip() or filename.strip()
    candidate = candidate.replace("\\", "/")
    if candidate.endswith(".py"):
        candidate = candidate[:-3]
    if "/src/" in candidate:
        candidate = candidate.split("/src/", 1)[1]
    if candidate.startswith("src/"):
        candidate = candidate[4:]
    return candidate.replace("/", ".")


def _line_counts(package: ET.Element) -> tuple[int, int]:
    statements = 0
    missed = 0
    for line in package.findall(".//line"):
        if line.get("branch") == "true":
            continue
        statements += 1
        if int(line.get("hits", "0") or "0") == 0:
            missed += 1
    return statements, missed


def parse_coverage_xml(path: str | Path = DEFAULT_XML_PATH) -> list[ModuleCoverage]:
    root = ET.parse(path).getroot()
    modules: list[ModuleCoverage] = []

    for class_element in root.findall(".//class"):
        statements, missed = _line_counts(class_element)
        if statements == 0:
            continue
        filename = class_element.get("filename", "")
        raw_name = filename or class_element.get("name", "")
        modules.append(
            ModuleCoverage(
                name=_stable_module_name(raw_name, filename),
                statements=statements,
                missed=missed,
            )
        )

    return sorted(modules, key=lambda module: (module.percent, module.name))


def format_summary(modules: list[ModuleCoverage], *, limit: int = 10) -> str:
    if not modules:
        return "No module coverage data found."

    lines = ["Module coverage, lowest first:", "percent  statements  missed  module"]
    for module in modules[:limit]:
        lines.append(f"{module.percent:6.2f}  {module.statements:10d}  {module.missed:6d}  {module.name}")

    total_statements = sum(module.statements for module in modules)
    total_missed = sum(module.missed for module in modules)
    total_percent = 100.0 if total_statements == 0 else ((total_statements - total_missed) / total_statements) * 100.0
    lines.append(f"Total: {total_percent:.2f}% ({total_statements - total_missed}/{total_statements} statements)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize migrated subsystem coverage XML.")
    parser.add_argument("xml_path", nargs="?", default=str(DEFAULT_XML_PATH), help="Cobertura XML path to read.")
    parser.add_argument("--limit", type=int, default=10, help="Number of lowest modules to print.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    xml_path = Path(args.xml_path)
    if not xml_path.exists():
        print(f"Coverage XML not found: {xml_path}")
        print("Run: uv run python scripts/run_test_matrix.py coverage")
        return 0

    print(format_summary(parse_coverage_xml(xml_path), limit=max(args.limit, 0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
