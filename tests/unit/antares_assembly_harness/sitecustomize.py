"""Sitecustomize for isolated Antares assembly tests.

Loaded only when this directory is first on PYTHONPATH.

Stubs integrations.telegram_bot and a no-op playwright.sync_api before project
imports so the child does not start the production send loop or a browser.
Does not stub integrations.tg_commands or raccoon modules.
"""

from __future__ import annotations

import sys
import types

_TELEGRAM = "integrations.telegram_bot"
if _TELEGRAM not in sys.modules:
    stub = types.ModuleType(_TELEGRAM)
    stub.send_message_sync = lambda *a, **k: None
    stub.send_message = lambda *a, **k: None
    stub.send_file_sync = lambda *a, **k: None
    stub.send_document_sync = lambda *a, **k: None
    stub.sanitize_telegram_error = lambda s: s
    stub.log_telegram_health_if_due = lambda *a, **k: None
    stub.get_telegram_sender_health_snapshot = lambda: {
        "status": "idle",
        "total_sent": 0,
        "total_failed": 0,
        "queue_depth": 0,
        "consecutive_failures": 0,
        "last_success_age_sec": "none",
        "last_error_class": "none",
    }
    sys.modules[_TELEGRAM] = stub

_PW = "playwright"
_PW_SYNC = "playwright.sync_api"
if _PW not in sys.modules:
    pw = types.ModuleType(_PW)
    sync = types.ModuleType(_PW_SYNC)

    class _Playwright:
        def __enter__(self):
            raise RuntimeError("playwright must not start during assembly tests")

        def __exit__(self, *args):
            return False

    def sync_playwright():
        return _Playwright()

    sync.sync_playwright = sync_playwright
    pw.sync_api = sync
    sys.modules[_PW] = pw
    sys.modules[_PW_SYNC] = sync
