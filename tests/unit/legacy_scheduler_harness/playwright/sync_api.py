"""Blocked Playwright API: import succeeds, browser/Chromium work does not."""

from __future__ import annotations


class Error(Exception):
    pass


class TimeoutError(Error):
    pass


class Page:
    pass


class Locator:
    pass


class Browser:
    def new_context(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("playwright browser blocked in legacy scheduler test")

    def close(self) -> None:
        return None


class BrowserContext:
    def new_page(self) -> Page:
        raise RuntimeError("playwright page blocked in legacy scheduler test")

    def close(self) -> None:
        return None


class Chromium:
    def launch(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("chromium launch blocked in legacy scheduler test")

    def launch_persistent_context(self, *args, **kwargs):  # noqa: ANN003
        raise RuntimeError("chromium launch blocked in legacy scheduler test")


class _Playwright:
    chromium = Chromium()

    def stop(self) -> None:
        return None


class _SyncPlaywrightContext:
    def __enter__(self) -> _Playwright:
        raise RuntimeError("sync_playwright blocked in legacy scheduler test")

    def __exit__(self, *args) -> None:  # noqa: ANN002
        return None


def sync_playwright() -> _SyncPlaywrightContext:
    return _SyncPlaywrightContext()


def expect(*args, **kwargs):  # noqa: ANN002, ANN003
    raise RuntimeError("playwright expect blocked in legacy scheduler test")
