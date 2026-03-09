from __future__ import annotations

import pandas as pd

from utils.normalization import parse_dt_series_msk


def test_parse_dt_series_msk_parses_excel_datetime_text():
    s = pd.Series(["07.03.2026 21:25:00"])
    parsed = parse_dt_series_msk(s)

    assert parsed.notna().sum() == 1
    assert parsed.iloc[0].year == 2026
    assert parsed.iloc[0].month == 3
    assert parsed.iloc[0].day == 7
    assert parsed.iloc[0].hour == 21
    assert parsed.iloc[0].minute == 25
    assert str(parsed.dt.tz) == "Europe/Moscow"


def test_parse_dt_series_msk_supports_missing_seconds():
    s = pd.Series(["07.03.2026 21:25"])
    parsed = parse_dt_series_msk(s)

    assert parsed.notna().sum() == 1
    assert parsed.iloc[0].hour == 21
    assert parsed.iloc[0].minute == 25
    assert str(parsed.dt.tz) == "Europe/Moscow"


def test_parse_dt_series_msk_handles_empty_values():
    s = pd.Series(["", None, "nan"])
    parsed = parse_dt_series_msk(s)

    assert parsed.isna().all()