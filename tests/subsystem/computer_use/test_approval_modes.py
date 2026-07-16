from __future__ import annotations

import pytest

from row_bot.computer_use.service import ComputerUseError, ComputerUseService, LeaseOwner


OWNER = LeaseOwner("policy-thread", "policy-generation", "policy-task")


@pytest.mark.parametrize(
    ("approval_mode", "expected_prompts", "allowed"),
    [
        ("block", 0, False),
        ("approve", 1, True),
        ("allow_all", 0, True),
    ],
)
def test_launch_uses_shared_block_ask_auto_policy(
    fake_client,
    fake_transport,
    approval_mode: str,
    expected_prompts: int,
    allowed: bool,
) -> None:
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


def test_auto_is_not_cached_as_ask_app_consent(fake_client) -> None:
    prompts: list[dict] = []
    service = ComputerUseService(
        client_factory=lambda: fake_client,
        approval_callback=lambda payload: prompts.append(payload) or True,
    )
    service.acquire(OWNER, validate_context=False)
    target_id = service.list_windows(OWNER, app="Calculator")[0]["target_id"]

    service.capture(target_id, OWNER, approval_mode="allow_all")
    assert prompts == []

    service.capture(target_id, OWNER, approval_mode="approve")
    assert len(prompts) == 1


def test_auto_skips_optional_consequential_prompt_but_not_hard_surface_rules(
    fake_client,
    fake_transport,
) -> None:
    prompts: list[dict] = []
    service = ComputerUseService(
        client_factory=lambda: fake_client,
        approval_callback=lambda payload: prompts.append(payload) or True,
    )
    service.acquire(OWNER, validate_context=False)
    target_id = service.list_windows(OWNER, app="Calculator")[0]["target_id"]
    observation = service.capture(target_id, OWNER, approval_mode="allow_all")

    service.act(
        "click",
        target_id,
        OWNER,
        element_token=observation.elements[1].token,
        expected_effect="Submit result",
        approval_mode="allow_all",
    )

    assert prompts == []
    assert [name for name, _args in fake_transport.calls].count("click") == 1
    with pytest.raises(ComputerUseError, match="cannot be targeted"):
        service.launch_app("Row-Bot", OWNER, approval_mode="allow_all")


def test_hard_blocked_launch_does_not_start_cua(fake_client, fake_transport) -> None:
    service = ComputerUseService(
        client_factory=lambda: fake_client,
        approval_callback=lambda _payload: True,
    )

    with pytest.raises(ComputerUseError, match="cannot be targeted") as exc_info:
        service.launch_app("Row-Bot", OWNER, approval_mode="allow_all")

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
