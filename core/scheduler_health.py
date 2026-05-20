# core/scheduler_health.py
"""In-memory scheduler health for observation-only /status (Phase 1a)."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Union

_lock = threading.Lock()
_last_tick_ts: Optional[float] = None
_last_error: Optional[str] = None
_active_schedules_count: Optional[int] = None

SnapshotValue = Union[float, int, str]


def record_tick() -> None:
    try:
        ts = time.time()
        with _lock:
            global _last_tick_ts
            _last_tick_ts = ts
    except Exception:
        return


def record_error(error: str) -> None:
    try:
        with _lock:
            global _last_error
            _last_error = str(error)
    except Exception:
        return


def record_schedules_loaded(count: int) -> None:
    try:
        with _lock:
            global _last_error, _active_schedules_count
            _last_error = None
            _active_schedules_count = int(count)
    except Exception:
        return


def get_scheduler_health_snapshot() -> Dict[str, SnapshotValue]:
    try:
        now = time.time()
        with _lock:
            tick_ts = _last_tick_ts
            last_error = _last_error
            active_count = _active_schedules_count

        if tick_ts is None:
            tick_ts_out: SnapshotValue = "unknown"
            tick_age_out: SnapshotValue = "unknown"
        else:
            tick_ts_out = tick_ts
            tick_age_out = round(now - tick_ts, 1)

        if last_error is None:
            error_out: SnapshotValue = "none"
        else:
            error_out = last_error

        if active_count is None:
            count_out: SnapshotValue = "unknown"
        else:
            count_out = active_count

        return {
            "scheduler_last_tick_ts": tick_ts_out,
            "scheduler_last_tick_age_sec": tick_age_out,
            "scheduler_last_error": error_out,
            "scheduler_active_schedules_count": count_out,
        }
    except Exception:
        return {
            "scheduler_last_tick_ts": "unknown",
            "scheduler_last_tick_age_sec": "unknown",
            "scheduler_last_error": "unknown",
            "scheduler_active_schedules_count": "unknown",
        }


def _reset_scheduler_health_for_tests() -> None:
    """Test helper: clear in-memory scheduler health state."""
    global _last_tick_ts, _last_error, _active_schedules_count
    with _lock:
        _last_tick_ts = None
        _last_error = None
        _active_schedules_count = None
