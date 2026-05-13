# core/scheduler_clocks_control.py
"""Cross-thread signal to clear scheduler in-memory interval/cron clocks.

Used after successful ``/reload_rules`` so ``next_every`` / ``next_cron`` in
``schedule_loop`` are recomputed from the current workbook without restarting
the process. Does not touch HourlyGate, job locks, or ``request_job``.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Dict

_SCHEDULER_CLOCKS_RESET_REQUESTED = threading.Event()
_last_reason_lock = threading.Lock()
_last_reason: str = "manual_reload"


def request_scheduler_clocks_reset(reason: str = "manual_reload") -> None:
    """Request clearing of ``next_every`` / ``next_cron`` on the next scheduler tick.

    Safe if ``schedule_loop`` is not running (event is consumed only in the loop).
    Idempotent for repeated calls: the event remains set until the loop applies it.
    """
    global _last_reason
    with _last_reason_lock:
        _last_reason = reason
    _SCHEDULER_CLOCKS_RESET_REQUESTED.set()


def _apply_scheduler_clock_reset_if_requested(
    next_every: Dict[str, float],
    next_cron: Dict[str, datetime],
    *,
    logger=None,
) -> bool:
    """If a reset was requested, clear interval/cron clocks and the event.

    Returns True if a reset was applied. Does not modify HourlyGate or any job state.
    """
    if not _SCHEDULER_CLOCKS_RESET_REQUESTED.is_set():
        return False

    next_every.clear()
    next_cron.clear()
    _SCHEDULER_CLOCKS_RESET_REQUESTED.clear()

    with _last_reason_lock:
        r = _last_reason

    if logger is not None:
        logger.info("scheduler_clocks_reset reason=%s", r)

    return True


def _reset_scheduler_clocks_state_for_tests() -> None:
    """Test helper: clear pending reset flag and default reason."""
    global _last_reason
    _SCHEDULER_CLOCKS_RESET_REQUESTED.clear()
    with _last_reason_lock:
        _last_reason = "manual_reload"
