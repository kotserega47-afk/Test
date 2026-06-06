"""Telegram bot token redaction in logs, exceptions, and alert text."""
from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

import pytest
import requests

import integrations.telegram_bot as tg


TOKEN = "123456:ABCDEF"
TOKEN_URL = f"https://api.telegram.org/bot{TOKEN}/getUpdates"


@pytest.fixture(autouse=True)
def reset_health():
    tg._reset_telegram_sender_health_for_tests()
    yield
    tg._reset_telegram_sender_health_for_tests()


@pytest.fixture
def with_token(monkeypatch):
    monkeypatch.setattr(tg, "TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setattr(tg, "BASE_URL", f"https://api.telegram.org/bot{TOKEN}")
    yield


def test_sanitize_full_telegram_api_url(with_token):
    result = tg.sanitize_telegram_error(
        f"request failed for {TOKEN_URL} timeout"
    )
    assert TOKEN not in result
    assert "ABCDEF" not in result
    assert "https://api.telegram.org/bot<redacted>/getUpdates" in result


def test_sanitize_exception_text_with_bot_token_url(with_token):
    exc_text = (
        f"404 Client Error: Not Found for url: "
        f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    )
    result = tg.sanitize_telegram_error(exc_text)
    assert TOKEN not in result
    assert "https://api.telegram.org/bot<redacted>/sendMessage" in result


def test_sanitize_env_token_value(with_token, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TG_BOT_TOKEN", "999:SECONDARY")

    message = f"auth failed token={TOKEN} alt=999:SECONDARY"
    result = tg.sanitize_telegram_error(message)

    assert TOKEN not in result
    assert "999:SECONDARY" not in result
    assert "<redacted>" in result


def test_sanitize_preserves_normal_error_text(with_token):
    message = "network timeout while connecting to example.com"
    assert tg.sanitize_telegram_error(message) == message


def test_send_photo_sync_sanitizes_print_and_fallback_message(
    with_token, monkeypatch, capsys
):
    monkeypatch.setattr(tg, "loop", MagicMock())

    def _raise(*_args, **_kwargs):
        raise requests.RequestException(f"failed for {TOKEN_URL}")

    with patch("integrations.telegram_bot.requests.post", side_effect=_raise):
        with patch("builtins.open", mock_open(read_data=b"img")):
            tg.send_photo_sync("/tmp/shot.png", "caption", chat_id="-100")

    captured = capsys.readouterr()
    assert TOKEN not in captured.out
    assert "bot<redacted>" in captured.out or "<redacted>" in captured.out

    queued = tg.loop.call_soon_threadsafe.call_args
    assert queued is not None
    _func, (chat_id, text) = queued[0][1]
    assert chat_id == "-100"
    assert TOKEN not in text
    assert "ABCDEF" not in text


def test_send_message_sync_queue_error_is_sanitized(with_token, monkeypatch, caplog):
    mock_loop = MagicMock()
    mock_loop.call_soon_threadsafe.side_effect = RuntimeError(TOKEN_URL)
    monkeypatch.setattr(tg, "loop", mock_loop)

    with caplog.at_level("ERROR"):
        tg.send_message_sync("hello", chat_id="-100")

    assert any(TOKEN not in r.message for r in caplog.records)
    assert any("bot<redacted>" in r.message for r in caplog.records)


def test_send_file_sync_queue_error_is_sanitized(with_token, monkeypatch, caplog):
    mock_loop = MagicMock()
    mock_loop.call_soon_threadsafe.side_effect = RuntimeError(TOKEN_URL)
    monkeypatch.setattr(tg, "loop", mock_loop)

    with caplog.at_level("ERROR"):
        tg.send_file_sync("/tmp/file.xlsx", "caption", chat_id="-100")

    assert any(TOKEN not in r.message for r in caplog.records)
    assert any("bot<redacted>" in r.message for r in caplog.records)


def test_send_message_direct_request_exception_is_sanitized(with_token, monkeypatch):
    monkeypatch.setattr(tg, "TELEGRAM_TOKEN", TOKEN)

    def _raise(*_args, **_kwargs):
        raise requests.HTTPError(f"404 for url: https://api.telegram.org/bot{TOKEN}/sendMessage")

    with patch("integrations.telegram_bot.requests.post", side_effect=_raise):
        with pytest.raises(RuntimeError) as exc_info:
            tg.send_message_direct("hello", chat_id="-100")

    message = str(exc_info.value)
    assert TOKEN not in message
    assert "ABCDEF" not in message
    assert "bot<redacted>" in message


def test_health_failure_logs_and_state_sanitize_token(with_token, caplog):
    with caplog.at_level("ERROR"):
        tg._record_delivery_failure(
            "-100",
            "text",
            RuntimeError(f"getUpdates failed: {TOKEN_URL}"),
        )

    assert tg._health_state["last_error_message_safe"] is not None
    assert TOKEN not in tg._health_state["last_error_message_safe"]
    error_logs = [
        r.message
        for r in caplog.records
        if "Ошибка async отправки" in r.message
    ]
    assert error_logs
    assert TOKEN not in error_logs[0]
    assert "ABCDEF" not in error_logs[0]


def test_degraded_log_does_not_contain_token(with_token, caplog):
    with patch.dict("os.environ", {"TELEGRAM_HEALTH_DEGRADED_THRESHOLD": "1"}, clear=False):
        with caplog.at_level("ERROR"):
            tg._record_delivery_failure(
                "-100",
                "text",
                RuntimeError(f"failed {TOKEN_URL}"),
            )

    for record in caplog.records:
        assert TOKEN not in record.message
        assert "ABCDEF" not in record.message
