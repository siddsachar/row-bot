from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.subsystem


def _reload_logging_config(monkeypatch, tmp_path):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    sys.modules.pop("row_bot.logging_config", None)
    import row_bot.logging_config as logging_config

    return logging_config


def _detach_file_handler(logging_config) -> None:
    handler = getattr(logging_config, "_file_handler", None)
    if handler is not None:
        logging.getLogger().removeHandler(handler)
        handler.close()
        logging_config._file_handler = None


def test_json_formatter_preserves_core_extra_and_exception_fields(monkeypatch, tmp_path) -> None:
    logging_config = _reload_logging_config(monkeypatch, tmp_path)
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


def test_file_log_level_persists_and_invalid_values_are_ignored(monkeypatch, tmp_path) -> None:
    logging_config = _reload_logging_config(monkeypatch, tmp_path)

    assert logging_config.get_file_log_level() == "DEBUG"
    logging_config.set_file_log_level("WARNING")
    assert logging_config.get_file_log_level() == "WARNING"

    sys.modules.pop("row_bot.logging_config", None)
    reloaded = importlib.import_module("row_bot.logging_config")
    assert reloaded.get_file_log_level() == "WARNING"
    reloaded.set_file_log_level("INVALID_LEVEL")
    assert reloaded.get_file_log_level() == "WARNING"


def test_setup_is_idempotent_and_recent_log_stats_are_structured(monkeypatch, tmp_path) -> None:
    logging_config = _reload_logging_config(monkeypatch, tmp_path)
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
        assert logging_config._RETENTION_DAYS == 7

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


def test_logging_health_and_ui_wiring_contracts() -> None:
    from row_bot.ui.status_checks import ALL_CHECKS, check_logging

    result = check_logging()
    assert result.name == "Logging"
    assert result.status in {"ok", "warn", "error", "inactive"}
    assert result.settings_tab == "System"
    assert check_logging in ALL_CHECKS

    settings_source = Path("src/row_bot/ui/settings.py").read_text(encoding="utf-8")
    home_source = Path("src/row_bot/ui/home.py").read_text(encoding="utf-8")
    app_source = Path("src/row_bot/app.py").read_text(encoding="utf-8")

    assert "set_file_log_level" in settings_source
    assert "Open Log Folder" in settings_source
    assert "read_recent_logs" in home_source
    assert "View Full Log" in home_source
    assert "setup_file_logging()" in app_source
