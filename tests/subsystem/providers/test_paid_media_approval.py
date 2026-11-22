"""Pictures and videos a provider charges for go through the approval gate (fake HTTP, fake reviewer only).

Only a call billed per call asks: a provider on an API key, or a paid OpenRouter model. A plan's own
pictures (ChatGPT, Grok) and free models run as they always have.
"""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.subsystem

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode("ascii")


class _Response:
    def __init__(self, payload=None, *, content=b""):
        self._payload = payload if payload is not None else {}
        self.status_code = 200
        self.content = content
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


@pytest.fixture
def media(monkeypatch):
    """The agent's image and video tools on a chosen model, a fake key, fake HTTP and a fake approval card."""
    import httpx

    import row_bot.api_keys as api_keys
    from row_bot.tools import approval_gate, image_gen_tool, video_gen_tool

    state = SimpleNamespace(image="", video="", mode="approve", answer=True, asked=[], calls=[], videos=[],
                            image_tool=image_gen_tool)

    def answer(method, url, **kwargs):
        state.calls.append((method, url))
        if method == "POST" and url.endswith("/images"):
            return _Response({"data": [{"b64_json": PNG}]})
        if method == "POST" and url.endswith("/videos"):
            return _Response({"id": "vid-1"})
        if method == "GET" and url.endswith("/videos/vid-1"):
            return _Response({"status": "completed"})
        if method == "GET" and url.endswith("/content?index=0"):
            return _Response(content=b"mp4-bytes")
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: answer("GET", url, **kwargs))
    monkeypatch.setattr(httpx, "request", lambda method, url, **kwargs: answer(method, url, **kwargs))
    monkeypatch.setattr(api_keys, "get_key", lambda env: f"synthetic-{env.lower()}")
    monkeypatch.setattr(image_gen_tool.registry, "get_tool_config",
                        lambda tool, *args: state.image if tool == "image_gen" else state.video)
    monkeypatch.setattr(image_gen_tool, "_image_cache", {})
    monkeypatch.setattr(image_gen_tool, "_last_generated_image", None)
    monkeypatch.setattr(image_gen_tool, "_save_image_to_disk", lambda *args, **kwargs: None)
    monkeypatch.setattr(video_gen_tool, "_save_video_to_disk", lambda data, *args: state.videos.append(data) or None)
    monkeypatch.setattr(video_gen_tool.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: state.mode)
    monkeypatch.setattr(approval_gate, "_interrupt", lambda payload: state.asked.append(payload) or state.answer)
    tools = {tool.name: tool for owner in (image_gen_tool.ImageGenTool(), video_gen_tool.VideoGenTool())
             for tool in owner.as_langchain_tools()}
    state.call = lambda name, **args: tools[name].invoke(args)
    return state


def test_an_api_key_picture_under_ask_asks_and_a_denial_makes_nothing(media):
    media.image = "openrouter/bytedance-seed/seedream-4.5"
    media.answer = False

    result = media.call("generate_image", prompt="a lighthouse at dawn")

    [card] = media.asked
    assert card["tool"] == "generate_image"
    assert card["question"] == "Make a picture on OpenRouter? It costs money on your OpenRouter account."
    assert "bytedance-seed/seedream-4.5" in card["description"] and "a lighthouse at dawn" in card["description"]
    assert result.startswith("Approval: asked; denied by you")
    assert "nothing was made and nothing was charged" in result
    assert media.calls == []
    assert media.image_tool.get_and_clear_last_image() is None


def test_an_approved_picture_under_ask_is_made(media):
    media.image = "openrouter/bytedance-seed/seedream-4.5"

    result = media.call("generate_image", prompt="a lighthouse at dawn")

    assert len(media.asked) == 1
    assert result.splitlines()[0] == "Approval: asked; approved by you"
    assert result.splitlines()[1].startswith("Image generated successfully.")
    assert media.image_tool.get_and_clear_last_image() == PNG


def test_auto_approves_a_picture_the_person_asked_for_and_it_is_made(media, monkeypatch):
    """The reviewer sees only the person's own words and the card: a picture they asked for is approved."""
    from row_bot import approval_review

    media.image = "openrouter/bytedance-seed/seedream-4.5"
    media.mode = "auto"
    seen = []

    def reviewer(messages, _runtime):
        case = json.loads(messages[1]["content"])
        seen.append(case)
        asked_for_one = any("draw" in text.casefold() for text in case["person_asked"])
        costs = "costs money" in case["action"]["details"] and case["action"]["risk_flags"]
        if asked_for_one and costs and case["action"]["tool"] == "generate_image":
            return '{"decision": "approve", "reason": "You asked for this picture."}'
        return '{"decision": "ask", "reason": "Not what you asked for."}'

    monkeypatch.setattr(approval_review, "current_request",
                        lambda _runtime=None: approval_review.ReviewRequest(messages=("Draw me a lighthouse at dawn",)))
    monkeypatch.setattr(approval_review, "_model_ref", lambda _runtime: "model:openai:gpt-test")
    monkeypatch.setattr(approval_review, "_call_reviewer", reviewer)

    result = media.call("generate_image", prompt="a lighthouse at dawn, watercolour")

    assert media.asked == []
    assert len(seen) == 1 and "draw me a lighthouse at dawn" in seen[0]["person_asked"][0].casefold()
    assert result.splitlines()[0] == "Approval: approved by the reviewer: You asked for this picture"
    assert media.image_tool.get_and_clear_last_image() == PNG


@pytest.mark.parametrize("selection", ["codex/gpt-image-2", "openrouter/some-lab/image-model:free"])
def test_a_picture_a_plan_includes_or_a_free_model_never_asks(media, monkeypatch, selection):
    media.image = selection
    media.answer = False
    made = []
    monkeypatch.setitem(media.image_tool._DIRECT_IMAGE, "codex",
                        lambda *args: made.append(args) or PNG)

    result = media.call("generate_image", prompt="a lighthouse")

    assert media.asked == []
    assert result.startswith("Image generated successfully.")
    assert (made if selection.startswith("codex/") else media.calls)


def test_hands_off_makes_a_paid_picture_without_asking(media):
    media.image = "openrouter/bytedance-seed/seedream-4.5"
    media.mode = "full"

    result = media.call("generate_image", prompt="a lighthouse")

    assert media.asked == []
    assert result.splitlines()[0] == "Approval: allowed by Hands-off"
    assert media.image_tool.get_and_clear_last_image() == PNG


def test_block_refuses_a_paid_picture(media):
    media.image = "openrouter/bytedance-seed/seedream-4.5"
    media.mode = "block"

    result = media.call("generate_image", prompt="a lighthouse")

    assert result.startswith("BLOCKED: this would make a picture on OpenRouter, which costs money")
    assert media.asked == [] and media.calls == []


def test_a_paid_edit_with_no_picture_to_edit_says_so_without_asking(media):
    media.image = "openrouter/bytedance-seed/seedream-4.5"

    result = media.call("edit_image", prompt="make it night", image_source="missing-photo.png")

    assert result == ("Couldn't find missing-photo.png: name a picture in this chat's files, 'last', or a file in "
                      "the workspace.")
    assert media.asked == [] and media.calls == []


def test_a_paid_video_under_ask_asks_and_a_denial_makes_nothing(media):
    media.video = "openrouter/google/veo-3.1"
    media.answer = False

    result = media.call("generate_video", prompt="waves at dusk", duration_seconds=8, resolution="720p")

    [card] = media.asked
    assert card["tool"] == "generate_video"
    assert card["question"] == "Make a video (8 seconds, 720p) on OpenRouter? It costs money on your OpenRouter account."
    assert result.startswith("Approval: asked; denied by you")
    assert media.calls == [] and media.videos == []


def test_a_clip_the_model_cant_extend_is_refused_before_the_card(media, monkeypatch):
    from row_bot.tools import video_gen_tool

    monkeypatch.setattr(video_gen_tool, "_resolve_clip", lambda value: b"\x00\x00\x00\x18ftypmp42clip")
    media.video = "openrouter/google/veo-3.1"

    result = media.call("generate_video", prompt="the door opens", extend="last")

    assert result.startswith("OpenRouter can't continue a clip with google/veo-3.1.")
    assert media.asked == [] and media.calls == [] and media.videos == []


def test_a_1080p_default_is_on_the_card_with_its_price_and_in_the_result(media, monkeypatch):
    """The highest resolution is the default, never a silent one: the card asks for it and prices it."""
    from row_bot.tools import video_gen_tool

    client = SimpleNamespace(
        interactions=SimpleNamespace(create=lambda **request: SimpleNamespace(
            output_video=SimpleNamespace(data=base64.b64encode(b"omni-bytes").decode()))))
    monkeypatch.setattr(video_gen_tool, "_get_google_client", lambda: client)
    media.video = "google/gemini-omni-1.2-flash"

    result = media.call("generate_video", prompt="waves at dusk", duration_seconds=8)

    [card] = media.asked
    assert card["question"] == "Make a video (8 seconds, 1080p) on Google? It costs money on your Google account."
    assert card["price"]["low_usd"] == pytest.approx(0.832)
    assert "Duration: 8s | Aspect ratio: 16:9 | Resolution: 1080p | Mode: text-to-video | Provider: Google" in result
    assert media.videos == [b"omni-bytes"]


@pytest.fixture
def design(media, monkeypatch):
    """An open design whose agent tools are bound, with nothing written to disk."""
    from row_bot.designer import tool as designer_tool
    from row_bot.designer.state import DesignerPage, DesignerProject

    project = DesignerProject(id="design-1", name="Launch", pages=[DesignerPage(title="Cover", html="<h1>Hi</h1>")])
    monkeypatch.setattr(designer_tool, "get_active_project", lambda: project)
    monkeypatch.setattr(designer_tool, "_pre_mutate", lambda *args, **kwargs: None)
    monkeypatch.setattr(designer_tool, "save_project", lambda project: None)
    monkeypatch.setattr(designer_tool, "_build_asset_fragment",
                        lambda project, *, data, **kwargs: (f"<img data-bytes='{len(data)}'>", "asset-1"))
    tools = {tool.name: tool for tool in designer_tool.DesignerTool().as_langchain_tools()}
    media.design = project
    media.call_design = lambda name, **args: tools[name].invoke(args)
    return media


def test_a_designer_picture_under_ask_asks_and_a_denial_changes_nothing(design):
    design.image = "openrouter/bytedance-seed/seedream-4.5"
    design.answer = False

    result = design.call_design("designer_generate_image", prompt="a rocket on a launch pad")

    [card] = design.asked
    assert card["tool"] == "designer_generate_image"
    assert card["question"] == ("Make a picture for this design on OpenRouter? "
                                "It costs money on your OpenRouter account.")
    assert result.startswith("Approval: asked; denied by you")
    assert design.calls == [] and design.design.pages[0].html == "<h1>Hi</h1>"


def test_auto_approves_a_designer_picture_the_person_asked_for_and_it_is_placed(design, monkeypatch):
    from row_bot import approval_review

    design.image = "openrouter/bytedance-seed/seedream-4.5"
    design.mode = "auto"
    seen = []

    def reviewer(messages, _runtime):
        case = json.loads(messages[1]["content"])
        seen.append(case)
        if "costs money" in case["action"]["details"] and "picture" in case["person_asked"][0]:
            return '{"decision": "approve", "reason": "You asked for a picture on the cover."}'
        return '{"decision": "ask", "reason": "Not what you asked for."}'

    monkeypatch.setattr(approval_review, "current_request", lambda _runtime=None: approval_review.ReviewRequest(
        messages=("Put a picture of a rocket on the cover",)))
    monkeypatch.setattr(approval_review, "_model_ref", lambda _runtime: "model:openai:gpt-test")
    monkeypatch.setattr(approval_review, "_call_reviewer", reviewer)

    result = design.call_design("designer_generate_image", prompt="a rocket on a launch pad")

    assert design.asked == [] and len(seen) == 1
    assert seen[0]["action"]["tool"] == "designer_generate_image"
    assert result.splitlines()[0] == "Approval: approved by the reviewer: You asked for a picture on the cover"
    assert "Generated AI image" in result and "data-bytes=" in design.design.pages[0].html


def test_an_approved_video_under_ask_is_made(media):
    media.video = "openrouter/google/veo-3.1"

    result = media.call("generate_video", prompt="waves at dusk")

    assert len(media.asked) == 1
    assert result.splitlines()[0] == "Approval: asked; approved by you"
    assert media.videos == [b"mp4-bytes"]


# ── The price on the card (D4) ───────────────────────────────────────────────


def test_a_paid_picture_card_shows_its_list_price_for_the_quality_and_size_asked(media):
    media.image = "openai/gpt-image-2"
    media.answer = False

    result = media.call("generate_image", prompt="a lighthouse", size="1024x1024", quality="medium")

    [card] = media.asked
    assert card["price"] == {"status": "known", "low_usd": 0.053, "high_usd": 0.053,
                             "basis": "OpenAI's list price, checked 15 Nov 2026"}
    assert result.startswith("Approval: asked; denied by you") and media.calls == []


def test_a_quality_left_to_the_provider_shows_the_range_it_can_cost(media):
    media.image = "openai/gpt-image-2"
    media.answer = False

    media.call("generate_image", prompt="a lighthouse", size="1024x1024")

    [card] = media.asked
    assert (card["price"]["low_usd"], card["price"]["high_usd"]) == (0.006, 0.211)


def test_a_model_with_no_list_price_still_asks_and_says_the_price_is_unknown(media):
    media.image = "openrouter/bytedance-seed/seedream-4.5"
    media.answer = False

    result = media.call("generate_image", prompt="a lighthouse")

    [card] = media.asked
    assert card["price"] == {"status": "unknown"}
    assert result.startswith("Approval: asked; denied by you") and media.calls == []


def test_a_paid_video_is_priced_by_its_seconds_and_resolution(media):
    media.video = "google/gemini-omni-1.2-flash"
    media.answer = False

    media.call("generate_video", prompt="waves at dusk", duration_seconds=8, resolution="720p")

    [card] = media.asked
    assert card["price"]["status"] == "known"
    assert card["price"]["low_usd"] == card["price"]["high_usd"] == pytest.approx(0.56)


@pytest.mark.parametrize(("quality", "usd"), [("auto", 0.06), ("high", 0.08)])
def test_xais_own_model_list_prices_the_tier_it_will_make_over_the_table(media, monkeypatch, quality, usd):
    """xAI lists each picture tier's price (in 1/100,000,000ths of a cent); the tier sent is the one priced."""
    from row_bot.providers import capability_resolution
    from row_bot.providers.xai_catalog import xai_generation_parameters_from_item

    parameters = xai_generation_parameters_from_item({"pricing": [
        {"quality": "low", "resolution": "1k", "price_per_image": 400_000_000},
        {"quality": "medium", "resolution": "1k", "price_per_image": 600_000_000},
        {"quality": "medium", "resolution": "2k", "price_per_image": 800_000_000},
    ]})
    monkeypatch.setattr(capability_resolution, "resolve_capability_snapshot",
                        lambda *args, **kwargs: {"generation_parameters": parameters})
    media.image = "xai/grok-imagine-image-2.0"
    media.answer = False

    media.call("generate_image", prompt="a lighthouse", quality=quality)

    [card] = media.asked
    assert card["price"] == {"status": "known", "low_usd": usd, "high_usd": usd, "basis": "from xAI's model list"}


def test_the_reviewer_is_shown_the_same_action_with_or_without_a_price(media, monkeypatch):
    """The price is for the person's card: what the reviewer reads about the call is unchanged."""
    from row_bot import approval_review

    seen = []
    monkeypatch.setattr(approval_review, "current_request",
                        lambda _runtime=None: approval_review.ReviewRequest(messages=("Draw me a lighthouse",)))
    monkeypatch.setattr(approval_review, "_model_ref", lambda _runtime: "model:openai:gpt-test")
    monkeypatch.setattr(approval_review, "_call_reviewer",
                        lambda messages, _runtime: seen.append(json.loads(messages[1]["content"])["action"])
                        or '{"decision": "ask", "reason": "Check it."}')
    media.mode, media.answer = "auto", False

    for selection in ("openai/gpt-image-2", "openrouter/bytedance-seed/seedream-4.5"):
        media.image = selection
        media.call("generate_image", prompt="a lighthouse", size="1024x1024", quality="medium")

    priced, unpriced = seen
    assert priced.keys() == unpriced.keys()
    assert not any("$" in str(value) for value in priced.values())
    assert [card["price"]["status"] for card in media.asked] == ["known", "unknown"]
