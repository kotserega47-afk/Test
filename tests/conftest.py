"""Pytest options for TASK-04 explicit Platform compare. Not production."""

from __future__ import annotations

import asyncio
import signal
import sys
import threading

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--platform-checkout",
        action="store",
        default=None,
        help=(
            "Path to a clean Platform_2.0 checkout at "
            "ebbcd6c11b0c2418575f807edd40feb1c4fed468. Required to collect "
            "tests/compare_platform_raccoon_payin.py."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "platform_compare: explicit Platform checkout compare (needs --platform-checkout)",
    )
    if sys.platform == "win32":
        # Child python processes in this suite can deliver CTRL_C_EVENT to the
        # console group; ignore it so later tests are not aborted.
        signal.signal(signal.SIGINT, signal.SIG_IGN)



def pytest_ignore_collect(collection_path, config: pytest.Config) -> bool:  # noqa: ANN001
    if collection_path.name == "compare_platform_raccoon_payin.py":
        return not bool(config.getoption("--platform-checkout"))
    return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--platform-checkout"):
        return
    items[:] = [item for item in items if item.get_closest_marker("platform_compare") is None]


def _stop_telegram_bot_background_loop() -> None:
    """If a test imported the real telegram_bot module, stop its asyncio loop."""
    mod = sys.modules.get("integrations.telegram_bot")
    if mod is None or getattr(mod, "__file__", None) is None:
        return
    loop = getattr(mod, "loop", None)
    if loop is None or not loop.is_running():
        return
    request = getattr(mod, "request", None)
    shutdown = getattr(request, "shutdown", None)
    if callable(shutdown):
        asyncio.run_coroutine_threadsafe(shutdown(), loop).result()
    loop.call_soon_threadsafe(loop.stop)
    runner = getattr(mod, "_loop_runner", None)
    for thread in threading.enumerate():
        if getattr(thread, "_target", None) is runner:
            thread.join()
            break


@pytest.fixture(scope="session", autouse=True)
def _stop_telegram_bot_loop_imported_by_tests() -> None:
    yield
    _stop_telegram_bot_background_loop()
