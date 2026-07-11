from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any


_MATRIX = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "e2e" / "test_live_provider_matrix.py")
)


def _failed_case(message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "answer_chars": 0,
        "errors": [message],
        "tool_calls": 0,
        "tool_results": 0,
    }


def test_only_explicit_credit_or_billing_exhaustion_is_acceptable() -> None:
    classify = _MATRIX["_classify_result"]

    assert classify(_failed_case("insufficient credits for this account")) == "acceptable_error"
    assert classify(_failed_case("billing limit reached")) == "acceptable_error"
    assert classify(_failed_case("quota exceeded")) == "unexpected_error"
    assert classify(_failed_case("rate limit exceeded")) == "unexpected_error"
    assert classify(_failed_case("invalid API key")) == "unexpected_error"


def test_live_tool_case_requires_a_complete_round_trip() -> None:
    classify = _MATRIX["_classify_result"]
    base = {
        "status": "done",
        "answer_chars": 2,
        "errors": [],
        "requires_tool_round_trip": True,
    }

    assert classify({**base, "tool_calls": 0, "tool_results": 0}) == "unexpected_error"
    assert classify({**base, "tool_calls": 1, "tool_results": 0}) == "unexpected_error"
    assert classify({**base, "tool_calls": 1, "tool_results": 1}) == "pass"
