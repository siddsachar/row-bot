from pathlib import Path


def test_attach_thinking_to_message_preserves_nonempty_reasoning():
    from ui.helpers import attach_thinking_to_message

    msg = {"role": "assistant", "content": "final answer"}

    result = attach_thinking_to_message(msg, "  reasoning tokens  ")

    assert result is msg
    assert msg["thinking"] == "reasoning tokens"


def test_attach_thinking_to_message_skips_empty_reasoning():
    from ui.helpers import attach_thinking_to_message

    msg = {"role": "assistant", "content": "final answer"}

    attach_thinking_to_message(msg, "   ")

    assert "thinking" not in msg


def test_streaming_final_message_persists_thinking_text():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "streaming.py").read_text(encoding="utf-8")

    assert "attach_thinking_to_message(a_msg, gen.thinking_text)" in source
    assert "state.messages.append(a_msg)" in source


def test_streaming_treats_reasoning_only_as_final_output():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "streaming.py").read_text(encoding="utf-8")
    output_block = source.split("_has_final_output = bool(", 1)[1].split(")", 1)[0]

    assert "gen.thinking_text" in output_block


def test_chat_reattach_preserves_thinking_text():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "chat.py").read_text(encoding="utf-8")

    assert "attach_thinking_to_message(a_msg, _reattach_gen.thinking_text)" in source
    assert "if _reattach_gen.accumulated or _reattach_gen.thinking_text:" in source
    assert "_reattach_gen.thinking_collapsed = True" in source
    assert '"\\U0001f4ad Thinking", icon="psychology"' in source


def test_render_path_still_displays_persisted_thinking():
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "render.py").read_text(encoding="utf-8")

    assert 'thinking = msg.get("thinking")' in source
    assert '"\\U0001f4ad Thinking", icon="psychology"' in source
