import asyncio
from pathlib import Path
import queue


def test_stream_graph_preserves_text_on_tool_call_chunks():
    from row_bot.agent import _stream_graph

    class AIMessageChunk:
        def __init__(self, content="", *, tool_calls=None, tool_call_chunks=None):
            self.content = content
            self.response_metadata = {}
            self.additional_kwargs = {}
            self.tool_calls = tool_calls or []
            self.tool_call_chunks = tool_call_chunks or []

    class FakeToolCallMessage:
        type = "ai"
        content = ""
        tool_calls = [{"id": "call_search", "name": "web_search", "args": {"query": "test"}}]

    class FakeToolMessage:
        type = "tool"
        name = "web_search"
        content = "result"

    class FakeState:
        next = None
        tasks = []
        values = {"messages": []}

    class FakeAgent:
        def stream(self, input_data, config, stream_mode):
            yield ("messages", (AIMessageChunk("I'll"), {"langgraph_node": "agent"}))
            yield (
                "messages",
                (
                    AIMessageChunk(
                        " check that now.",
                        tool_call_chunks=[{"name": "web_search", "args": "{}"}],
                    ),
                    {"langgraph_node": "agent"},
                ),
            )
            yield ("updates", {"agent": {"messages": [FakeToolCallMessage()]}})
            yield ("updates", {"tools": {"messages": [FakeToolMessage()]}})
            yield ("messages", (AIMessageChunk(" The result is ready."), {"langgraph_node": "agent"}))
            yield ("updates", {})

        def get_state(self, config):
            return FakeState()

    events = list(_stream_graph(FakeAgent(), {}, {"configurable": {"thread_id": "test-channel-stream"}}))
    token_text = "".join(payload for event_type, payload in events if event_type == "token")
    done_text = [payload for event_type, payload in events if event_type == "done"][-1]
    tool_payload = [payload for event_type, payload in events if event_type == "tool_call"][0]

    assert "I'll check that now." in token_text
    assert "I'll check that now." in done_text
    assert "The result is ready." in done_text
    assert "Web Search" in str(tool_payload)
    assert tool_payload.get("raw_name") == "web_search"
    assert tool_payload.get("id") == "call_search"


def test_telegram_stream_consumer_returns_none_when_final_edit_fails():
    from row_bot.channels.telegram import _tg_edit_consumer

    class FakeSentMessage:
        def __init__(self):
            self.edits = []

        async def edit_text(self, text, **kwargs):
            self.edits.append(text)
            if "complete" in text:
                raise RuntimeError("simulated final edit failure")

    async def run_case():
        events = queue.Queue()
        events.put(("token", "first"))
        events.put(("token", " complete"))
        events.put(None)
        sent = FakeSentMessage()
        result = await _tg_edit_consumer(None, sent, events, asyncio.get_running_loop())
        return result, sent.edits

    result, edits = asyncio.run(run_case())

    assert result is None
    assert edits[0] == "first"
    assert any("complete" in edit for edit in edits)


def test_channel_final_answer_prefers_model_text_over_tool_reports():
    from row_bot.channels.agent_output import assemble_agent_answer

    answer = assemble_agent_answer("Here is the answer.", ["Using Search...", "Search done"])

    assert answer == "Here is the answer."


def test_channel_final_answer_falls_back_to_tool_reports_without_model_text():
    from row_bot.channels.agent_output import assemble_agent_answer

    answer = assemble_agent_answer("  ", ["Using Search...", "Search done"])

    assert answer == "Using Search...\nSearch done"


def test_channel_runtime_marks_stream_capable_adapters_and_leaves_sms_nonstreaming():
    streaming_paths = [
        "src/row_bot/channels/telegram.py",
        "src/row_bot/channels/slack.py",
        "src/row_bot/channels/discord_channel.py",
        "src/row_bot/channels/whatsapp.py",
    ]
    for path in streaming_paths:
        source = Path(path).read_text(encoding="utf-8")
        assert '"channel_streaming": purpose != "approval"' in source
        assert "assemble_agent_answer" in source

    sms_source = Path("src/row_bot/channels/sms.py").read_text(encoding="utf-8")
    assert '"channel_streaming": False' in sms_source
    assert "assemble_agent_answer" not in sms_source
