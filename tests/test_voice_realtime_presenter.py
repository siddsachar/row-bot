from __future__ import annotations

from voice.realtime_presenter import RealtimeSpeechChunker, RealtimeSpeechQueue


def test_realtime_speech_chunker_emits_sentence_before_final_completion():
    chunker = RealtimeSpeechChunker(min_chunk_chars=20)

    assert chunker.push("I found the file you asked about. ") == ["I found the file you asked about."]
    assert chunker.push("It has three worksheets") == []


def test_realtime_speech_chunker_default_threshold_allows_earlier_useful_sentence():
    chunker = RealtimeSpeechChunker()

    assert chunker.min_chunk_chars == 32
    assert chunker.push("I found the matching file for you. ") == ["I found the matching file for you."]


def test_realtime_speech_chunker_flushes_remaining_short_answer():
    chunker = RealtimeSpeechChunker(min_chunk_chars=80)

    assert chunker.push("Done.") == []
    assert chunker.flush() == ["Done."]


def test_realtime_speech_chunker_limits_number_of_chunks():
    chunker = RealtimeSpeechChunker(max_chunks=2, min_chunk_chars=1)

    chunks = chunker.push("One. Two. Three. Four.")

    assert chunks == ["One.", "Two."]
    assert chunker.flush() == []


def test_realtime_speech_queue_speaks_multiple_chunks_when_audio_is_idle():
    queue = RealtimeSpeechQueue()

    assert queue.offer_stream_chunk("First useful sentence.") == "First useful sentence."
    assert queue.offer_stream_chunk("Second useful sentence.") == "Second useful sentence."
    assert queue.spoken_chunk_count == 2
    assert queue.spoken_stream_chars > len("First useful sentence.")


def test_realtime_speech_queue_queues_and_flushes_while_audio_active():
    queue = RealtimeSpeechQueue()

    assert queue.offer_stream_chunk("First useful sentence.") == "First useful sentence."
    assert queue.offer_stream_chunk("Second useful sentence.", playback_active=True) == ""
    assert queue.coalesced_stream_text == "Second useful sentence."

    assert queue.flush_queued(playback_active=True) == ""
    assert queue.flush_queued(playback_active=False) == "Second useful sentence."
    assert queue.coalesced_stream_text == ""


def test_realtime_speech_queue_suppresses_final_only_when_stream_coverage_is_enough():
    queue = RealtimeSpeechQueue(min_final_coverage_ratio=0.4, min_final_unspoken_chars=80)

    assert queue.offer_stream_chunk("I found it. The full answer is visible.") == "I found it. The full answer is visible."
    assert queue.should_speak_final("I found it. The full answer is visible.") is False
    assert queue.final_suppressed_after_stream is True
    assert queue.final_suppressed_reason == "stream_coverage_sufficient"


def test_realtime_speech_queue_allows_final_after_tiny_stream_prefix():
    queue = RealtimeSpeechQueue(min_final_coverage_ratio=0.55, min_final_unspoken_chars=80)

    assert queue.offer_stream_chunk("Of course, sir.") == "Of course, sir."
    final = (
        "Of course, sir. Your last two inbox emails are from Deliveroo and LinkedIn. "
        "Tomorrow you have a Bupa digital appointment at 11 AM."
    )

    decision = queue.final_decision(final)

    assert decision["speak"] is True
    assert decision["reason"] == "insufficient_stream_coverage"
    assert queue.should_speak_final(final) is True


def test_realtime_speech_queue_allows_final_when_no_stream_chunk_spoke():
    queue = RealtimeSpeechQueue()

    assert queue.should_speak_final("Done.") is True
