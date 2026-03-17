# core/schedules.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from core.rules_provider import get_snapshot_v2
from core.rules_v2.accessors import ScheduleRulesAccessor
from core.rules_v2.indexes import build_indexes


@dataclass(frozen=True)
class Schedule:
    id: str
    enabled: int
    job_type: str
    schedule_type: str  # every_seconds|cron
    every_seconds: int
    cron: str
    jitter_sec: int
    max_runtime_sec: int
    coalesce: int


def load_schedules(*, force_sync: bool = False) -> List[Schedule]:
    snapshot = get_snapshot_v2(force_sync=...)
    indexes = build_indexes(snapshot)
    accessor = ScheduleRulesAccessor(snapshot, indexes)

    out: List[Schedule] = []

    for rule in accessor.get_enabled_schedules():
        stype = str(rule.schedule_type or "").strip().lower()

        if stype == "interval":
            every_seconds = int(rule.every_seconds or 0)
            cron = ""
        elif stype == "cron":
            every_seconds = 0
            cron = str(rule.cron_expr or "").strip()
        else:
            continue

        if not rule.job_key:
            continue
        if stype == "interval" and every_seconds <= 0:
            continue
        if stype == "cron" and not cron:
            continue

        out.append(
            Schedule(
                id=str(rule.schedule_key or "").strip(),
                enabled=1,
                job_type=str(rule.job_key).strip(),
                schedule_type="every_seconds" if stype == "interval" else "cron",
                every_seconds=every_seconds,
                cron=cron,
                jitter_sec=0,
                max_runtime_sec=0,
                coalesce=1 if bool(rule.coalesce) else 0,
            )
        )

    return out