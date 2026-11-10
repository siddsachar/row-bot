"""What the chat says when a turn fails: Row-Bot's sentence, or the provider's own words."""

from __future__ import annotations

import re

_LIMIT = 400
_JSON_MESSAGE = re.compile(r"""['"]message['"]\s*:\s*['"](?P<message>(?:\\.|[^'"\\])+)['"]""")
_STATUS = re.compile(r"(?:error code|status code|http)\s*:?\s*(?P<code>[1-5]\d\d)", re.IGNORECASE)


def _provider_words(raw: str) -> str:
    """A raw provider error as one sentence: who refused, and the provider's reason."""
    if "Details:" in raw:
        head, _, details = raw.partition("Details:")
        who = head.split(". ", 1)[0].strip().rstrip(".")
        details = " ".join(details.split())
        return f"{who}: {details}" if who and details else (details or who)
    found = _JSON_MESSAGE.search(raw)
    if found:
        message = found.group("message").replace("\\'", "'").replace('\\"', '"')
        status = _STATUS.search(raw)
        return f"The provider refused the request (HTTP {status.group('code')}): {message}" if status else message
    return " ".join(raw.split())


def failure_reason(error_text: str) -> str:
    """The reason shown on a failed turn, with secrets taken out and kept short."""
    from row_bot.evolution import redact_secrets

    text = str(error_text or "").strip()
    text = text.removeprefix("⚠️").strip()
    if text.startswith("API error:"):
        text = _provider_words(text[len("API error:"):].strip())
    text = redact_secrets(text)
    if len(text) > _LIMIT:
        text = text[: _LIMIT - 1].rstrip() + "…"
    return text
