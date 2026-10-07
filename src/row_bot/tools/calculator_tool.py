"""Calculator tool — safe math, date and text-length evaluation via simpleeval."""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools.base import BaseTool
from row_bot.tools import registry

# Functions exposed to the expression evaluator
_MATH_FUNCTIONS = {
    # Basic
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "int": int,
    "float": float,
    # Powers & roots
    "pow": pow,
    "sqrt": math.sqrt,
    "cbrt": math.cbrt,
    # Logarithms
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    # Trigonometry
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "radians": math.radians,
    "degrees": math.degrees,
    # Rounding
    "ceil": math.ceil,
    "floor": math.floor,
    "trunc": math.trunc,
    # Combinatorics
    "factorial": math.factorial,
    "comb": math.comb,
    "perm": math.perm,
    # Other
    "gcd": math.gcd,
    "lcm": math.lcm,
    "hypot": math.hypot,
}



# Dates (F25): checking a weekday or a gap between dates needs no shell. Local time, as the scheduler uses.
def _as_moment(value: date | datetime | str) -> date | datetime:
    if isinstance(value, (date, datetime)):
        return value
    text = str(value).strip()
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"expected a date like 2026-10-18 or 2026-10-18T09:00, not {value!r}") from None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment.date() if len(text) <= 10 else moment


def _date(value: date | datetime | str | int, month: int | None = None, day: int | None = None) -> date | datetime:
    if month is not None and day is not None:
        return date(int(value), month, day)
    return _as_moment(value)


def _add_months(value: date | datetime | str, months: int) -> date | datetime:
    moment = _as_moment(value)
    index = moment.month - 1 + int(months)
    year, month = moment.year + index // 12, index % 12 + 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def _days_between(start: date | datetime | str, end: date | datetime | str) -> int:
    first, last = _as_moment(start), _as_moment(end)
    first = first.date() if isinstance(first, datetime) else first
    last = last.date() if isinstance(last, datetime) else last
    return (last - first).days


def _date_time(value: date | datetime | str) -> datetime:
    moment = _as_moment(value)
    return moment if isinstance(moment, datetime) else datetime.combine(moment, datetime.min.time())


def _hours_between(start: date | datetime | str, end: date | datetime | str) -> float:
    return (_date_time(end) - _date_time(start)).total_seconds() / 3600


_DATE_FUNCTIONS = {
    "today": date.today,
    "now": lambda: datetime.now().replace(second=0, microsecond=0),
    "date": _date,
    "weekday": lambda value: calendar.day_name[_as_moment(value).weekday()],
    "add_days": lambda value, days: _as_moment(value) + timedelta(days=days),
    "add_hours": lambda value, hours: _date_time(value) + timedelta(hours=hours),
    "add_months": _add_months,
    "days_between": _days_between,
    "hours_between": _hours_between,
    "len": len,
}


def _format(result: object) -> str:
    if isinstance(result, datetime):
        return f"{result:%Y-%m-%d %H:%M} ({calendar.day_name[result.weekday()]})"
    if isinstance(result, date):
        return f"{result.isoformat()} ({calendar.day_name[result.weekday()]})"
    if isinstance(result, timedelta):
        hours = result.seconds / 3600
        return f"{result.days} days" + (f" {hours:g} hours" if hours else "")
    # Avoid a trailing .0 for integers
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        return str(int(result))
    return str(result)


# Constants exposed to the expression evaluator
_MATH_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}


class _CalculateInput(BaseModel):
    expression: str = Field(
        description=(
            "A mathematical expression to evaluate. Supports standard "
            "operators (+, -, *, /, //, %, **) and functions like "
            "sqrt(), sin(), cos(), log(), round(), abs(), factorial(), etc. "
            "Constants: pi, e, tau. Examples: '2 ** 10', 'sqrt(144)', "
            "'sin(radians(45))', 'log2(1024)', 'factorial(10)'. Dates: "
            "weekday('2026-10-18'), days_between(today(), '2026-12-25'), "
            "add_days(today(), 10), add_months('2026-01-31', 1)."
        )
    )
    text: str = Field(
        default="",
        description="Optional text the expression can use as `text`, e.g. len(text) to count a post's characters.",
    )


def _calculate(expression: str, text: str = "") -> str:
    """Evaluate a math, date or text-length expression safely."""
    from simpleeval import simple_eval, InvalidExpression

    try:
        result = simple_eval(
            expression,
            functions={**_MATH_FUNCTIONS, **_DATE_FUNCTIONS},
            names={**_MATH_NAMES, "text": text},
        )
        return f"{expression} = {_format(result)}"
    except InvalidExpression as e:
        return f"Invalid expression: {e}"
    except ZeroDivisionError:
        return "Error: division by zero"
    except (ValueError, OverflowError, TypeError) as e:
        return f"Math error: {e}"
    except Exception as e:
        return f"Calculation error: {e}"


class CalculatorTool(BaseTool):

    @property
    def name(self) -> str:
        return "calculator"

    @property
    def display_name(self) -> str:
        return "🧮 Calculator"

    @property
    def description(self) -> str:
        return (
            "Evaluate mathematical expressions safely. Supports arithmetic, "
            "powers, roots, trigonometry, logarithms, factorials, dates, "
            "weekdays, durations and text length. Use this for any "
            "calculation the user asks about."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_calculate,
                name="calculate",
                description=(
                    "Evaluate a mathematical expression. Supports +, -, *, /, "
                    "//, %, ** operators and functions: sqrt, sin, cos, tan, "
                    "log, log2, log10, abs, round, ceil, floor, factorial, "
                    "comb, perm, gcd, lcm, pow, min, max, sum, radians, "
                    "degrees, hypot. Constants: pi, e, tau. Dates and "
                    "durations (local time): today(), now(), date('2026-10-18'), "
                    "weekday(d), add_days(d, n), add_hours(d, n), add_months(d, n), "
                    "days_between(a, b), hours_between(a, b). Text: len(text) "
                    "with the text in `text`. Use this instead of the shell "
                    "for dates, weekdays and lengths. Examples: 'sqrt(144)', "
                    "'weekday(\"2026-10-18\")', 'len(text)'."
                ),
                args_schema=_CalculateInput,
            )
        ]

    def execute(self, query: str) -> str:
        return _calculate(query)


registry.register(CalculatorTool())
