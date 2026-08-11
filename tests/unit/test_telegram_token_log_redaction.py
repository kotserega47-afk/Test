"""Logging: Telegram bot token must never appear in rendered log lines."""

from __future__ import annotations

import logging

from utils.logger import TelegramTokenRedactionFilter, _quiet_http_loggers


FAKE_TOKEN = "0000000000:FAKE-TOKEN-FOR-UNIT-TEST-ONLY-xx"


def test_telegram_token_redacted_from_log_message():
    filt = TelegramTokenRedactionFilter()
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=(
            'HTTP Request: POST https://api.telegram.org/bot'
            + FAKE_TOKEN
            + '/getUpdates "HTTP/1.1 200 OK"'
        ),
        args=(),
        exc_info=None,
    )
    assert filt.filter(record) is True
    rendered = record.getMessage()
    assert FAKE_TOKEN not in rendered
    assert "/bot***REDACTED***/" in rendered
    assert "getUpdates" in rendered


def test_telegram_token_redacted_from_log_args():
    filt = TelegramTokenRedactionFilter()
    url = f"https://api.telegram.org/bot{FAKE_TOKEN}/getMe"
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="HTTP Request: POST %s",
        args=(url,),
        exc_info=None,
    )
    assert filt.filter(record) is True
    rendered = record.getMessage()
    assert FAKE_TOKEN not in rendered
    assert "/bot***REDACTED***/" in rendered


def test_httpx_logger_level_is_warning_or_higher():
    _quiet_http_loggers()
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING
