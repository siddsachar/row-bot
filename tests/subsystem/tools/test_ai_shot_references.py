"""AI shots that hold together (C3): references, a last frame and extending a clip on each video provider,
saved characters and styles, resolution defaults, and the new picture shapes and references. Fake HTTP and fake
SDK clients only; nothing reaches a provider."""
# ruff: noqa: F401, F811 (the platform and media fixtures are imported)
from __future__ import annotations

import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from tests.contracts.client_platform.test_headless_lifecycle import platform
from tests.subsystem.providers.test_media_routes import _Response, media

pytestmark = pytest.mark.subsystem

FIRST, LAST, MAYA, CUP = (b"\x89PNG\r\n\x1a\n" + name for name in (b"first", b"last", b"maya", b"cup"))
CLIP = b"\x00\x00\x00\x18ftypmp42clip"


def _uri(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _png(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="PNG")
    return out.getvalue()


def _shot(**fields):
    from row_bot.tools.video_gen_tool import _Shot

    return _Shot(**fields)


@pytest.fixture(autouse=True)
def _first_frame(monkeypatch):
    """animate_image's picture to start on is FIRST."""
    from row_bot.tools import video_gen_tool

    monkeypatch.setattr(video_gen_tool, "_resolve_image_source", lambda source: FIRST)


def _make(media, prompt, seconds, aspect, resolution, first, *, shot):
    """A shot through the tools' own path: animating FIRST when it has a first frame, else from the prompt."""
    if first is None:
        return media.video_tool._generate_video(prompt, seconds, aspect, resolution, shot=shot)
    return media.video_tool._animate_image(prompt, "last", seconds, aspect, resolution, shot=shot)


def _job(media, submit_suffix: str, poll_suffix: str, submitted: dict, done: dict, link: str = "") -> None:
    media.answers[("POST", submit_suffix)] = [_Response(submitted)]
    media.answers[("GET", poll_suffix)] = [_Response(done)]
    if link:
        media.answers[("GET", link)] = [_Response(content=b"mp4-bytes")]


# ── Each provider maps references, a last frame and extend, or says it doesn't take them ──────────


def _openrouter(media):
    _job(media, "/videos", "/videos/or-1", {"id": "or-1"}, {"status": "completed"})
    media.answers[("GET", "/videos/or-1/content?index=0")] = [_Response(content=b"mp4-bytes")]


def test_openrouter_sends_first_and_last_frames_and_references_as_its_api_names_them(media):
    media.video = "openrouter/kwaivgi/kling-v3"
    _openrouter(media)

    result = _make(media, "she turns", 5, "16:9", "1080p", FIRST, shot=_shot(last_frame=LAST))

    assert "Mode: image-to-video | Provider: OpenRouter" in result
    assert media.calls[0].json["frame_images"] == [
        {"type": "image_url", "image_url": {"url": _uri(FIRST)}, "frame_type": "first_frame"},
        {"type": "image_url", "image_url": {"url": _uri(LAST)}, "frame_type": "last_frame"}]

    media.calls.clear()
    result = media.video_tool._generate_video("she walks in", 5, "16:9", "1080p", shot=_shot(references=[MAYA, CUP]))

    assert "Mode: reference-to-video" in result
    assert media.calls[0].json["input_references"] == [
        {"type": "image_url", "image_url": {"url": _uri(MAYA)}}, {"type": "image_url", "image_url": {"url": _uri(CUP)}}]
    assert "frame_images" not in media.calls[0].json


def test_openrouter_uses_frames_over_references_and_says_so(media):
    media.video = "openrouter/kwaivgi/kling-v3"
    _openrouter(media)

    result = _make(media, "she turns", 5, "16:9", "1080p", FIRST, shot=_shot(references=[MAYA]))

    assert "References: OpenRouter takes reference pictures or a first and last frame, not both" in result
    assert "input_references" not in media.calls[0].json


def test_together_sends_frames_and_references_in_its_media_object(media):
    media.video = "together/ByteDance/Seedance-2.0"
    _job(media, "/videos", "/videos/tg-1", {"id": "tg-1"},
         {"status": "completed", "outputs": {"video_url": "https://api.together.ai/out/v"}}, "/out/v")

    result = _make(media, "she turns", 6, "16:9", "720p", FIRST, shot=_shot(last_frame=LAST, references=[MAYA]))

    assert result.startswith("Video generated successfully.")
    assert media.calls[0].json["media"] == {
        "frame_images": [{"input_image": _uri(FIRST), "frame": "first"}, {"input_image": _uri(LAST), "frame": "last"}],
        "reference_images": [_uri(MAYA)]}


def test_minimax_sends_frame_and_reference_roles_but_never_mixes_them(media):
    media.video = "minimax/MiniMax-H3"
    _job(media, "/video_generation", "/query/video_generation/m-1", {"task_id": "m-1"},
         {"task": {"status": "succeeded", "content": {"url": "https://cdn.minimax.example/v.mp4"}}}, "/v.mp4")

    _make(media, "she turns", 6, "16:9", "1080p", FIRST, shot=_shot(last_frame=LAST))
    roles = [(item.get("role"), item.get("image_url", {}).get("url")) for item in media.calls[0].json["content"][1:]]
    assert roles == [("first_frame", _uri(FIRST)), ("last_frame", _uri(LAST))]
    assert media.calls[0].json["ratio"] == "adaptive"

    media.calls.clear()
    result = media.video_tool._generate_video("she walks in", 6, "9:16", "1080p", shot=_shot(references=[MAYA, CUP]))
    body = media.calls[0].json
    assert [item.get("role") for item in body["content"]] == [None, "reference_image", "reference_image"]
    assert body["ratio"] == "9:16" and "Mode: reference-to-video" in result

    media.calls.clear()
    result = _make(media, "she turns", 6, "16:9", "1080p", FIRST, shot=_shot(references=[MAYA]))
    assert "not both, so the shot was made from the frames without the references" in result
    assert [item.get("role") for item in media.calls[0].json["content"]] == [None, "first_frame"]


def test_zai_takes_a_last_frame_only_after_a_first_and_no_references(media):
    media.video = "zai/cogvideox-3"
    _job(media, "/videos/generations", "/async-result/z-1", {"id": "z-1"},
         {"task_status": "SUCCESS", "video_result": [{"url": "https://cdn.z.ai/v.mp4"}]}, "/v.mp4")

    _make(media, "she turns", 5, "16:9", "1080p", FIRST, shot=_shot(last_frame=LAST))
    assert media.calls[0].json["image_url"] == [_uri(FIRST), _uri(LAST)]

    media.calls.clear()
    result = media.video_tool._generate_video("she walks in", 5, "16:9", "1080p",
                                              shot=_shot(references=[MAYA], last_frame=LAST))
    assert "Last frame: Z.AI takes one only with a first frame, so it wasn't used." in result
    assert "References: Z.AI doesn't take reference pictures for cogvideox-3, so the shot was made without them." in result
    assert "image_url" not in media.calls[0].json


@pytest.mark.parametrize(("model", "first", "shot", "media_list", "note"), [
    ("wan2.7-i2v", FIRST, {"last_frame": LAST},
     [{"type": "first_frame", "url": _uri(FIRST)}, {"type": "last_frame", "url": _uri(LAST)}], ""),
    ("wan2.7-r2v", None, {"references": [MAYA, CUP]},
     [{"type": "reference_image", "url": _uri(MAYA)}, {"type": "reference_image", "url": _uri(CUP)}], ""),
    ("wan2.7-t2v", None, {"references": [MAYA]}, None, "References: Qwen (Alibaba Cloud) doesn't take reference"),
])
def test_qwen_wan_takes_frames_on_i2v_and_references_on_r2v(media, model, first, shot, media_list, note):
    media.video = f"dashscope/{model}"
    _job(media, "/video-synthesis", "/tasks/t-1", {"output": {"task_id": "t-1"}},
         {"output": {"task_status": "SUCCEEDED", "video_url": "https://oss.example/v.mp4"}}, "/v.mp4")

    result = _make(media, "she turns", 5, "16:9", "1080p", first, shot=_shot(**shot))

    assert media.calls[0].json["input"].get("media") == media_list
    assert note in result


@pytest.mark.parametrize(("selection", "why"), [
    ("dashscope/wan2.7-i2v", "Wan continues a clip only from a public web link"),
    ("google/veo-3.1-generate-001", "Veo continues only a clip it made itself in the last two days"),
    ("together/ByteDance/Seedance-2.0", ""),
    ("minimax/MiniMax-H3", ""),
    ("openrouter/google/veo-3.1", ""),
    ("atlascloud/kwaivgi/kling-v2.1-t2v-master", ""),
])
def test_a_model_that_cant_extend_a_clip_says_so_and_makes_nothing(media, selection, why):
    media.video = selection

    result = media.video_tool._generate_video("the door opens", 6, shot=_shot(extend=CLIP))

    assert " can't continue a clip with " in result and "Nothing was made or charged." in result
    assert why in result
    assert media.calls == [] and media.videos == []


def test_atlas_cloud_says_it_takes_no_references_or_last_frame(media):
    media.video = "atlascloud/kwaivgi/kling-v2.1-t2v-master"
    _job(media, "/model/generateVideo", "/model/prediction/p-1", {"data": {"id": "p-1"}},
         {"data": {"status": "completed", "outputs": ["https://atlas.example/v.mp4"]}}, "/v.mp4")

    result = media.video_tool._generate_video("she walks in", shot=_shot(references=[MAYA], last_frame=LAST))

    assert "References: Atlas Cloud doesn't take reference pictures" in result
    assert "Last frame: Atlas Cloud doesn't take one" in result
    assert media.calls[0].json == {"model": "kwaivgi/kling-v2.1-t2v-master", "prompt": "she walks in"}


@pytest.fixture
def xai(media, monkeypatch):
    """xAI's media API faked at its request helpers: each POST answered with a job, done at once."""
    from row_bot.providers import xai_media

    sent: list[tuple[str, str, dict | None]] = []

    def request(provider_id, method, path, *, json=None, timeout=None):
        sent.append((method, path, json))
        return {"request_id": "x-1"} if method == "POST" else {"status": "done", "video": {"url": "https://x.example/v"}}

    monkeypatch.setattr(xai_media, "xai_media_json_request", request)
    monkeypatch.setattr(xai_media, "xai_media_get", lambda *args, **kwargs: SimpleNamespace(content=b"mp4-bytes"))
    return sent


def test_xai_sends_its_first_frame_last_frame_and_reference_images(media, xai):
    media.video = "xai/grok-imagine-video-1.5"

    result = media.video_tool._animate_image("she turns", "last", 8, "16:9", "",
                                             shot=_shot(last_frame=LAST, references=[MAYA]))

    method, path, body = xai[0]
    assert (method, path) == ("POST", "/videos/generations")
    assert body["last_frame"] == {"url": _uri(LAST)} and body["reference_images"] == [{"url": _uri(MAYA)}]
    assert body["resolution"] == "720p"
    assert "Resolution: xAI makes shots from reference pictures at up to 720p." in result


def test_xai_extends_a_clip_through_its_extensions_api(media, xai):
    media.video = "xai/grok-imagine-video"

    result = media.video_tool._generate_video("the door opens", 20, shot=_shot(extend=CLIP, references=[MAYA]))

    assert xai[0] == ("POST", "/videos/extensions", {"model": "grok-imagine-video", "prompt": "the door opens",
                                                    "duration": 10, "video": {"url": _uri(CLIP, "video/mp4")}})
    assert ("Duration: 10s more | Aspect ratio: the clip's own | Resolution: up to 720p, the clip's own | "
            "Mode: extension") in result
    assert "Not used: the reference pictures. Extending continues the clip as it is." in result
    assert media.videos == [b"mp4-bytes"]


def test_the_classic_xai_model_says_it_takes_no_last_frame(media, xai):
    media.video = "xai/grok-imagine-video"

    result = media.video_tool._generate_video("she turns", shot=_shot(last_frame=LAST))

    assert "Last frame: xAI doesn't take one for grok-imagine-video" in result
    assert "last_frame" not in xai[0][2]


class _Video:
    def save(self, path):
        with open(path, "wb") as out:
            out.write(b"veo-bytes")


@pytest.fixture
def google(media, monkeypatch):
    """Google's SDK faked: Veo's operation finishes at once, Omni's interaction answers with a file."""
    from row_bot.tools import video_gen_tool

    state = SimpleNamespace(veo=[], omni=[], uploads=[])

    def upload(*, file, config):
        state.uploads.append((file.read(), config["mime_type"]))
        name = f"files/up{len(state.uploads)}"
        return SimpleNamespace(name=name, uri=f"https://generativelanguage.googleapis.com/v1beta/{name}", state="ACTIVE")

    def generate_videos(**request):
        state.veo.append(request)
        return SimpleNamespace(done=True, response=SimpleNamespace(generated_videos=[SimpleNamespace(video=_Video())]))

    def create(**request):
        state.omni.append(request)
        return SimpleNamespace(output_video=SimpleNamespace(
            uri="https://generativelanguage.googleapis.com/v1beta/files/out1"))

    client = SimpleNamespace(
        models=SimpleNamespace(generate_videos=generate_videos), interactions=SimpleNamespace(create=create),
        files=SimpleNamespace(upload=upload, get=lambda *, name: SimpleNamespace(name=name, state="ACTIVE"),
                              download=lambda *, file: b"omni-bytes" if isinstance(file, str) else None))
    monkeypatch.setattr(video_gen_tool, "_get_google_client", lambda: client)
    monkeypatch.setattr(video_gen_tool, "_GOOGLE_FILES", {})
    return state


def test_veo_takes_reference_pictures_at_eight_seconds_and_a_last_frame_with_a_first(media, google):
    media.video = "google/veo-3.1-generate-001"

    result = media.video_tool._generate_video("she walks in", 4, shot=_shot(references=[MAYA, CUP]))

    config = google.veo[0]["config"]
    assert [(ref.reference_type.value, ref.image.image_bytes) for ref in config.reference_images] == [
        ("ASSET", MAYA), ("ASSET", CUP)]
    assert (config.duration_seconds, config.resolution) == (8, "1080p")
    assert "Mode: reference-to-video | Provider: Google" in result and media.videos == [b"veo-bytes"]

    result = media.video_tool._animate_image("she turns", "last", 8, shot=_shot(last_frame=LAST, references=[MAYA]))
    request = google.veo[1]
    assert request["image"].image_bytes == FIRST and request["config"].last_frame.image_bytes == LAST
    assert request["config"].reference_images is None
    assert "not both" in result


def test_veo_3_lite_says_it_takes_no_references(media, google):
    media.video = "google/veo-3.1-lite-generate-001"

    result = media.video_tool._generate_video("she walks in", 8, shot=_shot(references=[MAYA]))

    assert "References: Google doesn't take reference pictures for veo-3.1-lite-generate-001" in result
    assert google.veo[0]["config"].reference_images is None


def test_omni_sends_references_once_as_files_and_says_they_are_references(media, google):
    media.video = "google/gemini-omni-1.2-flash"

    first = media.video_tool._generate_video("she walks in", 12, "9:16", shot=_shot(references=[MAYA, CUP]))
    media.video_tool._generate_video("she sits down", 12, "9:16", shot=_shot(references=[MAYA, CUP]))

    request = google.omni[0]
    assert [part["type"] for part in request["input"]] == ["image", "image", "text"]
    assert request["input"][0]["uri"].endswith("files/up1") and request["input"][-1]["text"] == "she walks in"
    assert request["generation_config"] == {"video_config": {"task": "reference_to_video"}}
    assert request["response_format"] == {"type": "video", "duration": "12s", "delivery": "uri",
                                          "aspect_ratio": "9:16", "resolution": "1080p"}
    # The second shot reuses the uploads: each picture went up once.
    assert google.uploads == [(MAYA, "image/png"), (CUP, "image/png")]
    assert google.omni[1]["input"][0]["uri"] == request["input"][0]["uri"]
    assert "Duration: 12s | Aspect ratio: 9:16 | Resolution: 1080p | Mode: reference-to-video" in first
    assert media.videos == [b"omni-bytes", b"omni-bytes"]


def test_omni_ends_on_a_last_frame_and_extends_a_clip(media, google):
    media.video = "google/gemini-omni-1.2-flash"

    media.video_tool._animate_image("she turns", "last", 15, shot=_shot(last_frame=LAST))
    frames = google.omni[0]
    assert [part["type"] for part in frames["input"]] == ["image", "image", "text"]
    assert frames["response_format"]["duration"] == "10s" and "generation_config" not in frames

    result = media.video_tool._generate_video("the door opens", 6, shot=_shot(extend=CLIP))
    extension = google.omni[1]
    assert [part["type"] for part in extension["input"]] == ["video", "text"]
    assert google.uploads[-1] == (CLIP, "video/mp4")
    assert extension["generation_config"] == {"video_config": {"task": "extend"}}
    assert extension["response_format"] == {"type": "video", "duration": "6s", "delivery": "uri"}
    assert "Mode: extension | Provider: Google" in result


def test_google_lists_omni_as_a_video_model(monkeypatch):
    import row_bot.api_keys as api_keys
    import row_bot.models as models
    from row_bot.providers.catalog import model_info_from_metadata, model_info_to_cache_entry
    from row_bot.tools.video_gen_tool import get_available_video_models

    monkeypatch.setattr(api_keys, "get_key", lambda key: "synthetic" if key == "GOOGLE_API_KEY" else "")
    info = model_info_from_metadata("google", "gemini-omni-1.2-flash", {"supportedGenerationMethods": ["createInteraction"]},
                                    display_name="Gemini Omni Flash")
    monkeypatch.setattr(models, "_cloud_model_cache", {"gemini-omni-1.2-flash": model_info_to_cache_entry(info)})

    assert get_available_video_models() == {"google/gemini-omni-1.2-flash": "💎  Gemini Omni Flash  (Google)"}


# ── The highest resolution each model makes, and why when it's lower ──────────────────────────────


@pytest.mark.parametrize(("selection", "suffix", "sent"), [
    ("minimax/MiniMax-H3", "/video_generation", lambda body: body["resolution"] == "2K"),
    ("minimax/MiniMax-H3-Lite", "/video_generation", lambda body: body["resolution"] == "768P"),
    ("dashscope/wan2.7-t2v", "/video-synthesis", lambda body: body["parameters"]["resolution"] == "1080P"),
    ("zai/cogvideox-3", "/videos/generations", lambda body: body["size"] == "1920x1080"),
    # Together lists no model's resolutions, so each model makes its own.
    ("together/ByteDance/Seedance-2.0", "/videos", lambda body: "resolution" not in body),
])
def test_an_unasked_resolution_is_the_highest_the_model_makes(media, selection, suffix, sent):
    media.video = selection
    media.answers[("POST", suffix)] = [_Response({})]  # No job: only the request is looked at.

    result = media.video_tool._generate_video("waves", 8)

    assert result.endswith("started no video job.")
    assert sent(media.calls[0].json)


@pytest.mark.parametrize(("selection", "seconds", "resolution", "note"), [
    ("google/veo-3.1-generate-001", 8, "1080p", ""),
    ("google/veo-3.1-generate-001", 4, "720p", "Resolution: Veo makes 1080p only at 8 seconds, so this is 720p."),
    ("google/gemini-omni-1.2-flash", 5, "1080p", ""),
])
def test_google_makes_the_highest_resolution_for_the_length(media, google, selection, seconds, resolution,
                                                            note):
    media.video = selection

    result = media.video_tool._generate_video("waves", seconds)

    assert f"Resolution: {resolution} |" in result and note in result


@pytest.mark.parametrize(("model", "resolution"), [("grok-imagine-video-1.5", "1080p"), ("grok-imagine-video", "720p")])
def test_xai_makes_its_models_highest_resolution(media, xai, model, resolution):
    media.video = f"xai/{model}"

    media.video_tool._generate_video("waves", 8)

    assert xai[0][2]["resolution"] == resolution


def test_openrouter_leaves_an_unlisted_models_resolution_to_the_model(media):
    media.video = "openrouter/acme/new-video"
    _openrouter(media)

    result = media.video_tool._generate_video("waves", 8)

    assert "resolution" not in media.calls[0].json and "Resolution: the model's own" in result


# ── Saved characters and styles ─────────────────────────────────────────────────────────────────


def test_a_saved_character_and_style_resolve_to_their_tagged_pictures(platform):
    from row_bot import media_library
    from row_bot.application.attachments import register_attachment
    from row_bot.tools import media_tool
    from row_bot.tools.image_gen_tool import resolve_references

    maya = [register_attachment("conversation-a", f"maya-{n}.png", MAYA + bytes([n]), "image/png") for n in range(4)]
    noir = register_attachment("conversation-b", "noir.png", CUP, "image/png")
    other = register_attachment("conversation-a", "beach.png", FIRST, "image/png")
    for item in maya:
        media_library.set_tags(item["attachment_ref"], ["Character:Maya", "demo"])
    media_library.set_tags(noir["attachment_ref"], ["style : Noir"])
    media_library.set_tags(other["attachment_ref"], ["character: tom"])

    assert media_library.items(query="maya")[0]["tags"] == ["character: maya", "demo"]
    pictures = resolve_references([], character="maya", style="NOIR")
    # A character's three newest pictures, then the style's.
    assert len(pictures) == 4 and pictures[-1] == CUP and set(pictures[:3]) <= {MAYA + bytes([n]) for n in range(4)}

    listed = media_tool._media_find(kind="character")
    assert listed.startswith("2 character(s), each with its newest pictures:")
    assert "- maya: 4 picture(s): " in listed and "- tom: 1 picture(s): beach.png" in listed
    assert media_tool._media_find("tom", kind="character").startswith("1 character(s)")
    assert media_tool._media_find("Ada", kind="character") == (
        "No character named 'Ada' is saved. Save one by tagging a few of its pictures 'character: <name>' with "
        "media_tag.")
    with pytest.raises(ValueError, match="There's no character named 'Ada' in Library › Media"):
        resolve_references([], character="Ada")
    with pytest.raises(ValueError, match="up to 3 reference pictures"):
        resolve_references(["a", "b", "c", "d"])


def test_references_a_last_frame_and_a_clip_are_read_only_where_tools_may_read(platform, monkeypatch):
    """Each goes through media_sources.resolve: a name can't send any file on disk to a provider."""
    from row_bot import tool_outputs
    from row_bot.application.attachments import register_attachment
    from row_bot.data_paths import private_data_dirs
    from row_bot.tools.image_gen_tool import resolve_references
    from row_bot.tools.video_gen_tool import _resolve_clip, _shot_inputs

    monkeypatch.setattr(tool_outputs, "current_thread_id", lambda: "conversation-a")
    register_attachment("conversation-a", "maya.png", MAYA, "image/png")
    private = private_data_dirs()[0]
    private.mkdir(parents=True, exist_ok=True)
    (private / "secret.png").write_bytes(FIRST)
    (private / "secret.mp4").write_bytes(CLIP)

    assert resolve_references(["maya.png"]) == [MAYA]
    assert _shot_inputs([], "", "", "maya.png").last_frame == MAYA
    for read in (lambda: resolve_references([str(private / "secret.png")]),
                 lambda: _shot_inputs([], "", "", str(private / "secret.png")),
                 lambda: _resolve_clip(str(private / "secret.mp4"))):
        with pytest.raises(ValueError, match="private data folder"):
            read()
    with pytest.raises(ValueError) as missing:
        resolve_references(["nowhere.png"])
    assert str(missing.value) == ("Couldn't find nowhere.png: name a picture in this chat's files, 'last', or a "
                                  "file in the workspace.")


@pytest.mark.parametrize("tool", ["edit_image", "animate_image"])
def test_the_picture_to_edit_or_animate_is_read_only_where_tools_may_read(platform, monkeypatch, tmp_path, tool):
    """image_source goes through media_sources.resolve too: a chat file or a workspace file, never the data folder."""
    from row_bot import tool_outputs
    from row_bot.application.attachments import register_attachment
    from row_bot.data_paths import private_data_dirs
    from row_bot.tools import image_gen_tool, video_gen_tool

    sent = []
    monkeypatch.setattr(tool_outputs, "current_thread_id", lambda: "conversation-a")
    monkeypatch.setattr(image_gen_tool, "_get_configured_selection", lambda: "together/flux")
    monkeypatch.setattr(video_gen_tool, "_get_configured_selection", lambda: "together/seedance")
    monkeypatch.setattr(image_gen_tool, "_make_image", lambda prompt, size, quality, image, refs: sent.append(image) or "ok")
    monkeypatch.setattr(video_gen_tool, "_dispatch", lambda prompt, seconds, aspect, res, image, shot: sent.append(image) or "ok")
    call = image_gen_tool._edit_image_tool if tool == "edit_image" else video_gen_tool._animate_image_tool
    register_attachment("conversation-a", "maya.png", MAYA, "image/png")
    (tmp_path / "workspace").mkdir(exist_ok=True)
    (tmp_path / "workspace" / "still.png").write_bytes(CUP)
    private = private_data_dirs()[0]
    private.mkdir(parents=True, exist_ok=True)
    (private / "secret.png").write_bytes(FIRST)

    assert call("make it night", image_source="maya.png") == "ok"
    assert call("make it night", image_source="still.png") == "ok"
    refused = call("make it night", image_source=str(private / "secret.png"))

    assert sent == [MAYA, CUP]
    assert "private data folder" in refused


def test_extending_last_continues_the_chats_newest_video(platform, monkeypatch):
    from row_bot import tool_outputs
    from row_bot.application.attachments import register_attachment
    from row_bot.tools.video_gen_tool import _resolve_clip

    monkeypatch.setattr(tool_outputs, "current_thread_id", lambda: "conversation-a")
    with pytest.raises(ValueError, match="There's no video in this chat's files to extend yet."):
        _resolve_clip("last")
    register_attachment("conversation-a", "shot-1.mp4", CLIP, "video/mp4")
    register_attachment("conversation-a", "still.png", FIRST, "image/png")

    assert _resolve_clip("last") == CLIP == _resolve_clip("shot-1.mp4")
    with pytest.raises(ValueError, match="isn't a video"):
        _resolve_clip("still.png")


# ── Pictures: 16:9, 9:16 and 4:5, and several references ────────────────────────────────────────


def test_new_shapes_go_as_sizes_to_providers_that_take_any_size(media):
    media.image = "together/black-forest-labs/FLUX.1-schnell"
    media.answers[("POST", "/images/generations")] = [_Response({"data": [{"b64_json": base64.b64encode(
        _png(1536, 864)).decode()}]})]

    result = media.image_tool._generate_image("a lighthouse", size="1536x864")

    assert (media.calls[0].json["width"], media.calls[0].json["height"]) == (1536, 864)
    assert "Cropped" not in result

    media.calls.clear()
    media.image = "zai/glm-image"
    media.answers[("POST", "/images/generations")] = [_Response({"data": [{"b64_json": base64.b64encode(
        _png(1024, 1280)).decode()}]})]
    media.image_tool._generate_image("a lighthouse", size="1024x1280")
    assert media.calls[0].json["size"] == "1024x1280"


def test_a_shape_the_provider_lacks_is_made_at_its_nearest_and_cropped(media, monkeypatch):
    from row_bot.providers import xai_media

    sent = []
    made = base64.b64encode(_png(900, 1200)).decode()
    monkeypatch.setattr(xai_media, "xai_media_json_request",
                        lambda provider, method, path, *, json, timeout: sent.append((path, json)) or {
                            "data": [{"b64_json": made}]})
    shown = []
    monkeypatch.setattr(media.image_tool, "_set_pending_image", lambda value, name="": shown.append(value))
    media.image = "xai/grok-imagine-image"

    result = media.image_tool._generate_image("a lighthouse", size="1024x1280")

    assert sent[0][1]["aspect_ratio"] == "3:4"
    assert "Cropped to 4:5 from the model's nearest shape (900x1200)." in result
    with Image.open(io.BytesIO(base64.b64decode(shown[0]))) as picture:
        assert picture.size == (900, 1125)


@pytest.mark.parametrize(("model", "size"), [("gpt-image-1.5", "1024x1536"), ("gpt-image-2", "864x1536")])
def test_openai_fixed_size_models_get_their_nearest_size(media, monkeypatch, model, size):
    import sys

    requests = []

    def generate(**request):  # A picture of the size asked for.
        requests.append(request)
        width, _, height = request["size"].partition("x")
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(_png(int(width), int(height))).decode())])

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **kwargs: SimpleNamespace(
        images=SimpleNamespace(generate=generate))))
    media.image = f"openai/{model}"

    result = media.image_tool._generate_image("a lighthouse", size="864x1536")

    assert requests[0]["size"] == size
    assert ("Cropped to 9:16" in result) == (size != "864x1536")


def test_several_references_go_to_providers_that_take_them_and_the_rest_say_so(media, monkeypatch):
    png = base64.b64encode(_png(64, 64)).decode()
    media.image = "openrouter/google/gemini-3.1-flash-image-preview"
    media.answers[("POST", "/images")] = [_Response({"data": [{"b64_json": png}]})]

    media.image_tool._generate_image("Maya holds the cup", references=[MAYA, CUP])
    assert [ref["image_url"]["url"] for ref in media.calls[0].json["input_references"]] == [_uri(MAYA), _uri(CUP)]

    media.calls.clear()
    monkeypatch.setattr(media.image_tool, "_resolve_image_source", lambda source: FIRST)
    media.image = "dashscope/qwen-image-2.0"
    media.answers[("POST", "/multimodal-generation/generation")] = [_Response({"output": {"choices": [
        {"message": {"content": [{"image": "https://oss.example/1.png"}]}}]}})]
    media.answers[("GET", "/1.png")] = [_Response(content=_png(64, 64))]
    result = media.image_tool._edit_image("put her in the scene", references=[MAYA, CUP, LAST])
    images = [part["image"] for part in media.calls[0].json["input"]["messages"][0]["content"] if "image" in part]
    assert images == [_uri(FIRST), _uri(MAYA), _uri(CUP)]
    assert "References: Qwen (Alibaba Cloud) takes up to 2 for qwen-image-2.0, so the rest weren't used." in result

    media.calls.clear()
    media.image = "minimax/image-01"
    media.answers[("POST", "/image_generation")] = [_Response({"data": {"image_base64": [png]}, "base_resp": {}})]
    result = media.image_tool._generate_image("Maya on a beach", references=[MAYA, CUP])
    assert media.calls[0].json["subject_reference"] == [{"type": "character", "image_file": _uri(MAYA)}]
    assert "References: MiniMax takes up to 1 for image-01, so the rest weren't used." in result

    media.calls.clear()
    media.image = "together/black-forest-labs/FLUX.1-schnell"
    media.answers[("POST", "/images/generations")] = [_Response({"data": [{"b64_json": png}]})]
    result = media.image_tool._generate_image("Maya on a beach", references=[MAYA])
    assert "References: Together AI doesn't take reference pictures for black-forest-labs/FLUX.1-schnell" in result


def test_xai_makes_a_picture_from_references_through_its_edits_api(media, monkeypatch):
    from row_bot.providers import xai_media

    sent = []
    png = base64.b64encode(_png(64, 64)).decode()
    monkeypatch.setattr(xai_media, "xai_media_json_request",
                        lambda provider, method, path, *, json, timeout: sent.append((path, json)) or {
                            "data": [{"b64_json": png}]})
    media.image = "xai/grok-imagine-image"

    media.image_tool._generate_image("Maya holds the cup", references=[MAYA, CUP])

    assert sent[0][0] == "/images/edits"
    assert sent[0][1]["images"] == [{"url": _uri(MAYA)}, {"url": _uri(CUP)}] and "image" not in sent[0][1]


def test_nano_banana_gets_each_reference_as_a_part_after_the_prompt(media, monkeypatch):
    seen = []
    made = _png(64, 64)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **request: seen.append(request) or
                                                    SimpleNamespace(parts=[SimpleNamespace(
                                                        inline_data=SimpleNamespace(data=made))])))
    monkeypatch.setattr(media.image_tool, "_get_client", lambda: (client, "Google", "google"))
    media.image = "google/gemini-3.1-flash-image-preview"

    result = media.image_tool._generate_image("Maya holds the cup", size="1536x864", references=[MAYA, CUP])

    contents = seen[0]["contents"]
    assert contents[0] == "Maya holds the cup" and [part.inline_data.data for part in contents[1:]] == [MAYA, CUP]
    assert seen[0]["config"].image_config.aspect_ratio == "16:9"
    assert "Cropped to 16:9" in result
