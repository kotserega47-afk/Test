from __future__ import annotations

from datetime import datetime

import pandas as pd

from core.datetime_utils import (
    format_msk_datetime,
    parse_msk_datetime,
    parse_msk_series,
    parse_time_value,
    start_of_day_msk,
)


def test_parse_msk_datetime_strict_format():
    dt = parse_msk_datetime("07.03.2026 21:25:00")

    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 3
    assert dt.day == 7
    assert dt.hour == 21
    assert dt.minute == 25
    assert dt.second == 0
    assert str(dt.tzinfo) == "Europe/Moscow"


def test_format_msk_datetime_roundtrip():
    dt = parse_msk_datetime("07.03.2026 21:25:00")

    assert format_msk_datetime(dt) == "07.03.2026 21:25:00"


def test_parse_time_value_hh_mm():
    t = parse_time_value("23:59")

    assert t is not None
    assert t.hour == 23
    assert t.minute == 59
    assert t.second == 0


def test_parse_time_value_hh_mm_ss():
    t = parse_time_value("23:59:58")

    assert t is not None
    assert t.hour == 23
    assert t.minute == 59
    assert t.second == 58


def test_start_of_day_msk():
    dt = parse_msk_datetime("07.03.2026 21:25:00")
    sod = start_of_day_msk(dt)

    assert sod.year == 2026
    assert sod.month == 3
    assert sod.day == 7
    assert sod.hour == 0
    assert sod.minute == 0
    assert sod.second == 0
    assert str(sod.tzinfo) == "Europe/Moscow"


def test_parse_msk_series_strict_and_fallback():
    s = pd.Series([
        "07.03.2026 21:25:00",
        "07.03.2026 21:25",
        "",
        None,
    ])

    parsed = parse_msk_series(s)

    assert parsed.notna().sum() == 2
    assert str(parsed.dt.tz) == "Europe/Moscow"

    assert parsed.iloc[0].year == 2026
    assert parsed.iloc[0].month == 3
    assert parsed.iloc[0].day == 7
    assert parsed.iloc[0].hour == 21
    assert parsed.iloc[0].minute == 25

    assert parsed.iloc[1].year == 2026
    assert parsed.iloc[1].month == 3
    assert parsed.iloc[1].day == 7
    assert parsed.iloc[1].hour == 21
    assert parsed.iloc[1].minute == 25

    assert pd.isna(parsed.iloc[2])
    assert pd.isna(parsed.iloc[3])