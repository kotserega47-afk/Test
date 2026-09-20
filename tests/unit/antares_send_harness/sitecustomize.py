"""Sitecustomize for isolated Antares send-boundary tests.

Stubs integrations.telegram_bot before project imports. Boot harness must not
use this stub; it forbids the sender instead.
"""

from __future__ import annotations

import sys
import types

_NAME = "integrations.telegram_bot"


def _install() -> None:
    if _NAME in sys.modules:
        return
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


_install()
