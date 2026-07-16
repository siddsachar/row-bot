from __future__ import annotations

from row_bot.computer_use.service import ComputerUseService, LeaseOwner


OWNER = LeaseOwner("vision-thread", "vision-generation", "vision-task")


class _Vision:
    _model = "local::fake-vision"

    def __init__(self) -> None:
        self.calls = []

    def analyze(self, image_bytes: bytes, question: str) -> str:
        self.calls.append((image_bytes, question))
        return "The Equals button is visible."


def test_visual_question_uses_ephemeral_capture_only_when_requested(fake_client) -> None:
    vision = _Vision()
    service = ComputerUseService(client_factory=lambda: fake_client, approval_callback=lambda _payload: True, vision_service=vision)
    service.acquire(OWNER, validate_context=False)
    target = service.list_windows(OWNER, app="Calculator")[0]["target_id"]
    service.capture(target, OWNER)
    assert vision.calls == []
    observed = service.capture(target, OWNER, visual_question="Where is Equals?")
    assert len(vision.calls) == 1
    assert vision.calls[0][0] == observed.screenshot
    assert "Equals button" in observed.model_text()
    assert "base64" not in observed.model_text()


def test_launch_app_can_vision_ground_its_single_fresh_capture(fake_client) -> None:
    vision = _Vision()
    service = ComputerUseService(
        client_factory=lambda: fake_client,
        approval_callback=lambda _payload: True,
        vision_service=vision,
    )
    service.acquire(OWNER, validate_context=False)

    windows = service.launch_app(
        "Calculator",
        OWNER,
        visual_question="Identify the screenshot-local control region.",
    )

    assert windows
    observed = service.current_observation(windows[0]["target_id"])
    assert observed is not None
    assert len(vision.calls) == 1
    assert vision.calls[0][0] == observed.screenshot
    assert "Equals button" in observed.model_text()


def test_semantic_element_action_does_not_add_redundant_vision_call(fake_client) -> None:
    vision = _Vision()
    service = ComputerUseService(
        client_factory=lambda: fake_client,
        approval_callback=lambda _payload: True,
        vision_service=vision,
    )
    service.acquire(OWNER, validate_context=False)
    target = service.list_windows(OWNER, app="Calculator")[0]["target_id"]
    observed = service.capture(target, OWNER)

    service.act(
        "click",
        target,
        OWNER,
        element_token=observed.elements[0].token,
        capture_after=True,
        visual_question="Confirm the semantic button changed visually.",
    )

    assert vision.calls == []
