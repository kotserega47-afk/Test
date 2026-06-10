"""Hourly scheduler gate — bucket alignment and observability."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import scheduler
from core.scheduler_health import (
    _reset_scheduler_health_for_tests,
    get_scheduler_health_snapshot,
)
from scheduler import HourlyGate, evaluate_hourly_gate

MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture(autouse=True)
def _clean_gate_state() -> None:
    scheduler._reset_hourly_gate_skip_throttle_for_tests()
    _reset_scheduler_health_for_tests()
    yield
    scheduler._reset_hourly_gate_skip_throttle_for_tests()
    _reset_scheduler_health_for_tests()


def test_no_gate_config_blocks_with_reason() -> None:
    gate = HourlyGate()
    now = datetime(2026, 6, 7, 10, 5, tzinfo=MSK)
    with patch("scheduler.get_job_params", return_value={}):
        fired, reason = evaluate_hourly_gate(now, gate)
    assert fired is False
    assert "no_gate_config" in reason


def test_intraday_fires_on_first_tick_in_bucket_not_only_on_minute_zero() -> None:
    """Regression: old gate required minute % interval == 0 and blocked schedule at :05."""
    gate = HourlyGate()
    now = datetime(2026, 6, 7, 10, 5, tzinfo=MSK)
    params = {"intraday_interval_minutes": 15}
    with patch("scheduler.get_job_params", return_value=params):
        fired, reason = evaluate_hourly_gate(now, gate)
    assert fired is True
    assert "intraday_interval_minutes=15" in reason
    assert gate.last_intraday_key == "20260607-0040"


def test_intraday_dedup_within_same_bucket() -> None:
    gate = HourlyGate()
    now = datetime(2026, 6, 7, 10, 7, tzinfo=MSK)
    params = {"intraday_interval_minutes": 15}
    with patch("scheduler.get_job_params", return_value=params):
        assert evaluate_hourly_gate(now, gate)[0] is True
        fired, reason = evaluate_hourly_gate(now, gate)
    assert fired is False
    assert "intraday_already_fired" in reason


def test_intraday_60_fires_at_minute_five_in_new_hour_bucket() -> None:
    gate = HourlyGate()
    now = datetime(2026, 6, 7, 11, 5, tzinfo=MSK)
    params = {"intraday_interval_minutes": 60}
    with patch("scheduler.get_job_params", return_value=params):
        fired, reason = evaluate_hourly_gate(now, gate)
    assert fired is True
    assert gate.last_intraday_key == "20260607-0011"


def test_final_daily_fires_once_per_day() -> None:
    gate = HourlyGate()
    params = {"final_daily_time": "02:30"}
    now = datetime(2026, 6, 7, 2, 30, tzinfo=MSK)
    with patch("scheduler.get_job_params", return_value=params):
        assert evaluate_hourly_gate(now, gate)[0] is True
        assert evaluate_hourly_gate(now, gate)[0] is False


def test_apply_hourly_gate_emits_skip_event(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict] = []
    monkeypatch.setattr(
        "scheduler.append_event",
        lambda **kwargs: events.append(kwargs),
    )
    gate = HourlyGate()
    now = datetime(2026, 6, 7, 10, 5, tzinfo=MSK)
    with patch("scheduler.get_job_params", return_value={}):
        assert scheduler._apply_hourly_gate(now, gate) is False
    assert events
    assert events[0]["type"] == "job_gate_skipped"
    assert events[0]["job_type"] == "hourly"
    assert "no_gate_config" in events[0]["payload"]["reason"]

    health = get_scheduler_health_snapshot()
    assert "no_gate_config" in str(health["hourly_gate_last_skip_reason"])


def test_apply_hourly_gate_records_fire_reason() -> None:
    gate = HourlyGate()
    now = datetime(2026, 6, 7, 10, 0, tzinfo=MSK)
    with patch("scheduler.get_job_params", return_value={"intraday_interval_minutes": 30}):
        assert scheduler._apply_hourly_gate(now, gate) is True
    health = get_scheduler_health_snapshot()
    assert "intraday_interval_minutes=30" in str(health["hourly_gate_last_fire_reason"])


def test_raccoon_job_type_not_gated_in_schedule_loop_source() -> None:
    src = open(scheduler.__file__, encoding="utf-8").read()
    assert '_apply_hourly_gate' in src
    assert 'if jt == "hourly":' in src
    assert 'if jt == "raccoon_hourly":' not in src
