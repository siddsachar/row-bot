from __future__ import annotations

from row_bot.voice.speech_policy import ON_SCREEN_POINTER, speech_pieces, spoken_text


def test_a_long_answer_is_spoken_in_full():
    answer = " ".join(f"Point {index} matters." for index in range(1, 41))

    assert spoken_text(answer) == answer


def test_code_and_tables_stay_on_screen_and_the_prose_around_them_is_spoken():
    answer = (
        "Here's the function. It's on screen.\n"
        "```python\ndef add(a, b):\n    return a + b\n```\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n"
        "See [the docs](https://example.invalid/docs) for **more**."
    )

    assert spoken_text(answer) == "Here's the function. It's on screen.\nSee the docs for more."


def test_an_answer_with_nothing_to_say_aloud_points_to_the_screen():
    answer = "```python\ndef add(a, b):\n    return a + b\n```\n| a | b |\n|---|---|\nhttps://example.invalid/"

    assert spoken_text(answer) == ON_SCREEN_POINTER


def test_long_speech_is_split_between_sentences_and_keeps_every_word():
    text = "First sentence here. " * 30 + "Closing words."
    pieces = speech_pieces(text, 100)

    assert all(len(piece) <= 100 for piece in pieces)
    assert " ".join(pieces).split() == text.split()


def test_a_sentence_longer_than_a_piece_is_split_between_words():
    sentence = "word " * 60 + "supercalifragilistic."
    pieces = speech_pieces(sentence, 50)

    assert all(len(piece) <= 50 for piece in pieces)
    assert " ".join(pieces).split() == sentence.split()
