"""Async gates for deterministic cleanup-hold scenarios. Test harness only."""

from __future__ import annotations

import asyncio

_entered: asyncio.Event | None = None
_release: asyncio.Event | None = None


def gates() -> tuple[asyncio.Event, asyncio.Event]:
    global _entered, _release
    if _entered is None or _release is None:
        _entered = asyncio.Event()
        _release = asyncio.Event()
    return _entered, _release
