from __future__ import annotations

from row_bot.voice.speech_policy import make_speakable_response, user_requested_read_aloud


def test_speakable_response_truncates_long_answers():
    response = make_speakable_response(
        "One. Two. Three. Four. Five.",
        max_sentences=3,
    )

    assert response.truncated is True
    assert response.text == "One. Two. Three. The full response is shown in the app."


def test_speakable_response_uses_fallback_for_code_heavy_answers():
    response = make_speakable_response(
        "```python\n"
        "def a():\n"
        "    return {'x': 1};\n"
        "class B: pass\n"
        "function c() { return 2; }\n"
        "```"
    )

    assert response.fallback is True
    assert response.text == "I've provided the response in the app."


def test_speakable_response_allows_explicit_read_aloud():
    response = make_speakable_response(
        "One. Two. Three. Four.",
        max_sentences=2,
        allow_long=True,
    )

    assert response.truncated is False
    assert response.text == "One. Two. Three. Four."


def test_user_requested_read_aloud_markers():
    assert user_requested_read_aloud("Can you read this aloud for me?")
    assert user_requested_read_aloud("Please say this exactly.")
    assert not user_requested_read_aloud("Summarize this.")
