# transport/telegram_transport.py
from __future__ import annotations

from typing import Optional

from integrations.telegram_bot import send_file_sync, send_message_sync


def send_text(*, text: str, chat_id: str) -> None:
    # transport-only wrapper
    send_message_sync(text, chat_id=chat_id)


def send_document(*, path: str, chat_id: str, caption: str | None = None) -> None:
    send_file_sync(path, caption, chat_id=chat_id)
