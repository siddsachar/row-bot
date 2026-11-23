"""The Live voice socket: one WebSocket on Row-Bot's own origin per session.

It carries the session's events both ways for every front, replacing one HTTP
request per event. For Gemini Live and Grok Voice it also carries the audio:
the browser sends 16 kHz microphone frames and plays 24 kHz speech, and the
host relays both to the provider over its own socket, opened with the person's
key. The page itself never connects anywhere but Row-Bot. For OpenAI Realtime
the media stays on the browser's WebRTC call (the host exchanged its SDP) and
the socket carries the events and what Row-Bot asks the front to say.

Host to browser: ``ready``, ``state`` (the orb), ``caption``, ``flush``,
``say`` (OpenAI: an answer, a status or a call's result for the browser's
call), ``event_result`` (OpenAI: an event's outcome), ``error``, and binary
speech frames (``<BI`` 1, segment, then PCM16). Browser to host: binary
microphone frames, ``event`` (OpenAI), ``mute``, ``stop_speaking``, ``end``.

A front that sees images (Gemini Live) can be shown the person's camera or
screen: ``share`` starts or stops it, and while it lasts the browser sends a
still about every two seconds (``still``, its size, then the still as the next
binary message). The host checks each one and relays at most one a second.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

OUTPUT_RATE = 24000
MAX_FRAME = 64 * 1024
_EVENT_FIELDS = ("event_id", "type", "generation_id", "response_id", "output_item_id", "item_id",
                 "call_id", "name", "arguments", "text", "origin")
_SPEAKING_ENDS = {"response_done", "response_cancelled", "output_audio_done", "barge_in_cancelled"}
_WORKING = {"running", "stopping", "waiting_approval"}
#: A front is shown at most one still a second (Gemini Live's video rate).
STILL_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class LiveOutput:
    id: str
    kind: str  # "function_output", "status" or "final"
    text: str
    call_id: str = ""
    silent: bool = False


async def connect_socket(url: str, *, headers: dict[str, str]) -> Any:
    """The provider's WebSocket, from the host (the key goes in its headers, never in a page)."""
    from websockets.asyncio.client import connect

    return await connect(url, additional_headers=headers, max_size=4 * 1024 * 1024, open_timeout=10)


async def run_live_socket(transport: Any, redeemed: Any, *, link: Any, receive: Callable[[], Awaitable[Any]],
                          validate: Callable[[], None], revalidate: Callable[[], Awaitable[Any]],
                          connect: Callable[..., Awaitable[Any]] = connect_socket) -> None:
    """Drive one Live voice socket until it ends: the session's events, the relay, Row-Bot's answers."""
    from row_bot.voice.client_realtime import RELAYED, RealtimeEvent, _provider_error
    from row_bot.voice.engine.stills import SHARE_SOURCES, checked_still
    from row_bot.voice.live_front import RUN_ANSWER, RUN_STATUS, live_outputs

    owner, handle = redeemed.owner, redeemed.handle
    loop = asyncio.get_running_loop()
    ended = asyncio.Event()
    kind, provider, instructions = transport.attach(redeemed, lambda: loop.call_soon_threadsafe(ended.set))
    relay: Any = None
    stop_requested = False
    flags = {"muted": False, "speaking": False, "working": False, "orb": ""}
    # Sharing the camera or screen with a front that sees images (independent of mute).
    share: dict[str, Any] = {"source": None, "expected": 0, "sent_at": None}
    answered: dict[str, str] = {}
    said: set[str] = set()
    finished: set[str] = set()

    async def set_orb() -> None:
        orb = ("speaking" if flags["speaking"] else "muted" if flags["muted"]
               else "thinking" if flags["working"] else "listening")
        if orb != flags["orb"]:
            flags["orb"] = orb
            await link.send_event({"type": "state", "state": orb, "muted": flags["muted"]})

    async def handle_event(fields: dict) -> Any:
        if "generation_id" not in fields:
            fields = {**fields, "generation_id": await asyncio.to_thread(transport.generation, owner, handle)}
        event = RealtimeEvent(**{"event_id": str(uuid4()), **fields})
        result = await asyncio.to_thread(
            lambda: asyncio.run(transport.event(owner, handle, event, validate=validate)))
        if event.type == "output_started":
            flags["speaking"] = True
        elif event.type in _SPEAKING_ENDS:
            flags["speaking"] = False
        if result.accepted and result.caption and event.type in {"transcript_final", "assistant_transcript_final"}:
            role = "user" if event.type == "transcript_final" else "assistant"
            await link.send_event({"type": "caption", "role": role, "text": result.caption})
        if result.snapshot.run_id and event.type == "function_call_ready":
            flags["working"] = True
        if relay is not None and result.function_output and result.call_id:
            await relay.send_output(LiveOutput("", "function_output", result.function_output,
                                               result.call_id, result.silent))
        await set_orb()
        return result

    async def say(output: dict) -> None:
        if relay is not None:
            asked = await relay.send_output(LiveOutput(output["id"], output["kind"], output["text"],
                                                       output.get("call_id", "")))
            if asked:
                await handle_event({"type": "provider_response_create_sent", "origin": "row_bot"})
            return
        text = output["text"] if output["kind"] == "function_output" else (
            (RUN_ANSWER if output["kind"] == "final" else RUN_STATUS) + output["text"])
        await link.send_event({"type": "say", "kind": output["kind"], "call_id": output.get("call_id", ""),
                               "text": text})

    async def poll() -> None:
        # Row-Bot's run, read on the host: its progress cues, then the consult's answer.
        while not ended.is_set():
            await asyncio.sleep(transport.poll_seconds)
            run_id = await asyncio.to_thread(transport.generation, owner, handle)
            if not run_id or run_id in finished or transport.run_view is None:
                continue
            try:
                view = await asyncio.to_thread(transport.run_view, owner, handle, validate)
            except Exception as exc:
                if getattr(exc, "code", "") == "voice_run_changed":
                    continue
                raise
            flags["working"] = view.get("state") in _WORKING and not view.get("function_output")
            for output in live_outputs(view, answered):
                if output["id"] in said:
                    continue
                said.add(output["id"])
                if output["kind"] == "function_output":
                    answered.setdefault(output["call_id"], output["id"])
                await say(output)
            if view.get("state") not in _WORKING and (view.get("function_output") or view.get("text")):
                finished.add(run_id)
            await set_orb()

    async def pump() -> None:
        nonlocal stop_requested
        while not ended.is_set():
            message = await receive()
            if message is None:
                ended.set()
                return
            if isinstance(message, bytes):
                expected, share["expected"] = share["expected"], 0
                if expected:
                    await show(message, expected)
                elif relay is not None and not flags["muted"] and 0 < len(message) <= MAX_FRAME and len(message) % 2 == 0:
                    await relay.send_audio(message)
                continue
            kind_of = message.get("type")
            if kind_of == "event" and relay is None and isinstance(message.get("event"), dict):
                fields = {key: message["event"][key] for key in _EVENT_FIELDS if key in message["event"]}
                if not isinstance(fields.get("event_id"), str) or not isinstance(fields.get("type"), str):
                    raise ValueError("invalid_voice_event")
                result = await handle_event(fields)
                await link.send_event({"type": "event_result", "event_id": result.event_id,
                                       "accepted": result.accepted, "call_id": result.call_id,
                                       "function_output": result.function_output, "silent": result.silent,
                                       "run_id": result.snapshot.run_id or "", "state": result.snapshot.state})
                if result.snapshot.state in {"stopped", "stopping"}:
                    ended.set()
            elif kind_of == "mute":
                flags["muted"] = bool(message.get("muted"))
                await set_orb()
            elif kind_of == "share":
                source = message.get("source")
                wanted = source if source in SHARE_SOURCES and sees else None
                if source and wanted is None:
                    await link.send_event({"type": "error", "code": "voice_share_unavailable"})
                if wanted != share["source"]:
                    share["source"] = wanted
                    await link.send_event({"type": "sharing", "source": wanted})
            elif kind_of == "still":
                size = message.get("bytes")
                if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                    raise ValueError("voice_still_invalid")
                share["expected"] = size
            elif kind_of == "stop_speaking":
                if relay is not None and await relay.stop_speaking():
                    await link.send_event({"type": "flush"})
            elif kind_of == "end":
                stop_requested = True
                ended.set()
                return

    async def show(data: bytes, expected: int) -> None:
        """A still while sharing, checked, relayed at most once a second; anything else is dropped."""
        if share["source"] is None:
            return
        try:
            if len(data) != expected:
                raise ValueError("voice_still_invalid")
            still = checked_still(data, share["source"])
        except ValueError:
            await link.send_event({"type": "error", "code": "voice_still_refused"})
            return
        now = loop.time()
        if share["sent_at"] is not None and now - share["sent_at"] < STILL_INTERVAL_SECONDS:
            return
        share["sent_at"] = now
        await relay.send_image(still)

    async def relayed() -> None:
        async for action in relay.actions():
            if action[0] == "event":
                await handle_event(action[1])
            elif action[0] == "audio":
                await link.send_audio(action[1], action[2])
            elif action[0] == "flush":
                flags["speaking"] = False
                await link.send_event({"type": "flush"})
        # The provider closed its socket.
        await link.send_event({"type": "error", "code": "realtime_disconnected"})
        ended.set()

    async def tick() -> None:
        while not ended.is_set():
            try:
                await asyncio.wait_for(ended.wait(), timeout=transport.tick_seconds)
                return
            except TimeoutError:
                pass
            await revalidate()
            await asyncio.to_thread(transport.keepalive, owner, handle)

    workers: list[asyncio.Task] = []
    waiter: asyncio.Task | None = None
    sees = False
    try:
        if kind in RELAYED:
            try:
                relay = await provider.open_relay(instructions, connect=connect)
            except Exception as exc:
                code = _provider_error(exc).code
                logger.warning("Live voice could not reach %s: %s", kind, code)
                await link.send_event({"type": "error", "code": code})
                return
            await handle_event({"type": "connected"})
            sees = callable(getattr(relay, "send_image", None))
        await link.send_event({"type": "ready", "transport": kind, "output_sample_rate": OUTPUT_RATE,
                               "sees_images": sees})
        await set_orb()
        workers = [asyncio.create_task(pump(), name="live-voice-pump"),
                   asyncio.create_task(tick(), name="live-voice-tick"),
                   asyncio.create_task(poll(), name="live-voice-poll")]
        if relay is not None:
            workers.append(asyncio.create_task(relayed(), name="live-voice-relay"))
        waiter = asyncio.create_task(ended.wait())
        done, _ = await asyncio.wait([waiter, *workers], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not waiter and not task.cancelled() and task.exception() is not None:
                raise task.exception()
    finally:
        for task in workers:
            task.cancel()
        if waiter is not None:
            waiter.cancel()
        await asyncio.gather(*workers, *([waiter] if waiter is not None else []), return_exceptions=True)
        if relay is not None:
            try:
                await relay.close()
            except Exception:
                logger.debug("Live voice relay close failed", exc_info=True)
        transport.detach(redeemed, stop=stop_requested)
