"""Run a coroutine without asyncio.run()'s SIGINT handler.

Bind/registration tests spawn subprocesses. On Windows those children can
deliver CTRL_C_EVENT to the console process group; asyncio.run() turns that
into KeyboardInterrupt even after the coroutine finished.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TypeVar

T = TypeVar("T")


def run_coro(coro: Coroutine[object, object, T]) -> T:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)
