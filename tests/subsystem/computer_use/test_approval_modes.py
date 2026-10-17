from __future__ import annotations

import pytest

from row_bot.approval_review import ReviewVerdict
from row_bot.computer_use.service import ComputerUseError, ComputerUseService, LeaseOwner


OWNER = LeaseOwner("policy-thread", "policy-generation", "policy-task")


@pytest.fixture
def reviewer(monkeypatch):
    """The Auto reviewer, standing in: it approves unless told otherwise, and counts what it saw."""
    state = {"decision": "approve", "seen": 0}

    def review(_payload, **_kwargs):
        state["seen"] += 1
        return ReviewVerdict(state["decision"], "You asked to use the calculator.")

    monkeypatch.setattr("row_bot.approval_review.review", review)
    return state


@pytest.mark.parametrize(
    ("approval_mode", "reviewer_decision", "expected_prompts", "allowed"),
    [
        ("block", "approve", 0, False),
        ("approve", "approve", 1, True),
        ("auto", "approve", 0, True),
        ("auto", "ask", 1, True),
        ("full", "ask", 0, True),
    ],
)
def test_launch_uses_the_shared_approval_levels(
    fake_client,
    fake_transport,
    reviewer,
    approval_mode: str,
    reviewer_decision: str,
    expected_prompts: int,
    allowed: bool,
) -> None:
    reviewer["decision"] = reviewer_decision
    prompts: list[dict] = []
    service = ComputerUseService(
        client_factory=lambda: fake_client,
        approval_callback=lambda payload: prompts.append(payload) or True,
    )
    service.acquire(OWNER, validate_context=False)

    if allowed:
        assert service.launch_app(
            "Calculator",
            OWNER,
            approval_mode=approval_mode,
        )
    else:
        with pytest.raises(ComputerUseError, match="Block approval mode"):
            service.launch_app(
                "Calculator",
                OWNER,
                approval_mode=approval_mode,
            )

    assert len(prompts) == expected_prompts
    assert [name for name, _args in fake_transport.calls].count("launch_app") == int(allowed)


@pytest.mark.parametrize("approval_mode", ["auto", "full"])
def test_auto_and_full_autonomy_are_not_cached_as_ask_app_consent(fake_client, reviewer, approval_mode) -> None:
    prompts: list[dict] = []
    service = ComputerUseService(
        client_factory=lambda: fake_client,
        approval_callback=lambda payload: prompts.append(payload) or True,
    )
    service.acquire(OWNER, validate_context=False)
    target_id = service.list_windows(OWNER, app="Calculator")[0]["target_id"]

    service.capture(target_id, OWNER, approval_mode=approval_mode)
    service.capture(target_id, OWNER, approval_mode=approval_mode)
    assert prompts == []
    # The reviewer's approval of the app holds for the session under Auto: it is asked once.
    assert reviewer["seen"] == (1 if approval_mode == "auto" else 0)

    service.capture(target_id, OWNER, approval_mode="approve")
    assert len(prompts) == 1


@pytest.mark.parametrize(("approval_mode", "reviewed"), [("full", 0), ("auto", 2)])
def test_full_autonomy_and_an_approving_reviewer_skip_the_consequential_prompt_but_not_hard_surface_rules(
    fake_client,
    fake_transport,
    reviewer,
    approval_mode,
    reviewed,
) -> None:
    prompts: list[dict] = []
    service = ComputerUseService(
        client_factory=lambda: fake_client,
        approval_callback=lambda payload: prompts.append(payload) or True,
    )
    service.acquire(OWNER, validate_context=False)
    target_id = service.list_windows(OWNER, app="Calculator")[0]["target_id"]
    observation = service.capture(target_id, OWNER, approval_mode=approval_mode)

    service.act(
        "click",
        target_id,
        OWNER,
        element_token=observation.elements[1].token,
        expected_effect="Submit result",
        approval_mode=approval_mode,
    )

    assert prompts == []
    # Under Auto the reviewer saw the app's access and the consequential click; Hands-off asks no one.
    assert reviewer["seen"] == reviewed
    assert [name for name, _args in fake_transport.calls].count("click") == 1
    with pytest.raises(ComputerUseError, match="cannot be targeted"):
        service.launch_app("Row-Bot", OWNER, approval_mode=approval_mode)


def test_hard_blocked_launch_does_not_start_cua(fake_client, fake_transport) -> None:
    service = ComputerUseService(
        client_factory=lambda: fake_client,
        approval_callback=lambda _payload: True,
    )

    with pytest.raises(ComputerUseError, match="cannot be targeted") as exc_info:
        service.launch_app("Row-Bot", OWNER, approval_mode="full")

    assert exc_info.value.code == "hard_blocked"
    assert fake_transport.opened is False
    assert fake_transport.calls == []


def test_ask_denial_occurs_before_any_app_mutation(fake_client, fake_transport) -> None:
    service = ComputerUseService(
        client_factory=lambda: fake_client,
        approval_callback=lambda _payload: False,
    )
    service.acquire(OWNER, validate_context=False)

    with pytest.raises(ComputerUseError, match="not approved"):
        service.launch_app("Calculator", OWNER, approval_mode="approve")

    assert "launch_app" not in [name for name, _args in fake_transport.calls]
