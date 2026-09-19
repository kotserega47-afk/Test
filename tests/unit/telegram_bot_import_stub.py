"""Install a telegram_bot stub before imports that pull main.py.

Importing SCRIPT_REGISTRY loads operator_wallets_ready → main → telegram_bot,
which starts a Windows IOCP asyncio thread. That parent-process thread races
with later subprocess.communicate() (registration dump) and can interrupt
pytest. This stub is test isolation only.
"""

from __future__ import annotations

import sys
import types

_NAME = "integrations.telegram_bot"


def install_telegram_bot_stub() -> None:
    existing = sys.modules.get(_NAME)
    if existing is not None and getattr(existing, "__file__", None):
        return
    if existing is not None:
        return
    stub = types.ModuleType(_NAME)
    stub.send_message_sync = lambda *a, **k: None
    stub.send_message = lambda *a, **k: None
    stub.send_file_sync = lambda *a, **k: None
    stub.send_document_sync = lambda *a, **k: None
    stub.sanitize_telegram_error = lambda s: s
    stub.log_telegram_health_if_due = lambda *a, **k: None
    stub.get_telegram_sender_health_snapshot = lambda: {}
    sys.modules[_NAME] = stub


install_telegram_bot_stub()
