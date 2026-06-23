from __future__ import annotations

import pytest

from scripts import coverage_summary


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]


def test_parse_coverage_xml_sorts_lowest_modules_first(tmp_path) -> None:
    xml_path = tmp_path / "coverage.xml"
    xml_path.write_text(
        """<?xml version="1.0" ?>
<coverage>
  <packages>
    <package name="row_bot.good">
      <classes><class filename="src/row_bot/good.py"><lines>
        <line number="1" hits="1" />
        <line number="2" hits="1" />
      </lines></class></classes>
    </package>
    <package name="row_bot.low">
      <classes><class filename="src/row_bot/low.py"><lines>
        <line number="1" hits="1" />
        <line number="2" hits="0" />
        <line number="3" hits="0" />
      </lines></class></classes>
    </package>
  </packages>
</coverage>
""",
        encoding="utf-8",
    )

    modules = coverage_summary.parse_coverage_xml(xml_path)

    assert [module.name for module in modules] == ["row_bot.low", "row_bot.good"]
    assert modules[0].statements == 3
    assert modules[0].missed == 2
    assert modules[0].percent == pytest.approx(33.333, rel=0.001)


def test_format_summary_includes_totals_and_limit() -> None:
    modules = [
        coverage_summary.ModuleCoverage("row_bot.low", statements=4, missed=3),
        coverage_summary.ModuleCoverage("row_bot.high", statements=4, missed=0),
    ]

    text = coverage_summary.format_summary(modules, limit=1)

    assert "row_bot.low" in text
    assert "row_bot.high" not in text
    assert "Total: 62.50% (5/8 statements)" in text


def test_missing_xml_exits_cleanly(tmp_path, capsys) -> None:
    code = coverage_summary.main([str(tmp_path / "missing.xml")])

    output = capsys.readouterr().out
    assert code == 0
    assert "Coverage XML not found" in output
    assert "run_test_matrix.py coverage" in output


def test_stable_module_name_handles_windows_and_posix_paths(tmp_path) -> None:
    xml_path = tmp_path / "coverage.xml"
    xml_path.write_text(
        """<?xml version="1.0" ?>
<coverage>
  <packages>
    <package name="src\\row_bot\\providers\\runtime.py">
      <classes><class filename="src\\row_bot\\providers\\runtime.py"><lines>
        <line number="1" hits="1" />
      </lines></class></classes>
    </package>
    <package name="">
      <classes><class filename="src/row_bot/updater.py"><lines>
        <line number="1" hits="0" />
      </lines></class></classes>
    </package>
  </packages>
</coverage>
""",
        encoding="utf-8",
    )

    modules = coverage_summary.parse_coverage_xml(xml_path)

    assert {module.name for module in modules} == {"row_bot.providers.runtime", "row_bot.updater"}
