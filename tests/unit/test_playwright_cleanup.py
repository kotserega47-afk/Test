"""Tests for core.playwright_cleanup."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.playwright_cleanup import close_playwright_stack


def test_close_playwright_stack_closes_in_order_and_skips_closed_page() -> None:
    page = MagicMock()
    page.is_closed.return_value = True
    context = MagicMock()
    browser = MagicMock()

    close_playwright_stack(page=page, context=context, browser=browser)

    page.close.assert_not_called()
    context.close.assert_called_once()
    browser.close.assert_called_once()


def test_close_playwright_stack_never_raises_on_close_error() -> None:
    browser = MagicMock()
    browser.close.side_effect = RuntimeError("already closed")

    close_playwright_stack(browser=browser)
