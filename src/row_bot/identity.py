"""Row-Bot identity configuration: name, personality, and sanitization.

Stores assistant identity in ``~/.row-bot/user_config.json`` under the
``"identity"`` key, alongside the existing ``"avatar"`` data.
"""

from __future__ import annotations

import json
import logging
import re

from row_bot.brand import APP_DISPLAY_NAME
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

_DATA_DIR = get_row_bot_data_dir()
_USER_CONFIG_PATH = _DATA_DIR / "user_config.json"

_DEFAULT_NAME = APP_DISPLAY_NAME
_PERSONALITY_MAX_LEN = 200

# Patterns that look like prompt-injection attempts
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"<\|?\s*system\s*\|?>", re.IGNORECASE),
    re.compile(r"\bact\s+as\s+(a|an|the)\b", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all|your)\b", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"override\s+(your\s+)?(rules|instructions|prompt)", re.IGNORECASE),
]


def sanitize_personality(text: str) -> str:
    """Sanitize personality text: trim, enforce length, strip injection patterns."""
    text = text.strip()
    if len(text) > _PERSONALITY_MAX_LEN:
        text = text[:_PERSONALITY_MAX_LEN].rstrip()
    for pat in _INJECTION_PATTERNS:
        text = pat.sub("", text)
    # Collapse double spaces left by removals
    text = re.sub(r"  +", " ", text).strip()
    return text


def _load_user_config() -> dict:
    """Load the full user_config.json."""
    try:
        if _USER_CONFIG_PATH.exists():
            return json.loads(_USER_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_user_config(config: dict) -> None:
    """Write full user_config.json (read-merge-write safe)."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _USER_CONFIG_PATH.write_text(
            json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Failed to save identity config: %s", exc)


def get_identity_config() -> dict:
    """Return the identity dict (name, personality).  Always has defaults."""
    raw = _load_user_config().get("identity", {})
    return {
        "name": raw.get("name", _DEFAULT_NAME) or _DEFAULT_NAME,
        "personality": raw.get("personality", ""),
    }


def save_identity_config(identity: dict) -> None:
    """Persist identity dict into user_config.json (merges with existing data)."""
    config = _load_user_config()
    config["identity"] = {
        "name": (identity.get("name") or _DEFAULT_NAME).strip() or _DEFAULT_NAME,
        "personality": sanitize_personality(identity.get("personality", "")),
    }
    _save_user_config(config)


def get_assistant_name() -> str:
    """Convenience: return the configured assistant name."""
    return get_identity_config()["name"]


def get_personality() -> str:
    """Convenience: return the configured personality text."""
    return get_identity_config()["personality"]


def is_self_improvement_enabled() -> bool:
    """Return whether the self-improvement features are enabled."""
    raw = _load_user_config().get("identity", {})
    return raw.get("self_improvement", True)


def set_self_improvement_enabled(enabled: bool) -> None:
    """Persist the self-improvement toggle."""
    config = _load_user_config()
    identity = config.get("identity", {})
    identity["self_improvement"] = bool(enabled)
    config["identity"] = identity
    _save_user_config(config)
