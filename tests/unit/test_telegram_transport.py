from __future__ import annotations

from unittest.mock import patch

from integrations.bakai_monitor_playwright import _send_to_current_route
from integrations.wallet_editor_registry import _send_chat_warning
from integrations.wallet_editor_registry_refresh import _send_to_route
from transport.telegram_transport import send_document, send_text


def test_send_text_calls_telegram_bot_boundary() -> None:
    with patch("integrations.telegram_bot.send_message_sync") as send:
        send_text(text="hello", chat_id="-100")
    send.assert_called_once_with("hello", chat_id="-100")


def test_send_document_calls_telegram_bot_boundary() -> None:
    with patch("integrations.telegram_bot.send_file_sync") as send:
        send_document(path="/tmp/x.xlsx", chat_id="-100", caption="cap")
    send.assert_called_once_with("/tmp/x.xlsx", "cap", chat_id="-100")


def test_bakai_legacy_send_uses_telegram_bot_boundary(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHAT_ID_RATE", "-legacy-current")
    monkeypatch.delenv("TELEGRAM_ROUTES_FROM_RULES_V2", raising=False)
    from integrations.bakai_monitor_playwright import ENV_BAKAI_RATE_CURRENT_LEGACY

    monkeypatch.setenv(ENV_BAKAI_RATE_CURRENT_LEGACY, "-legacy-current")
    with patch("integrations.telegram_bot.send_message_sync") as send:
        _send_to_current_route("current msg")
    send.assert_called_once_with("current msg", chat_id="-legacy-current")


def test_registry_warning_send_uses_telegram_bot_boundary() -> None:
    with patch("integrations.telegram_bot.send_message_sync") as send:
        _send_chat_warning(-42, "warn")
    send.assert_called_once_with("warn", chat_id="-42")


def test_refresh_send_uses_telegram_bot_boundary() -> None:
    class _Res:
        chat_id = -7

    with patch(
        "integrations.wallet_editor_registry_refresh.resolve_route_chat_id",
        return_value=_Res(),
    ):
        with patch("integrations.telegram_bot.send_message_sync") as send:
            assert _send_to_route("wallet_editor_registry_refresh", "txt") is True
    send.assert_called_once_with("txt", chat_id="-7")



def test_send_text_calls_telegram_bot_boundary() -> None:
    with patch("integrations.telegram_bot.send_message_sync") as send:
        send_text(text="hello", chat_id="-100")
    send.assert_called_once_with("hello", chat_id="-100")


def test_send_document_calls_telegram_bot_boundary() -> None:
    with patch("integrations.telegram_bot.send_file_sync") as send:
        send_document(path="/tmp/x.xlsx", chat_id="-100", caption="cap")
    send.assert_called_once_with("/tmp/x.xlsx", "cap", chat_id="-100")
