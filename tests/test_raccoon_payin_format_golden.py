"""Independent golden for Raccoon payin-by-method formatting (Test SHA).

Expected text was written by hand from the synthetic rows (sort by method
total desc, then partner amount desc, fmt_int thousands). The test must not
rebuild expected via format_report.

Platform compare is tests/compare_platform_raccoon_payin.py and is not
collected unless pytest is given --platform-checkout PATH.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from analyzers.raccoon_hourly_report import aggregate_payin_by_method, format_report
from utils.normalization import normalize_partner_name

MSK = ZoneInfo("Europe/Moscow")
_GOLDEN = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "raccoon"
    / "golden"
    / "expected_payin_by_method.txt"
)


def _synth_df() -> pd.DataFrame:
    rows = [
        {"Партнер": "Alpha (1)", "Сумма": 100000, "method_display": "SBP"},
        {"Партнер": "Beta (2)", "Сумма": 50000, "method_display": "SBP"},
        {"Партнер": "Alpha (1)", "Сумма": 25000, "method_display": "Card"},
    ]
    out = []
    for r in rows:
        out.append(
            {
                "Партнер": r["Партнер"],
                "norm": normalize_partner_name(r["Партнер"]),
                "Сумма": r["Сумма"],
                "method_display": r["method_display"],
                "Дата/Время создания": datetime(2026, 5, 20, 12, 0, tzinfo=MSK),
            }
        )
    return pd.DataFrame(out)


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip() + "\n"


def test_raccoon_payin_format_matches_handwritten_golden() -> None:
    df = _synth_df()
    blocks = aggregate_payin_by_method(df)
    actual = format_report(
        blocks,
        date(2026, 5, 20),
        datetime(2026, 5, 20, 14, 30, tzinfo=MSK),
        float(df["Сумма"].sum()),
    )
    expected = _GOLDEN.read_text(encoding="utf-8")
    assert _norm(actual) == _norm(expected)
