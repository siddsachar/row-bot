from __future__ import annotations

import pytest

from row_bot.tools.calculator_tool import CalculatorTool


pytestmark = pytest.mark.subsystem


def calculate(expression: str, **kwargs: str) -> str:
    (tool,) = CalculatorTool().as_langchain_tools()
    return tool.invoke({"expression": expression, **kwargs})


@pytest.mark.parametrize(
    ("expression", "answer"),
    [
        ("weekday('2026-10-18')", "Sunday"),
        ("days_between('2026-10-07', '2026-12-25')", "79"),
        ("add_days('2026-10-07', 10)", "2026-10-17 (Saturday)"),
        ("add_months('2026-01-31', 1)", "2026-02-28 (Saturday)"),
        ("add_months('2026-11-15', 3)", "2027-02-15 (Monday)"),
        ("hours_between('2026-10-07T09:00', '2026-10-08T17:30')", "32.5"),
        ("add_hours('2026-10-07T22:00', 3)", "2026-10-08 01:00 (Thursday)"),
        ("date('2026-12-25') - date('2026-10-07')", "79 days"),
        ("date(2026, 2, 29 - 1)", "2026-02-28 (Saturday)"),
        ("2 ** 10", "1024"),
    ],
)
def test_dates_weekdays_and_durations_need_no_shell(expression: str, answer: str) -> None:
    assert calculate(expression) == f"{expression} = {answer}"


def test_text_length_counts_the_text_as_given() -> None:
    post = 'Four-day week: "it works" — 32 hours, same pay. It\'s here.'

    assert calculate("len(text)", text=post) == f"len(text) = {len(post)}"
    assert calculate("280 - len(text)", text=post) == f"280 - len(text) = {280 - len(post)}"


def test_an_unreadable_date_says_what_it_expected() -> None:
    assert "expected a date like 2026-10-18" in calculate("weekday('next Tuesday')")
