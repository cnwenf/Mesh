"""mesh_runtime.logging_config — structured daemon logging (§4.3 log_level)."""

from __future__ import annotations

import logging
import sys

import pytest

from mesh_runtime.logging_config import (
    DEFAULT_LOG_LEVEL,
    LOG_LEVELS,
    StructuredFormatter,
    configure_logging,
)


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """These tests rewire the root logger; restore it for later suites."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    root.handlers.clear()
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


def _record(msg: str, *, level: int = logging.INFO, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(
        name="mesh_runtime.test", level=level, pathname=__file__, lineno=1,
        msg=msg, args=None, exc_info=exc_info,
    )


class TestConfigureLogging:
    def test_sets_root_level_and_single_handler(self):
        configure_logging("debug")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1

    def test_accepts_every_documented_level(self):
        for level in LOG_LEVELS:
            configure_logging(level)
            assert logging.getLogger().level == logging.getLevelName(level)

    def test_rejects_unknown_level(self):
        with pytest.raises(ValueError, match="log_level"):
            configure_logging("verbose")

    def test_idempotent_single_handler_after_reconfigure(self):
        configure_logging("INFO")
        configure_logging("WARNING")
        assert len(logging.getLogger().handlers) == 1
        assert logging.getLogger().level == logging.WARNING

    def test_default_level_is_info(self):
        assert DEFAULT_LOG_LEVEL == "INFO"

    def test_records_reach_stderr_as_single_structured_lines(self, capsys):
        configure_logging("INFO")
        logging.getLogger("mesh_runtime.x").info("hello world")
        err = capsys.readouterr().err
        assert "level=INFO" in err
        assert "logger=mesh_runtime.x" in err
        assert "msg='hello world'" in err
        assert err.count("\n") == 1  # exactly one line


class TestStructuredFormatter:
    def test_renders_single_line(self):
        line = StructuredFormatter().format(_record("hello world"))
        assert line.startswith("ts=")
        assert "level=INFO" in line
        assert "logger=mesh_runtime.test" in line
        assert "msg='hello world'" in line
        assert "\n" not in line

    def test_newline_in_message_cannot_forge_log_lines(self):
        line = StructuredFormatter().format(_record("forged\nlevel=CRITICAL fake"))
        assert "\n" not in line  # repr-quoting escapes the newline

    def test_exception_traceback_appended(self):
        try:
            raise ValueError("boom")
        except ValueError:
            record = _record("failed", level=logging.ERROR, exc_info=sys.exc_info())
        text = StructuredFormatter().format(record)
        assert "msg='failed'" in text
        assert "ValueError: boom" in text
