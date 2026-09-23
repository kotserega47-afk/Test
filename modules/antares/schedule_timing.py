"""Isolated cron parse/next. Formulas copied from mixed scheduler.py; that module is not imported."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple


def _parse_cron_min_hour(expr: str) -> Tuple[int, Optional[int]]:
    """
    Поддержка:
      - "M H * * *" где M=0..59, H=0..23 или '*'
    Пример:
      - "0 * * * *"  -> каждый час в :00
      - "5 2 * * *"  -> каждый день в 02:05
    """
    parts = (expr or "").strip().split()
    if len(parts) != 5:
        raise ValueError("cron must have 5 parts: 'M H * * *'")

    m_s, h_s, _, _, _ = parts

    if m_s == "*":
        raise ValueError("cron minute '*' not supported; use explicit minute 0..59")
    minute = int(m_s)

    hour_any = (h_s == "*")
    hour = None if hour_any else int(h_s)

    if not (0 <= minute <= 59):
        raise ValueError("cron minute out of range 0..59")
    if hour is not None and not (0 <= hour <= 23):
        raise ValueError("cron hour out of range 0..23")

    return minute, hour  # hour=None means '*'


def _next_cron_run(now: datetime, cron_expr: str) -> datetime:
    minute, hour = _parse_cron_min_hour(cron_expr)

    if hour is None:
        # every hour at :minute
        target = now.replace(minute=minute, second=5, microsecond=0)
        if target <= now:
            target = target + timedelta(hours=1)
        return target

    # every day at hour:minute
    target = now.replace(hour=hour, minute=minute, second=5, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return target
