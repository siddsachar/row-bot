from __future__ import annotations

from pathlib import Path

from row_bot.ui.streaming import LiveMarkdownBatcher


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _Markdown:
    def __init__(self) -> None:
        self.contents: list[str] = []

    def set_content(self, value: str) -> None:
        self.contents.append(value)


def test_live_markdown_batcher_renders_first_token_promptly():
    clock = _Clock()
    markdown = _Markdown()
    batcher = LiveMarkdownBatcher(markdown.set_content, now=clock)

    assert batcher.update("h") is True

    assert markdown.contents == ["h"]
    assert batcher.flush_count == 1


def test_live_markdown_batcher_batches_many_small_answer_updates():
    clock = _Clock()
    markdown = _Markdown()
    batcher = LiveMarkdownBatcher(
        markdown.set_content,
        now=clock,
        min_interval_seconds=999.0,
        max_interval_seconds=999.0,
        min_chars=64,
    )

    for index in range(1_000):
        batcher.update("x" * (index + 1))
    batcher.update("x" * 1_000, force=True)

    assert markdown.contents[-1] == "x" * 1_000
    assert len(markdown.contents) < 30


def test_live_markdown_batcher_forces_final_render_below_threshold():
    clock = _Clock()
    markdown = _Markdown()
    batcher = LiveMarkdownBatcher(markdown.set_content, now=clock, min_chars=64)

    batcher.update("a")
    batcher.update("ab")

    assert markdown.contents == ["a"]

    assert batcher.flush(force=True) is True

    assert markdown.contents[-1] == "ab"


def test_live_markdown_batcher_flushes_after_time_window():
    clock = _Clock()
    markdown = _Markdown()
    batcher = LiveMarkdownBatcher(markdown.set_content, now=clock, min_interval_seconds=0.075)

    batcher.update("a")
    clock.advance(0.08)

    assert batcher.update("ab") is True
    assert markdown.contents[-1] == "ab"


def test_streaming_consumer_uses_batched_metrics_and_boundary_flushes():
    source = Path("src/row_bot/ui/streaming.py").read_text(encoding="utf-8")
    state_source = Path("src/row_bot/ui/state.py").read_text(encoding="utf-8")

    assert "LiveMarkdownBatcher" in source
    assert "stream_updates=_stream_updates" in source
    assert "answer_stream_updates=_answer_stream_updates" in source
    assert "thinking_stream_updates=_thinking_stream_updates" in source
    assert "ui_flushes=answer_batcher.flush_count" in source
    assert "thinking_ui_flushes=thinking_batcher.flush_count" in source
    assert 'event_type in {"error", "tool_call", "tool_done", "summarizing", "interrupt", "done"}' in source
    assert "generation.lifecycle" in source
    assert "producer_wait_ms" in source
    assert "queue_empty_wait_ms" in source
    assert "first_answer_token_ms" in source
    assert "low_ui_flush_count=answer_batcher.flush_count <= 4" in source
    assert "generation_id: str" in state_source
    assert "created_at: float" in state_source
    assert "producer_thread_started_at: float" in state_source
    assert "finalization_ms: float" in state_source
