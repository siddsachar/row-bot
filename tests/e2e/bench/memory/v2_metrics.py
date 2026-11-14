"""LongMemEval-V2's deterministic scorers, ported from its ``evaluation/qa_eval_metrics.py``
(xiaowu0162/LongMemEval-V2, Apache-2.0).

The benchmark names a scorer per question (``eval_function``, e.g.
``norm_phrase_set_match|lower=true|separators=,;``). The downloaded file is never imported
or run; these functions keep its behaviour, and ``protocols.load_longmemeval_v2`` refuses a
download that no longer defines them. The two LLM judges are not here: their prompts are
read from the download itself.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

DEFAULT_SEPARATORS = (",", ";")
LLM_EVAL_FUNCTIONS = frozenset({"llm_abstention_checker", "llm_gotchas_checker"})
DETERMINISTIC = ("norm_phrase_set_match", "norm_phrase_set_match_ordered", "mc_choice_match", "mc_choice_set_match",
                 "extract_boxed_answer", "normalize_phrase", "split_phrases")
_FILLER = {"AND", "ANSWER", "ANSWERS", "CHOICE", "CHOICES", "FINAL", "LETTER", "LETTERS", "OPTION", "OPTIONS"}


def normalize_phrase(text: Any, *, lower: bool = True, normalize_hyphen: bool = True, strip_punct: bool = True) -> str:
    if text is None:
        return ""
    text = str(text)
    if lower:
        text = text.lower()
    if normalize_hyphen:
        text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"[,;]", " ", text)
    if strip_punct:
        text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def split_phrases(text: Any, *, separators: Iterable[str] = DEFAULT_SEPARATORS, **normalize: bool) -> list[str]:
    if text is None:
        return []
    separator_list = list(separators)
    if not separator_list:
        normalized = normalize_phrase(text, **normalize)
        return [normalized] if normalized else []
    parts = re.split("|".join(re.escape(item) for item in separator_list), str(text))
    return [part for part in (normalize_phrase(item, **normalize) for item in parts) if part]


def norm_phrase_set_match(prediction: Any, answer: Any, *, separators: Iterable[str] = DEFAULT_SEPARATORS,
                          require_non_empty: bool = True, **normalize: bool) -> bool:
    predicted = normalize_phrase(prediction, **normalize)
    phrases = split_phrases(answer, separators=separators, **normalize)
    if require_non_empty and (not predicted or not phrases):
        return False
    return all(re.search(r"\b%s\b" % re.escape(phrase), predicted) for phrase in set(phrases))


def norm_phrase_set_match_ordered(prediction: Any, answer: Any, *, separators: Iterable[str] = DEFAULT_SEPARATORS,
                                  require_non_empty: bool = True, **normalize: bool) -> bool:
    predicted = normalize_phrase(prediction, **normalize)
    phrases = split_phrases(answer, separators=separators, **normalize)
    if require_non_empty and (not predicted or not phrases):
        return False
    start = 0
    for phrase in phrases:
        match = re.search(r"\b%s\b" % re.escape(phrase), predicted[start:])
        if match is None:
            return False
        start += match.end()
    return True


def mc_choice_match(prediction: Any, answer: Any, *, strip_chars: str = ".", require_non_empty: bool = True,
                    **_: Any) -> bool:
    if prediction is None or answer is None:
        return False
    boxed = re.search(r"\\boxed\{([^}]*)\}", str(prediction).lower())
    cleaned = re.sub(r"\b(choice|option)\b", "", boxed.group(1) if boxed else str(prediction), flags=re.IGNORECASE)
    for character in strip_chars:
        cleaned = cleaned.replace(character, "")
    cleaned, expected = cleaned.strip().upper(), str(answer).strip().upper()
    if require_non_empty and (not cleaned or not expected):
        return False
    return cleaned == expected


def _letters(text: Any) -> list[str]:
    if text is None:
        return []
    return [letter for chunk in re.findall(r"[A-Z]+", str(text).upper()) if chunk not in _FILLER for letter in chunk]


def mc_choice_set_match(prediction: Any, answer: Any, *, require_non_empty: bool = True, **_: Any) -> bool:
    predicted, expected = _letters(prediction), _letters(answer)
    if require_non_empty and (not predicted or not expected):
        return False
    return set(predicted) == set(expected)


def extract_boxed_answer(text: str) -> str:
    """The last ``\\boxed{...}``, nested braces kept; the whole reply when there is none."""
    text = str(text or "")
    index = text.rfind("\\boxed{")
    if index == -1:
        return text.strip()
    position, depth, out = index + len("\\boxed{"), 1, []
    while position < len(text) and depth > 0:
        character = text[position]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                break
        out.append(character)
        position += 1
    parsed = "".join(out).strip()
    return parsed if parsed else text.strip()


def is_unknown(parsed: str) -> bool:
    return parsed.strip().lower() == "unknown"


def _value(key: str, value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"none", "null"}:
        return None
    if key in {"separators", "separator"}:
        stripped = value.strip()
        if not value:
            return []
        if stripped.startswith("[") and stripped.endswith("]"):
            return json.loads(stripped)
        return [character for character in value if not character.isspace()]
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def parse_spec(spec: str) -> tuple[str, dict[str, Any]]:
    """``name|key=value|...`` -> (name, options), as the benchmark parses it."""
    parts = [part.strip() for part in str(spec or "").split("|")]
    if not parts or not parts[0]:
        raise ValueError("eval function spec missing function name")
    options: dict[str, Any] = {}
    for part in parts[1:]:
        if not part:
            continue
        key, separator, value = part.partition("=")
        if not separator or not key.strip() or key.strip() in options:
            raise ValueError(f"invalid eval function option: {part}")
        options[key.strip()] = _value(key.strip(), value.strip())
    return parts[0], options


_SCORERS = {"norm_phrase_set_match": norm_phrase_set_match,
            "norm_phrase_set_match_ordered": norm_phrase_set_match_ordered,
            "mc_choice_match": mc_choice_match, "mc_choice_set_match": mc_choice_set_match}


def score(name: str, options: dict[str, Any], prediction: str, answer: str) -> bool:
    if name not in _SCORERS:
        raise ValueError(f"unknown eval function: {name}")
    return bool(_SCORERS[name](prediction, answer, **options))


def parse_binary_judgement(text: str) -> int | None:
    """The judges' {"label": 0 or 1}: JSON first, then the benchmark's fallback patterns; None when absent."""
    cleaned = str(text or "").strip()
    if cleaned.startswith("```") and cleaned.endswith("```") and len(cleaned.splitlines()) >= 3:
        cleaned = "\n".join(cleaned.splitlines()[1:-1]).strip()
    blob = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if blob:
        try:
            payload = json.loads(blob.group(0))
            if isinstance(payload, dict) and payload.get("label") in {0, 1, "0", "1"}:
                return int(payload["label"])
        except json.JSONDecodeError:
            pass
    for pattern in (r'"label"\s*:\s*([01])', r"'label'\s*:\s*([01])", r"\blabel\b\s*[:=]\s*([01])"):
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None
