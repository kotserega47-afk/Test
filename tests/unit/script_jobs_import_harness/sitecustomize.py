"""Sitecustomize for script_jobs tests that import registry/runtime/delivery.

Loaded only when this directory is first on PYTHONPATH.

Stubs integrations.telegram_bot before any project import so the child does
not start the production asyncio loop. Runtime, delivery, SCRIPT_REGISTRY,
and command callbacks are the real modules.
"""

from __future__ import annotations

import sys
import types

_NAME = "integrations.telegram_bot"


def _install() -> None:
    if _NAME in sys.modules:
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


_install()
