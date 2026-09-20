"""Install a sender stub before integrations.telegram_bot can load for real."""

from __future__ import annotations

import sys
import types

_NAME = "integrations.telegram_bot"


def ensure_sender_stub() -> types.ModuleType:
    existing = sys.modules.get(_NAME)
    if existing is not None and getattr(existing, "_ANTARES_SEND_STUB", False):
        return existing
    if existing is not None:
        return existing
    stub = types.ModuleType(_NAME)
    stub._ANTARES_SEND_STUB = True
    stub.send_message_sync = lambda *a, **k: None
    stub.send_message = lambda *a, **k: None
    stub.send_file_sync = lambda *a, **k: None
    stub.send_document_sync = lambda *a, **k: None
    stub.sanitize_telegram_error = lambda s: s
    stub.log_telegram_health_if_due = lambda *a, **k: None
    stub.get_telegram_sender_health_snapshot = lambda: {}
    sys.modules[_NAME] = stub
    return stub
