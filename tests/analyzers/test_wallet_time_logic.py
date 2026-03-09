from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from analyzers.wallet_analyzer import _calc_last_success_minutes_ago


MSK = ZoneInfo("Europe/Moscow")


def test_calc_last_success_minutes_ago_returns_none_for_missing_value():
    now = datetime(2026, 3, 7, 21, 30, 0, tzinfo=MSK)

    assert _calc_last_success_minutes_ago(now, None) is None


def test_calc_last_success_minutes_ago_uses_passed_now():
    now = datetime(2026, 3, 7, 21, 30, 0, tzinfo=MSK)
    last_success_at = datetime(2026, 3, 7, 21, 0, 0, tzinfo=MSK)

    assert _calc_last_success_minutes_ago(now, last_success_at) == 30


def test_calc_last_success_minutes_ago_same_minute_floor():
    now = datetime(2026, 3, 7, 21, 30, 0, tzinfo=MSK)
    last_success_at = datetime(2026, 3, 7, 21, 29, 31, tzinfo=MSK)

    assert _calc_last_success_minutes_ago(now, last_success_at) == 0