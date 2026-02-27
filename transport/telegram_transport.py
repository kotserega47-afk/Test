# transport/telegram_transport.py
from __future__ import annotations

from typing import Optional

from integrations.telegram_bot import send_message_sync


def send_text(*, text: str, chat_id: str) -> None:
    # transport-only wrapper
    send_message_sync(text, chat_id=chat_id)
