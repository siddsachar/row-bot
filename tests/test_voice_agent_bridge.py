from __future__ import annotations

import asyncio
import threading

from row_bot.voice.agent_bridge import (
    REALTIME_ALLOWED_BRIDGE_TOOLS,
    REALTIME_ALLOWED_TOOLS,
    REALTIME_DIRECT_TOOL_POLICY,
    REALTIME_WAIT_TOOL,
    VOICE_BRAIN_STRATEGY,
    VoiceAgentBridge,
    realtime_bridge_tool_declarations,
)


class FakeGeneration:
    def __init__(self) -> None:
        self.status = "streaming"
        self.pending_tools = {"call_1": {"name": "browser_open"}}
        self.interrupt_data = {"approval": "needed"}
        self.stop_event = threading.Event()
        self.voice_control_queue = []
        self.realtime_forced_consult = False
        self.realtime_consult_request = ""
        self.realtime_tool_call_id = ""
        self.realtime_tool_name = ""


def test_voice_agent_bridge_submits_transcript_as_voice_message():
    calls = []

    async def send_message(text: str, *, voice_mode: bool = False) -> None:
        calls.append((text, voice_mode))

    bridge = VoiceAgentBridge(send_message=send_message, active_generation=lambda: None)

    asyncio.run(bridge.submit_user_transcript("hello"))

    assert calls == [("hello", True)]


def test_voice_agent_bridge_submits_transcript_to_plain_sender_without_voice_keyword():
    calls = []

    async def send_message(text: str) -> None:
        calls.append(text)

    bridge = VoiceAgentBridge(
        send_message=send_message,
        active_generation=lambda: None,
        surface="designer",
        thread_id="thread-1",
    )

    asyncio.run(bridge.submit_user_transcript("hello"))

    assert calls == ["hello"]


def test_voice_agent_bridge_reports_active_tool_and_approval_state():
    gen = FakeGeneration()
    bridge = VoiceAgentBridge(send_message=lambda *args, **kwargs: None, active_generation=lambda: gen)

    status = bridge.active_run_status()

    assert status["active"] is True
    assert status["status"] == "streaming"
    assert status["tools"] == ["browser_open"]
    assert status["approval_needed"] is True
    assert status["cancel_available"] is True
    assert status["steer_available"] is True
    assert status["follow_up_available"] is True


def test_voice_agent_bridge_can_cancel_active_run():
    gen = FakeGeneration()
    bridge = VoiceAgentBridge(send_message=lambda *args, **kwargs: None, active_generation=lambda: gen)

    assert bridge.cancel_active_run() is True
    assert gen.stop_event.is_set() is True


def test_voice_agent_bridge_idle_status_and_cancel():
    bridge = VoiceAgentBridge(send_message=lambda *args, **kwargs: None, active_generation=lambda: None)

    assert bridge.active_run_status() == {
        "active": False,
        "status": "idle",
        "tools": [],
        "approval_needed": False,
        "cancel_available": False,
        "steer_available": False,
        "follow_up_available": False,
        "queued_controls": [],
    }
    assert bridge.cancel_active_run() is False


def test_voice_agent_bridge_policy_is_consult_control_only():
    assert VOICE_BRAIN_STRATEGY == "row-bot-consult"
    assert REALTIME_DIRECT_TOOL_POLICY == "blocked"
    assert REALTIME_ALLOWED_BRIDGE_TOOLS == ("row_bot_agent_consult", "row_bot_agent_control")
    assert REALTIME_ALLOWED_TOOLS == ("row_bot_agent_consult", "row_bot_agent_control", REALTIME_WAIT_TOOL)
    assert [tool["name"] for tool in realtime_bridge_tool_declarations()] == list(REALTIME_ALLOWED_TOOLS)


def test_voice_agent_bridge_controls_active_run_status_cancel_and_queue():
    gen = FakeGeneration()
    bridge = VoiceAgentBridge(send_message=lambda *args, **kwargs: None, active_generation=lambda: gen)

    status = bridge.control_active_run("what are you doing")
    assert status["handled"] is True
    assert status["control"] == "status"
    assert "working" in status["speakable"].lower() or "approval" in status["speakable"].lower()

    queued = bridge.control_active_run("also check the logs")
    assert queued["handled"] is True
    assert queued["control"] == "steer"
    assert queued["status"] == "queued"
    assert gen.voice_control_queue == [{"kind": "steer", "text": "also check the logs"}]

    cancelled = bridge.control_active_run("cancel")
    assert cancelled["handled"] is True
    assert cancelled["control"] == "cancel"
    assert gen.stop_event.is_set() is True


def test_voice_agent_bridge_handles_realtime_consult_tool_call():
    calls = []
    queued = []

    async def send_message(text: str, *, voice_mode: bool = False) -> None:
        calls.append((text, voice_mode))

    bridge = VoiceAgentBridge(send_message=send_message, active_generation=lambda: None)

    result = asyncio.run(bridge.handle_realtime_function_call(
        name="row_bot_agent_consult",
        call_id="call_1",
        arguments='{"request":"check the files"}',
        queue_consult=queued.append,
    ))

    assert result["handled"] is True
    assert result["deferred"] is True
    assert calls == [("check the files", True)]
    assert queued == [{"call_id": "call_1", "name": "row_bot_agent_consult", "request": "check the files"}]


def test_voice_agent_bridge_handles_realtime_control_tool_call():
    gen = FakeGeneration()
    bridge = VoiceAgentBridge(send_message=lambda *args, **kwargs: None, active_generation=lambda: gen)

    result = asyncio.run(bridge.handle_realtime_function_call(
        name="row_bot_agent_control",
        call_id="call_2",
        arguments={"action": "status"},
    ))

    assert result["handled"] is True
    assert result["deferred"] is False
    assert "Row-Bot is" in result["output"]


def test_voice_agent_bridge_blocks_direct_realtime_tools():
    bridge = VoiceAgentBridge(send_message=lambda *args, **kwargs: None, active_generation=lambda: None)

    result = asyncio.run(bridge.handle_realtime_function_call(
        name="browser_open",
        call_id="call_3",
        arguments={"url": "https://example.com"},
    ))

    assert result["handled"] is True
    assert result["deferred"] is False
    assert "blocked" in result["output"]


def test_voice_agent_bridge_handles_wait_for_user_silently():
    calls = []
    bridge = VoiceAgentBridge(send_message=lambda *args, **kwargs: calls.append(args), active_generation=lambda: None)

    result = asyncio.run(bridge.handle_realtime_function_call(
        name=REALTIME_WAIT_TOOL,
        call_id="call_wait",
        arguments={},
    ))

    assert result["handled"] is True
    assert result["deferred"] is False
    assert result["silent"] is True
    assert "waiting" in result["output"]
    assert calls == []


def test_voice_agent_bridge_drops_empty_realtime_consult_silently():
    calls = []
    bridge = VoiceAgentBridge(send_message=lambda *args, **kwargs: calls.append(args), active_generation=lambda: None)

    result = asyncio.run(bridge.handle_realtime_function_call(
        name="row_bot_agent_consult",
        call_id="call_empty",
        arguments={"request": ""},
    ))

    assert result["handled"] is True
    assert result["deferred"] is False
    assert result["silent"] is True
    assert "ignored_empty_request" in result["output"]
    assert calls == []


def test_voice_agent_bridge_forced_consult_fallback_filters_substantive_turns():
    calls = []
    queued = []

    async def send_message(text: str, *, voice_mode: bool = False) -> None:
        calls.append((text, voice_mode))

    bridge = VoiceAgentBridge(send_message=send_message, active_generation=lambda: None)

    skipped = asyncio.run(bridge.force_consult_if_substantive("thanks", queue_consult=queued.append))
    handled = asyncio.run(bridge.force_consult_if_substantive("open the browser and check the docs", queue_consult=queued.append))

    assert skipped == {"handled": False, "status": "skipped"}
    assert handled == {"handled": True, "status": "submitted"}
    assert calls == [("open the browser and check the docs", True)]
    assert queued == [{"call_id": "", "name": "forced_consult", "request": "open the browser and check the docs"}]


def test_voice_agent_bridge_dedupes_repeated_forced_consult():
    calls = []
    queued = []
    gen = FakeGeneration()
    gen.realtime_forced_consult = True
    gen.realtime_consult_request = "Check my calendar tomorrow"

    async def send_message(text: str, *, voice_mode: bool = False) -> None:
        calls.append((text, voice_mode))

    bridge = VoiceAgentBridge(send_message=send_message, active_generation=lambda: gen)

    result = asyncio.run(bridge.force_consult_if_substantive("check my calendar tomorrow", queue_consult=queued.append))

    assert result == {"handled": True, "status": "deduped"}
    assert calls == []
    assert queued == []


def test_voice_agent_bridge_adopts_late_realtime_consult_call_id():
    calls = []
    queued = []
    gen = FakeGeneration()
    gen.realtime_forced_consult = True
    gen.realtime_consult_request = "Check my calendar tomorrow"

    async def send_message(text: str, *, voice_mode: bool = False) -> None:
        calls.append((text, voice_mode))

    bridge = VoiceAgentBridge(send_message=send_message, active_generation=lambda: gen)

    result = asyncio.run(bridge.handle_realtime_function_call(
        name="row_bot_agent_consult",
        call_id="call_late",
        arguments={"request": "check my calendar tomorrow"},
        queue_consult=queued.append,
    ))

    assert result["handled"] is True
    assert result["deferred"] is True
    assert calls == []
    assert queued == []
    assert gen.realtime_tool_call_id == "call_late"
    assert gen.realtime_tool_name == "row_bot_agent_consult"
    assert gen.realtime_forced_consult is False
