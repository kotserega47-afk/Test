from datetime import datetime, date
from zoneinfo import ZoneInfo

import analyzers.hourly_report as hr

TZ = ZoneInfo("Europe/Moscow")


class FixedDateTime(datetime):
    fixed_now = None

    @classmethod
    def now(cls, tz=None):
        if cls.fixed_now is None:
            raise RuntimeError("FixedDateTime.fixed_now not set")
        if tz is None:
            return cls.fixed_now.replace(tzinfo=None)
        return cls.fixed_now.astimezone(tz)


def set_now(monkeypatch, fixed_now: datetime):
    FixedDateTime.fixed_now = fixed_now
    monkeypatch.setattr(hr, "datetime", FixedDateTime, raising=True)


def test_0002_returns_yesterday_full_day(monkeypatch):
    set_now(monkeypatch, datetime(2026, 2, 18, 0, 0, tzinfo=TZ))
    start, end, header_date = hr.get_time_window()

    assert header_date == date(2026, 2, 17)
    assert start == datetime(2026, 2, 17, 0, 0, tzinfo=TZ)
    assert end == datetime(2026, 2, 17, 23, 59, tzinfo=TZ)


def test_0810_returns_today_0_to_current_hour(monkeypatch):
    set_now(monkeypatch, datetime(2026, 2, 18, 8, 10, tzinfo=TZ))
    start, end, header_date = hr.get_time_window()

    assert header_date == date(2026, 2, 18)
    assert start == datetime(2026, 2, 18, 0, 0, tzinfo=TZ)
    assert end == datetime(2026, 2, 18, 8, 0, tzinfo=TZ)


def test_2359_returns_today_0_to_23(monkeypatch):
    set_now(monkeypatch, datetime(2026, 2, 18, 23, 59, tzinfo=TZ))
    start, end, header_date = hr.get_time_window()

    assert header_date == date(2026, 2, 18)
    assert start == datetime(2026, 2, 18, 0, 0, tzinfo=TZ)
    assert end == datetime(2026, 2, 18, 23, 0, tzinfo=TZ)
