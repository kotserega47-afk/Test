"""Tests for in-memory scheduler health (Phase 1a)."""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from core.scheduler_health import (
    _reset_scheduler_health_for_tests,
    get_scheduler_health_snapshot,
    record_error,
    record_schedules_loaded,
    record_tick,
)


@pytest.fixture(autouse=True)
def _clean_health() -> None:
    _reset_scheduler_health_for_tests()
    yield
    _reset_scheduler_health_for_tests()


def test_health_before_first_tick() -> None:
    snap = get_scheduler_health_snapshot()
    assert snap["scheduler_last_tick_ts"] == "unknown"
    assert snap["scheduler_last_tick_age_sec"] == "unknown"
    assert snap["scheduler_last_error"] == "none"
    assert snap["scheduler_active_schedules_count"] == "unknown"


def test_record_tick_updates_age() -> None:
    record_tick()
    snap = get_scheduler_health_snapshot()
    assert isinstance(snap["scheduler_last_tick_ts"], float)
    assert isinstance(snap["scheduler_last_tick_age_sec"], float)
    assert snap["scheduler_last_tick_age_sec"] >= 0


def test_record_error_latches() -> None:
    record_error("load failed")
    snap = get_scheduler_health_snapshot()
    assert snap["scheduler_last_error"] == "load failed"


def test_record_schedules_loaded_clears_error_and_sets_count() -> None:
    record_error("x")
    record_schedules_loaded(4)
    snap = get_scheduler_health_snapshot()
    assert snap["scheduler_last_error"] == "none"
    assert snap["scheduler_active_schedules_count"] == 4


def test_record_tick_no_file_io() -> None:
    with patch("builtins.open", side_effect=AssertionError("no file I/O")):
        record_tick()
    snap = get_scheduler_health_snapshot()
    assert isinstance(snap["scheduler_last_tick_ts"], float)


def test_concurrent_read_write() -> None:
    errors: list[Exception] = []

    def writer() -> None:
        try:
            for _ in range(200):
                record_tick()
                record_error("e")
                record_schedules_loaded(1)
        except Exception as e:
            errors.append(e)

    def reader() -> None:
        try:
            for _ in range(200):
                get_scheduler_health_snapshot()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors
