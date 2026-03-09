from core.datetime_utils import (
    format_msk_datetime,
    parse_msk_datetime,
    parse_time_value,
    start_of_day_msk,
)


def test_parse_msk_datetime():
    dt = parse_msk_datetime("07.03.2026 21:25:00")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 3
    assert dt.day == 7
    assert dt.hour == 21
    assert dt.minute == 25
    assert str(dt.tzinfo) == "Europe/Moscow"


def test_format_msk_datetime():
    dt = parse_msk_datetime("07.03.2026 21:25:00")
    assert format_msk_datetime(dt) == "07.03.2026 21:25:00"


def test_parse_time_value_hh_mm():
    t = parse_time_value("23:59")
    assert t is not None
    assert t.hour == 23
    assert t.minute == 59


def test_start_of_day_msk():
    dt = parse_msk_datetime("07.03.2026 21:25:00")
    sod = start_of_day_msk(dt)
    assert sod.hour == 0
    assert sod.minute == 0
    assert sod.second == 0
    assert str(sod.tzinfo) == "Europe/Moscow"