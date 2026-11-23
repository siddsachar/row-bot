"""Sharing the camera or screen during Row-Bot Voice, with a fake browser.

The browser sends a still only when the host asks at the end of a spoken
turn; the still goes with that turn and no other. Nothing opens a camera,
loads a model or touches the network.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import io

import pytest
from PIL import Image

from row_bot.voice.engine.session import DuplexSession, SessionSettings
from row_bot.voice.engine.stills import MAX_STILL_BYTES, MAX_STILL_SIDE, checked_still
from tests.helpers.voice_fakes import (
    FRAME_MS, SILENT, VOICED, FakeClock, FakeHost, FakeLink, FakeSpeechToText, fake_engine_parts,
)

pytestmark = pytest.mark.subsystem


def picture(width: int = 64, height: int = 48, kind: str = "JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (40, 120, 200)).save(out, kind)
    return out.getvalue()


class Browser(FakeLink):
    """The page's end of the socket: answers each "look" with *still* (None: it never answers)."""

    def __init__(self) -> None:
        super().__init__()
        self.still: bytes | None = picture()
        self.answers: list[asyncio.Task] = []

    async def send_event(self, event: dict) -> None:
        await super().send_event(event)
        if event["type"] == "look" and self.still is not None:
            self.answers.append(asyncio.get_running_loop().create_task(self.send_still(self.still)))

    async def send_still(self, data: bytes) -> None:
        await self.session.control({"type": "still", "bytes": len(data)})
        await self.session.binary(data)


class Rig:
    def __init__(self, *finals: str, sees: bool = True, idle_pause_ms: int = 5 * 60 * 1000) -> None:
        self.clock = FakeClock()
        self.stt = FakeSpeechToText(self.clock, *finals)
        self.link = Browser()
        self.host = FakeHost(sees=sees)
        self.session = DuplexSession(fake_engine_parts(self.clock, self.stt), self.link, self.host,
                                     settings=SessionSettings(idle_pause_ms=idle_pause_ms), clock=self.clock)
        self.link.session = self.session

    def unanswered(self) -> None:
        """The page stops answering looks; a turn waits only briefly for one."""
        self.link.still = None
        self.session.settings = replace(self.session.settings, still_wait_seconds=0.01)

    async def say(self, voiced_ms: int = 600, quiet_ms: int = 400) -> None:
        for voiced, ms in ((True, voiced_ms), (False, quiet_ms)):
            for _ in range(ms // FRAME_MS):
                self.clock.advance(FRAME_MS)
                await self.session.binary(VOICED if voiced else SILENT)
                await asyncio.sleep(0)
        await self.session.idle()

    def sharing(self) -> list:
        return [event["source"] for event in self.link.of("sharing")]

    def refused(self) -> int:
        return sum(1 for event in self.link.of("error") if event["code"] == "voice_still_refused")


def run(coroutine):
    return asyncio.run(coroutine)


def test_a_still_shown_while_sharing_goes_with_the_next_spoken_turn_and_only_that_turn():
    async def scenario():
        rig = Rig("What is this?", "And now?", "Thanks")
        await rig.session.start()
        assert rig.link.of("ready")[0]["sees_images"] is True
        await rig.session.control({"type": "share", "source": "camera"})
        assert rig.sharing() == ["camera"]
        await rig.say()
        # The turn's end asked the page for one fresh still, and the turn carried it.
        assert len(rig.link.of("look")) == 1
        first = rig.host.stills[0]
        assert first.source == "camera" and first.mime_type == "image/jpeg" and first.name == "Live camera.jpg"
        assert first.data == rig.link.still
        # The page does not answer the next look: that turn carries nothing (never the old still).
        rig.unanswered()
        await rig.say()
        assert rig.host.stills[1] is None
        # A screen still is named as the screen.
        rig.link.still = picture(kind="PNG")
        rig.session.settings = replace(rig.session.settings, still_wait_seconds=1.5)
        await rig.session.control({"type": "share", "source": "screen"})
        await rig.say()
        assert (rig.host.stills[2].source, rig.host.stills[2].name) == ("screen", "Shared screen.png")
        await rig.session.close()
    run(scenario())


def test_nothing_is_asked_for_or_sent_while_not_sharing():
    async def scenario():
        rig = Rig("Hello")
        await rig.session.start()
        await rig.link.send_still(picture())  # Sent unasked, with nothing shared: dropped.
        await rig.say()
        assert rig.link.of("look") == [] and rig.host.stills == [None]
        await rig.session.close()
    run(scenario())


def test_a_model_that_cannot_see_images_is_sent_no_still():
    async def scenario():
        rig = Rig("What is this?", sees=False)
        await rig.session.start()
        assert rig.link.of("ready")[0]["sees_images"] is False
        await rig.session.control({"type": "share", "source": "camera"})
        assert [event["code"] for event in rig.link.of("error")] == ["voice_share_unavailable"]
        assert rig.sharing() == [] and rig.session.sharing is None
        await rig.link.send_still(picture())
        await rig.say()
        assert rig.link.of("look") == [] and rig.host.stills == [None]
        await rig.session.close()
    run(scenario())


def test_stopping_sharing_drops_the_held_still():
    async def scenario():
        rig = Rig("What is this?")
        await rig.session.start()
        await rig.session.control({"type": "share", "source": "camera"})
        await rig.link.send_still(picture())
        await rig.session.control({"type": "share", "source": None})
        assert rig.sharing() == ["camera", None]
        await rig.say()
        assert rig.link.of("look") == [] and rig.host.stills == [None]
        await rig.session.close()
    run(scenario())


def test_sharing_is_independent_of_mute_but_a_pause_or_the_end_of_voice_stops_it():
    async def scenario():
        rig = Rig(idle_pause_ms=1000)
        await rig.session.start()
        await rig.session.control({"type": "share", "source": "screen"})
        await rig.session.control({"type": "mute", "muted": True})
        assert rig.session.sharing == "screen"
        await rig.session.control({"type": "mute", "muted": False})
        await rig.say(voiced_ms=0, quiet_ms=1200)  # A quiet second pauses the session.
        assert rig.link.states()[-1] == "paused"
        assert rig.sharing() == ["screen", None]
        # Paused, nothing can start sharing.
        await rig.session.control({"type": "share", "source": "camera"})
        assert rig.session.sharing is None
        await rig.session.control({"type": "resume"})
        await rig.session.control({"type": "share", "source": "camera"})
        await rig.link.send_still(picture())
        await rig.session.close()
        assert rig.session.sharing is None
    run(scenario())


@pytest.mark.parametrize("data", [
    picture(MAX_STILL_SIDE + 1, 400),           # larger than the browser ever sends
    picture(kind="GIF"),                        # not JPEG or PNG
    picture(kind="WEBP"),
    b"\xff\xd8\xff" + b"\0" * 64,               # says JPEG, isn't one
    b"\xff\xd8\xff" + b"\0" * MAX_STILL_BYTES,  # too many bytes
], ids=["too-wide", "gif", "webp", "not-a-picture", "too-large"])
def test_the_host_refuses_a_still_outside_the_limits(data):
    with pytest.raises(ValueError):
        checked_still(data, "camera")

    async def scenario():
        rig = Rig("What is this?")
        await rig.session.start()
        await rig.session.control({"type": "share", "source": "camera"})
        rig.link.still = data
        await rig.say()
        assert rig.refused() == 1 and rig.host.stills == [None]
        await rig.session.close()
    run(scenario())


def test_a_still_must_be_exactly_what_its_announcement_said():
    async def scenario():
        rig = Rig("What is this?")
        await rig.session.start()
        await rig.session.control({"type": "share", "source": "camera"})
        data = picture()
        await rig.session.control({"type": "still", "bytes": len(data) + 1})
        await rig.session.binary(data)
        assert rig.refused() == 1
        rig.unanswered()
        await rig.say()  # Audio after the still is audio again, and the refused still went nowhere.
        assert rig.host.turns == ["What is this?"] and rig.host.stills == [None]
        # An announcement that names no size ends the socket.
        with pytest.raises(ValueError):
            await rig.session.control({"type": "still", "bytes": "many"})
        await rig.session.close()
    run(scenario())


def test_the_live_still_mark_never_reaches_a_model_request():
    """``voice_still`` is metadata for Learning: no provider's request carries it."""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_ollama import ChatOllama
    from langchain_openai import ChatOpenAI

    message = HumanMessage(content="What is this?", id="turn-1", additional_kwargs={
        "platform_public_content": "What is this?", "voice_still": "camera",
        "platform_attachments": [{"name": "Live camera.jpg", "mime_type": "image/jpeg"}]})
    requests = {
        "openai": ChatOpenAI(model="synthetic", api_key="synthetic", base_url="http://127.0.0.1:9")
        ._get_request_payload([message]),
        "anthropic": ChatAnthropic(model="synthetic", api_key="synthetic")._get_request_payload([message]),
        "ollama": ChatOllama(model="synthetic")._chat_params([message]),
        "google": ChatGoogleGenerativeAI(model="synthetic", google_api_key="synthetic")._prepare_request([message]),
    }
    for provider, request in requests.items():
        sent = str(request)
        assert "What is this?" in sent, provider
        assert "voice_still" not in sent and "Live camera.jpg" not in sent, provider
