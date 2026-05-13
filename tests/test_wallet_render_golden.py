"""Golden: WalletStatsDTO + synthetic ui_layout DataFrame → render_wallet (render contract only)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from analyzers.wallet_analyzer import (
    WalletAlertBlock,
    WalletPartnerStats,
    WalletStatsDTO,
)
from reporters.wallet_reporter import render_wallet

MSK = ZoneInfo("Europe/Moscow")

_MAIN = Path(__file__).resolve().parent / "fixtures" / "wallet" / "golden" / "expected_wallet_main.txt"
_ALERTS = Path(__file__).resolve().parent / "fixtures" / "wallet" / "golden" / "expected_wallet_alerts.txt"


def _norm_crlf(s: str) -> str:
    return s.replace("\r\n", "\n")


def _layout_df() -> pd.DataFrame:
    rows = [
        (1, "wallet", "stuck", "stuck.title", "text", 10),
        (1, "wallet", "stuck", "stuck.items", "block", 20),
        (1, "wallet", "stuck", "stuck.spacer", "blank", 30),
        (1, "wallet", "header", "header.title", "text", 40),
        (1, "wallet", "header", "header.window", "text", 50),
        (1, "wallet", "header", "header.spacer", "blank", 60),
        (1, "wallet", "partners", "partners.blocks", "block", 70),
        (1, "wallet", "partners", "partners.spacer", "blank", 80),
        (1, "wallet", "alerts", "alerts.title", "text", 100),
        (1, "wallet", "alerts", "alerts.blocks", "block", 110),
    ]
    return pd.DataFrame(
        rows,
        columns=["enabled", "view", "section", "key", "style", "order"],
    )


def _dto() -> WalletStatsDTO:
    last_ok = datetime(2026, 1, 15, 14, 30, 0, tzinfo=MSK)
    gen_at = datetime(2026, 1, 15, 15, 12, 0, tzinfo=MSK)
    partner = WalletPartnerStats(
        partner="Partner Alpha",
        group="G-Aurora",
        total_ops=20,
        success_ops=16,
        conversion_pct=80.0,
        conversion_threshold_pct=75.0,
        conversion_insufficient=False,
        conversion_bad=False,
        payin_amount=12345.0,
        daily_limit=50000.0,
        daily_limit_comment="daily cap note",
        percent_filled=25,
        limit_bad=False,
        limit_warn=True,
        api_cancel_count=1,
        api_total_ops=50,
        api_cancel_pct=2.0,
        api_cancel_threshold_pct=5.0,
        api_insufficient=False,
        api_bad=True,
        nok_wallets_count=2,
        last_success_at=last_ok,
        last_success_minutes_ago=42,
    )
    alert = WalletAlertBlock(
        partner="Alert Partner X",
        lines=[
            "Threshold breach on conversion",
            "Gate delayed",
        ],
    )
    return WalletStatsDTO(
        report_day=date(2026, 1, 15),
        generated_at=gen_at,
        window_minutes=90,
        offset_minutes=7,
        min_events=30,
        stuck_payins_count=2,
        stuck_payouts_count=3,
        partners=[partner],
        alerts=[alert],
    )


def test_wallet_render_golden_main_and_alerts() -> None:
    dto = _dto()
    layout = _layout_df()
    report = render_wallet(dto, layout_df=layout)

    expected_main = _norm_crlf(_MAIN.read_text(encoding="utf-8"))
    expected_alerts = _norm_crlf(_ALERTS.read_text(encoding="utf-8"))

    assert _norm_crlf(report.main_text) == expected_main
    assert _norm_crlf(report.alerts_text) == expected_alerts
