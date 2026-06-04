"""Row-Bot persistent structured logging to daily JSONL files.

Provides dual-layer logging: the existing console logger (stderr, plain text)
is preserved, and a second ``TimedRotatingFileHandler`` writes structured JSON
lines to ``~/.row-bot/logs/row_bot.log`` (rotated daily).

Usage (called once at import time from ``app.py``)::

    from row_bot.logging_config import setup_file_logging
    setup_file_logging()

Log level for file output is configurable at runtime via
``set_file_log_level()`` and persisted in ``~/.row-bot/user_config.json``.
"""

from __future__ import annotations

import json
import logging
import pathlib
import time
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

from row_bot.brand import STRUCTURED_LOG_FILENAME
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

_DATA_DIR = get_row_bot_data_dir()
_LOG_DIR = _DATA_DIR / "logs"
_CONFIG_PATH = _DATA_DIR / "user_config.json"

_RETENTION_DAYS = 7
_file_handler: TimedRotatingFileHandler | None = None
_SUPPRESSED_RECENT_LOG_SUBSTRINGS = (
    "PyNaCl is not installed, voice will NOT be supported",
    "davey is not installed, voice will NOT be supported",
)


# ═════════════════════════════════════════════════════════════════════════════
# JSON FORMATTER
# ═════════════════════════════════════════════════════════════════════════════

class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": datetime.fromtimestamp(record.created).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            )[:-3],
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = self.formatException(record.exc_info)
        # Extra structured fields (e.g. tool name, tokens, duration)
        for key in ("tool", "tokens", "duration_ms", "model", "thread_id",
                     "task_name", "step", "phase"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        return json.dumps(entry, default=str, ensure_ascii=False)


# ═════════════════════════════════════════════════════════════════════════════
# SETUP
# ═════════════════════════════════════════════════════════════════════════════

def setup_file_logging() -> None:
    """Attach a daily-rotating JSON file handler to the root logger.

    Safe to call multiple times — silently returns if already attached.
    """
    global _file_handler
    if _file_handler is not None:
        return

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    log_path = _LOG_DIR / STRUCTURED_LOG_FILENAME
    _file_handler = TimedRotatingFileHandler(
        str(log_path),
        when="midnight",
        interval=1,
        backupCount=_RETENTION_DAYS,
        encoding="utf-8",
        utc=False,
    )
    _file_handler.suffix = "%Y-%m-%d"
    _file_handler.setFormatter(JsonFormatter())

    level = _load_file_log_level()
    _file_handler.setLevel(getattr(logging, level, logging.DEBUG))

    logging.getLogger().addHandler(_file_handler)
    logger.info("File logging started → %s", log_path)

    # Clean up old log files beyond retention
    _cleanup_old_logs()


def _load_file_log_level() -> str:
    """Read the persisted file log level from user_config.json."""
    try:
        if _CONFIG_PATH.exists():
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            return cfg.get("file_log_level", "DEBUG").upper()
    except Exception:
        pass
    return "DEBUG"


def _save_file_log_level(level: str) -> None:
    """Persist the file log level to user_config.json."""
    cfg: dict = {}
    try:
        if _CONFIG_PATH.exists():
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    cfg["file_log_level"] = level.upper()
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# RUNTIME API
# ═════════════════════════════════════════════════════════════════════════════

def set_file_log_level(level: str) -> None:
    """Change the file handler's log level at runtime and persist it."""
    numeric = getattr(logging, level.upper(), None)
    if numeric is None:
        return
    if _file_handler is not None:
        _file_handler.setLevel(numeric)
    _save_file_log_level(level.upper())


def get_file_log_level() -> str:
    """Return the current file log level as a string."""
    if _file_handler is not None:
        return logging.getLevelName(_file_handler.level)
    return _load_file_log_level()


def get_log_dir() -> pathlib.Path:
    """Return the log directory path."""
    return _LOG_DIR


def get_current_log_path() -> pathlib.Path | None:
    """Return the path to today's active log file (if it exists)."""
    log_path = _LOG_DIR / STRUCTURED_LOG_FILENAME
    if log_path.exists():
        return log_path
    return None


def _include_recent_log_entry(entry: dict) -> bool:
    """Return whether a recent-log entry should be shown to the app."""
    msg = str(entry.get("msg", ""))
    return not any(text in msg for text in _SUPPRESSED_RECENT_LOG_SUBSTRINGS)


def read_recent_logs(n: int = 20) -> list[dict]:
    """Read the last *n* log entries from the current log file.

    Returns a list of dicts (parsed JSON lines), newest first.
    """
    log_path = get_current_log_path()
    if not log_path or not log_path.exists():
        return []

    lines: list[str] = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            # Read all lines and take the last n — simple and safe for
            # daily log files which are unlikely to exceed a few MB.
            all_lines = f.readlines()
            lines = all_lines[-n:]
    except Exception:
        return []

    entries: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            entry = {"ts": "", "level": "?", "msg": line}
        if _include_recent_log_entry(entry):
            entries.append(entry)
    return entries


def get_log_stats() -> dict:
    """Return basic log statistics for the health-check pill."""
    log_path = get_current_log_path()
    stats: dict = {
        "log_dir": str(_LOG_DIR),
        "today_file": str(log_path) if log_path else None,
        "today_size_kb": 0,
        "total_files": 0,
        "total_size_kb": 0,
    }
    if not _LOG_DIR.exists():
        return stats

    for f in _LOG_DIR.iterdir():
        if f.is_file() and f.name.startswith(STRUCTURED_LOG_FILENAME):
            stats["total_files"] += 1
            stats["total_size_kb"] += f.stat().st_size / 1024

    if log_path and log_path.exists():
        stats["today_size_kb"] = log_path.stat().st_size / 1024

    stats["total_size_kb"] = round(stats["total_size_kb"], 1)
    stats["today_size_kb"] = round(stats["today_size_kb"], 1)
    return stats


# ═════════════════════════════════════════════════════════════════════════════
# CLEANUP
# ═════════════════════════════════════════════════════════════════════════════

def _cleanup_old_logs() -> None:
    """Remove log files older than _RETENTION_DAYS."""
    if not _LOG_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=_RETENTION_DAYS)
    for f in _LOG_DIR.iterdir():
        if not f.is_file() or not f.name.startswith(STRUCTURED_LOG_FILENAME):
            continue
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                f.unlink()
        except Exception:
            pass
