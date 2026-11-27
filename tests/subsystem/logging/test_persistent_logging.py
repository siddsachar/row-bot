from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.subsystem


def _reload_logging_config(reload_for_data_dir, tmp_path):
    (logging_config,) = reload_for_data_dir(tmp_path, "row_bot.logging_config")
    return logging_config


def _detach_file_handler(logging_config) -> None:
    handler = getattr(logging_config, "_file_handler", None)
    if handler is not None:
        logging.getLogger().removeHandler(handler)
        handler.close()
        logging_config._file_handler = None


def test_json_formatter_preserves_core_extra_and_exception_fields(reload_for_data_dir, tmp_path) -> None:
    logging_config = _reload_logging_config(reload_for_data_dir, tmp_path)
    formatter = logging_config.JsonFormatter()

    record = logging.LogRecord("test.logger", logging.INFO, "test.py", 1, "Hello %s", ("world",), None)
    parsed = json.loads(formatter.format(record))
    assert parsed["level"] == "INFO"
    assert parsed["msg"] == "Hello world"
    assert parsed["logger"] == "test.logger"
    assert "ts" in parsed

    extra = logging.LogRecord("test", logging.DEBUG, "t.py", 1, "tool call", None, None)
    extra.tool = "web_search"
    extra.duration_ms = 1234
    parsed_extra = json.loads(formatter.format(extra))
    assert parsed_extra["tool"] == "web_search"
    assert parsed_extra["duration_ms"] == 1234

    try:
        raise ValueError("test error")
    except ValueError:
        exc_info = sys.exc_info()
    failed = logging.LogRecord("test", logging.ERROR, "t.py", 1, "failed", None, exc_info)
    parsed_failed = json.loads(formatter.format(failed))
    assert "ValueError" in parsed_failed["exc"]


def test_setup_is_idempotent_and_recent_log_stats_are_structured(reload_for_data_dir, tmp_path) -> None:
    logging_config = _reload_logging_config(reload_for_data_dir, tmp_path)
    try:
        logging_config.setup_file_logging()
        logging_config.setup_file_logging()

        root = logging.getLogger()
        file_handlers = [
            handler
            for handler in root.handlers
            if isinstance(handler, logging.handlers.TimedRotatingFileHandler)
            and Path(handler.baseFilename).parent == logging_config.get_log_dir()
        ]
        assert len(file_handlers) == 1

        logger = logging.getLogger("row_bot.test.logging_contract")
        logger.warning("visible persistent log entry")
        for handler in file_handlers:
            handler.flush()

        recent = logging_config.read_recent_logs(5)
        assert any(entry.get("msg") == "visible persistent log entry" for entry in recent)

        stats = logging_config.get_log_stats()
        assert {"log_dir", "today_file", "today_size_kb", "total_files", "total_size_kb"} <= set(stats)
        assert stats["total_files"] >= 1
    finally:
        _detach_file_handler(logging_config)


def _file_entries(logging_config) -> list[dict]:
    logging_config._file_handler.flush()
    return logging_config.read_recent_logs(50)


@pytest.mark.parametrize(
    ("saved", "debug_kept"),
    [(None, False), ("DEBUG", True), ("WARNING", False)],
    ids=["new-profile", "everything", "warnings"],
)
def test_the_chosen_log_detail_is_what_the_file_keeps(reload_for_data_dir, tmp_path, saved, debug_kept) -> None:
    """A new profile keeps Normal detail; Everything (debug) really writes debug lines, and only to the file."""
    if saved:
        (tmp_path / "user_config.json").write_text(json.dumps({"file_log_level": saved}), encoding="utf-8")
    logging_config = _reload_logging_config(reload_for_data_dir, tmp_path)
    root = logging.getLogger()
    console_stream = []

    class _Console(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            console_stream.append(record.levelname)

    console = _Console()
    kept_level = root.level
    kept_handler_levels = {handler: handler.level for handler in root.handlers}
    root.setLevel(logging.INFO)
    root.addHandler(console)
    try:
        logging_config.setup_file_logging()
        logger = logging.getLogger("row_bot.test.log_detail")
        logger.debug("a debug line")
        logger.info("an info line")
        messages = {entry.get("msg") for entry in _file_entries(logging_config)}
        assert ("a debug line" in messages) is debug_kept
        assert ("an info line" in messages) is (saved != "WARNING")
        assert "DEBUG" not in console_stream
    finally:
        _detach_file_handler(logging_config)
        root.removeHandler(console)
        root.setLevel(kept_level)
        for handler, level in kept_handler_levels.items():
            handler.setLevel(level)


def test_logging_health_check_is_registered() -> None:
    from row_bot.status_checks import ALL_CHECKS, check_logging

    result = check_logging()
    assert result.name == "Logging"
    assert result.status in {"ok", "warn", "error", "inactive"}
    assert result.settings_tab == "System"
    assert check_logging in ALL_CHECKS
