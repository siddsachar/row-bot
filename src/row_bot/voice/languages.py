"""Languages for Row-Bot Voice (V8): the language setting, what each engine understands or speaks, and a
cheap guess at the language of a piece of text.

Nothing here loads a model or imports a speech library, so Settings and the
voice paths can read it at no cost. Codes are Whisper's (ISO 639-1).
"""
from __future__ import annotations

import re
import threading
from collections import Counter, deque

AUTO = "auto"

#: The languages a person can choose in Settings › Voice › Language, by name. Whisper understands every one.
LANGUAGES: dict[str, str] = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "sv": "Swedish", "da": "Danish",
    "no": "Norwegian", "fi": "Finnish", "cs": "Czech", "ro": "Romanian", "hu": "Hungarian",
    "el": "Greek", "tr": "Turkish", "ru": "Russian", "uk": "Ukrainian", "ar": "Arabic",
    "he": "Hebrew", "fa": "Persian", "ur": "Urdu", "hi": "Hindi", "bn": "Bengali",
    "ta": "Tamil", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "vi": "Vietnamese",
    "th": "Thai", "id": "Indonesian", "ms": "Malay", "sw": "Swahili",
}
#: Written right to left.
RTL_LANGUAGES = frozenset({"ar", "he", "fa", "ur"})

WHISPER_ENGINE = "local-whisper"
SENSEVOICE_ENGINE = "local-funasr-sensevoice"
#: SenseVoice Small's own languages (it also knows Cantonese, which is not in the list above).
SENSEVOICE_LANGUAGES = frozenset({"zh", "en", "ja", "ko"})


def setting(value: object) -> str:
    """A saved language setting: a code from ``LANGUAGES``, else ``auto``."""
    return value if isinstance(value, str) and value in LANGUAGES else AUTO


def requested(value: object) -> str | None:
    """The language to ask an engine for: the chosen code, or None to detect it."""
    chosen = setting(value)
    return None if chosen == AUTO else chosen


def name(code: str | None) -> str:
    return LANGUAGES.get(str(code or ""), str(code or ""))


def engine_languages(engine_id: str) -> frozenset[str] | None:
    """What a speech-to-text engine understands: None for every language (Whisper)."""
    if engine_id == SENSEVOICE_ENGINE:
        return SENSEVOICE_LANGUAGES
    from row_bot.voice import speech_models

    model = speech_models.by_engine(engine_id)
    if model is not None and model.language:
        return frozenset({model.language.split("-")[0]})
    return None


# ── Kokoro's voices for other languages ──────────────────────────────────────
# Kokoro v1.0's voices by language, from its own voice list. Japanese and Mandarin voices exist too but need a
# G2P package Row-Bot does not ship (misaki), so they are not offered: those answers stay on screen.
KOKORO_VOICES: dict[str, dict[str, str]] = {
    "es": {"ef_dora": "Dora (female)", "em_alex": "Alex (male)", "em_santa": "Santa (male)"},
    "fr": {"ff_siwis": "Siwis (female)"},
    "hi": {"hf_alpha": "Alpha (female)", "hf_beta": "Beta (female)", "hm_omega": "Omega (male)",
           "hm_psi": "Psi (male)"},
    "it": {"if_sara": "Sara (female)", "im_nicola": "Nicola (male)"},
    "pt": {"pf_dora": "Dora (female)", "pm_alex": "Alex (male)", "pm_santa": "Santa (male)"},
}
#: The voice used for a language until the person picks one (English: Kokoro's default voice).
ENGLISH_PRESET = "af_heart"
KOKORO_PRESETS: dict[str, str] = {language: next(iter(voices)) for language, voices in KOKORO_VOICES.items()}
#: Every language Kokoro speaks (English through the person's own voice list).
KOKORO_LANGUAGES = frozenset({"en", *KOKORO_VOICES})
# Kokoro's phonemizer (espeak-ng) language for each voice's first letter.
_KOKORO_PHONEMES = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "p": "pt-br"}
_KOKORO_LANGUAGE = {"a": "en", "b": "en", "e": "es", "f": "fr", "h": "hi", "i": "it", "p": "pt"}


def kokoro_phonemes(voice_id: str) -> str:
    """The ``lang`` Kokoro needs for a voice (``en-us`` for an unknown one)."""
    return _KOKORO_PHONEMES.get(str(voice_id or "a")[:1], "en-us")


def kokoro_language(voice_id: str) -> str:
    return _KOKORO_LANGUAGE.get(str(voice_id or "a")[:1], "en")


def kokoro_voice_ids() -> frozenset[str]:
    return frozenset(voice for voices in KOKORO_VOICES.values() for voice in voices)


def kokoro_voice_for(text: str, *, hint: str | None, own: str, picked: dict[str, str]) -> tuple[str, str | None]:
    """The language *text* is spoken in and the Kokoro voice that speaks it (None: Kokoro can't).

    The text's own language decides when it is clear; else *hint* (what the person was heard speaking)
    when Kokoro speaks it; else the language of the person's own voice, *own*, which speaks its own
    language. Another language gets the voice the person *picked* for it, or its preset.
    """
    own_language = kokoro_language(own)
    found = text_language(text)
    if found is None:
        found = hint if hint in KOKORO_LANGUAGES else own_language
    if found == own_language:
        return found, own
    if found == "en":
        return found, ENGLISH_PRESET
    if found not in KOKORO_VOICES:
        return found, None
    return found, picked.get(found) or KOKORO_PRESETS[found]


# ── The language of a piece of text ─────────────────────────────────────────
# Common words that mark a language. A guess needs two of them (one in a reply of three words or fewer) and
# must beat every other language, so a name or a borrowed word never switches the voice on its own.
_MARKER_WORDS: dict[str, frozenset[str]] = {
    "en": frozenset("the and is are was were you your that this with for have has not what it's of to in "
                    "on be will can do i it there they we he she my me hello thanks yes".split()),
    "es": frozenset("el la los las que de y es en un una por para con se lo su del al como pero más está "
                    "muy también hola gracias sí estoy eres son tu yo".split()),
    "fr": frozenset("le la les des est et un une que qui pas pour dans ce il elle vous nous avec sur au du "
                    "je c'est mais très bonjour merci oui suis tu".split()),
    "de": frozenset("der die das und ist nicht ich du sie wir mit ein eine zu den dem auf für es auch sind "
                    "von wie hallo danke bin".split()),
    "it": frozenset("il lo la gli le di che è e non per una un sono con mi ti ci del della anche ma più "
                    "questo come ciao grazie sei".split()),
    "pt": frozenset("o a os as que de e é não um uma para com do da em no na por mais você está muito "
                    "também olá obrigado obrigada sou".split()),
    "nl": frozenset("de het een en is van ik je niet dat op te zijn met voor maar ook wat er hallo dank".split()),
}
# Letters only one of these languages uses: one more point, once.
_MARKER_LETTERS = {"es": "ñ¿¡", "de": "ßäöü", "pt": "ãõ", "fr": "œç", "pl": "łąęśźżćń", "tr": "ığş",
                   "vi": "ơưđăạảấầẩẫậắằẳẵặẹẻẽếềểễệịọỏốồổỗộớờởỡợụủứừửữự"}
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")


def _script(char: str) -> str | None:
    code = ord(char)
    if 0x3040 <= code <= 0x30FF:
        return "kana"
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
        return "han"
    if 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:
        return "ko"
    if 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F or 0xFB50 <= code <= 0xFEFF:
        return "arabic"
    for low, high, script in ((0x0590, 0x05FF, "he"), (0x0400, 0x04FF, "cyrillic"), (0x0370, 0x03FF, "el"),
                              (0x0900, 0x097F, "hi"), (0x0980, 0x09FF, "bn"), (0x0B80, 0x0BFF, "ta"),
                              (0x0E00, 0x0E7F, "th")):
        if low <= code <= high:
            return script
    return "latin" if char.isalpha() else None


def _from_script(script: str, text: str) -> str:
    if script == "han":
        return "zh"
    if script == "kana":
        return "ja"
    if script == "arabic":
        if any(letter in text for letter in "ٹڈڑںے"):
            return "ur"
        return "fa" if any(letter in text for letter in "پچژگ") else "ar"
    if script == "cyrillic":
        return "uk" if any(letter in text.lower() for letter in "іїєґ") else "ru"
    return script


def text_language(text: str) -> str | None:
    """A cheap guess at the language *text* is written in, or None when it is not clear."""
    scripts = Counter(script for char in str(text or "") if (script := _script(char)) is not None)
    if not scripts:
        return None
    if scripts["kana"]:
        scripts["kana"] += scripts.pop("han", 0)  # Japanese writes kanji between its kana.
    script, count = scripts.most_common(1)[0]
    if script != "latin":
        return _from_script(script, text) if count * 2 >= sum(scripts.values()) else None
    lowered = text.lower()
    words = _WORD.findall(lowered.replace("’", "'"))
    scores = {language: sum(1 for word in words if word in marked) for language, marked in _MARKER_WORDS.items()}
    for language, letters in _MARKER_LETTERS.items():
        if any(letter in lowered for letter in letters):
            scores[language] = scores.get(language, 0) + 1
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (best, top), runner_up = ranked[0], (ranked[1][1] if len(ranked) > 1 else 0)
    needed = 1 if len(words) <= 3 else 2
    return best if top >= needed and top > runner_up else None


# ── What the person was heard speaking ──────────────────────────────────────
# The languages detected in the person's last turns (Dictate, Talk, voice notes), kept in memory only, so
# Settings can say when an English-only engine keeps hearing another language.
_HEARD: deque[str] = deque(maxlen=10)
_HEARD_LOCK = threading.Lock()
# Of the last ten detections, this many in another language is "often".
HEARD_OFTEN = 3


def note_heard(language: str | None) -> None:
    code = str(language or "").split("-")[0].lower()
    if code in LANGUAGES:
        with _HEARD_LOCK:
            _HEARD.append(code)


def heard_often(understood: frozenset[str]) -> str | None:
    """The language most often heard lately that *understood* leaves out, when it was heard often."""
    with _HEARD_LOCK:
        others = Counter(code for code in _HEARD if code not in understood)
    if not others:
        return None
    code, count = others.most_common(1)[0]
    return code if count >= HEARD_OFTEN else None


def forget_heard() -> None:
    with _HEARD_LOCK:
        _HEARD.clear()
