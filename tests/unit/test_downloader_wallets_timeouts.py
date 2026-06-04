"""WALLET-HANG-PATCH-A: Playwright timeouts and stage logs in downloader_wallets."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from integrations import downloader_wallets as dw

_WALLET_LOGGER = "utils.logger"


@contextmanager
def _mock_download_context():
    """Minimal page mock for _download_payin / _download_payout."""
    page = MagicMock()
    page.url = "https://antares.plus/lkcard/#/payin"
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.click = MagicMock()

    download = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.value = download
    page.expect_download.return_value = cm

    with patch("integrations.downloader_wallets.time.sleep"):
        with patch.object(dw, "_find_and_pick_date", return_value=True):
            yield page


def test_payin_goto_uses_60s_timeout() -> None:
    with _mock_download_context() as page:
        dw._download_payin(page, "12.00", 0)
    page.goto.assert_called_once_with(
        "https://antares.plus/lkcard/#/payin",
        timeout=dw.GOTO_TIMEOUT_MS,
    )
    assert dw.GOTO_TIMEOUT_MS == 60_000


def test_payout_goto_uses_60s_timeout() -> None:
    with _mock_download_context() as page:
        dw._download_payout(page, "12.00", 0)
    page.goto.assert_called_once_with(
        "https://antares.plus/lkcard/#/vyplaty",
        timeout=dw.GOTO_TIMEOUT_MS,
    )


def test_payin_calendar_selector_uses_15s_timeout() -> None:
    with _mock_download_context() as page:
        dw._download_payin(page, "12.00", 0)
    page.wait_for_selector.assert_called_with(
        ".b-calendar",
        timeout=dw.CALENDAR_SELECTOR_TIMEOUT_MS,
    )
    assert dw.CALENDAR_SELECTOR_TIMEOUT_MS == 15_000


def test_payin_networkidle_uses_60s_timeout() -> None:
    with _mock_download_context() as page:
        dw._download_payin(page, "12.00", 0)
    assert page.wait_for_load_state.call_args_list == [
        (("networkidle",), {"timeout": dw.NETWORKIDLE_TIMEOUT_MS}),
        (("networkidle",), {"timeout": dw.NETWORKIDLE_TIMEOUT_MS}),
    ]
    assert dw.NETWORKIDLE_TIMEOUT_MS == 60_000


def test_payout_networkidle_uses_60s_timeout() -> None:
    with _mock_download_context() as page:
        dw._download_payout(page, "12.00", 0)
    assert page.wait_for_load_state.call_args_list == [
        (("networkidle",), {"timeout": dw.NETWORKIDLE_TIMEOUT_MS}),
        (("networkidle",), {"timeout": dw.NETWORKIDLE_TIMEOUT_MS}),
    ]


def test_ensure_logged_in_goto_uses_60s_timeout() -> None:
    page = MagicMock()
    page.url = "https://antares.plus/lkcard/#/payin"
    context = MagicMock()
    dw._ensure_logged_in(page, context)
    assert page.goto.call_count == 1
    page.goto.assert_called_with(
        "https://antares.plus/lkcard/#/payin",
        wait_until="domcontentloaded",
        timeout=dw.GOTO_TIMEOUT_MS,
    )


def test_stage_logs_payin_payout(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=_WALLET_LOGGER):
        with _mock_download_context() as page:
            dw._download_payin(page, "12.00", 0)
        with _mock_download_context() as page:
            dw._download_payout(page, "12.00", 0)

    text = caplog.text
    for marker in (
        "payin_goto_start",
        "payin_goto_done",
        "payin_networkidle_done",
        "payin_calendar_open",
        "payin_apply_done",
        "payin_export_click",
        "payout_goto_start",
        "payout_goto_done",
        "payout_networkidle_done",
        "payout_calendar_open",
        "payout_apply_done",
        "payout_export_click",
    ):
        assert marker in text


def test_stage_logs_run_wallet_cycle_full_path(caplog: pytest.LogCaptureFixture) -> None:
    rendered = MagicMock(main_text="report", alerts_text="")

    with patch.object(dw, "LOGIN", "u"), patch.object(dw, "PASSWORD", "p"):
        with patch.object(dw, "_load_wallet_params", return_value=dw.WalletJobParams()):
            with patch.object(dw, "_download_wallet_files", return_value=("/tmp/a.xlsx", "/tmp/b.xlsx")):
                with patch.object(dw, "_calc_wallet_fingerprint", return_value="fp-new"):
                    with patch.object(dw, "state_get", return_value=None):
                        with patch.object(dw, "state_update"):
                            with patch.object(dw, "build_wallet_stats_dto", return_value=MagicMock()):
                                with patch.object(dw, "render_wallet", return_value=rendered):
                                    with patch.object(dw, "routes_from_rules_v2_enabled", return_value=False):
                                        with patch.object(dw, "send_text"):
                                            with caplog.at_level(logging.INFO, logger=_WALLET_LOGGER):
                                                dw.run_wallet_cycle()

    text = caplog.text
    for marker in (
        "wallet_analyze_start",
        "wallet_send_start",
        "wallet_cycle_done",
    ):
        assert marker in text


def test_stage_logs_download_wallet_files(caplog: pytest.LogCaptureFixture) -> None:
    page = MagicMock()
    page.url = "https://antares.plus/lkcard/#/payin"

    browser = MagicMock()
    context = MagicMock()
    context.new_page.return_value = page
    browser.new_context.return_value = context

    pw = MagicMock()
    pw.chromium.launch.return_value = browser

    with patch.object(dw, "sync_playwright") as sp:
        sp.return_value.__enter__.return_value = pw
        with patch.object(dw, "_ensure_logged_in"):
            with patch.object(dw, "_download_payin", return_value="/tmp/payin.xlsx"):
                with patch.object(dw, "_download_payout", return_value="/tmp/payout.xlsx"):
                    with caplog.at_level(logging.INFO, logger=_WALLET_LOGGER):
                        dw._download_wallet_files("12.00", dw.WalletJobParams())

    text = caplog.text
    assert "wallet_playwright_start" in text
    assert "wallet_playwright_done" in text


def test_playwright_timeout_propagates_from_download_payin() -> None:
    with _mock_download_context() as page:
        page.goto.side_effect = TimeoutError("Navigation timeout")
        with pytest.raises(TimeoutError, match="Navigation timeout"):
            dw._download_payin(page, "12.00", 0)


def test_playwright_timeout_propagates_from_run_wallet_cycle() -> None:
    with patch.object(dw, "LOGIN", "u"), patch.object(dw, "PASSWORD", "p"):
        with patch.object(dw, "_load_wallet_params", return_value=dw.WalletJobParams()):
            with patch.object(
                dw,
                "_download_wallet_files",
                side_effect=TimeoutError("networkidle timeout"),
            ):
                with pytest.raises(TimeoutError, match="networkidle timeout"):
                    dw.run_wallet_cycle()
