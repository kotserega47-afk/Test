# core/schedules.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pandas as pd

from core.rules_provider import get_rules_snapshot


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


def _i(v, default=0) -> int:
    try:
        if pd.isna(v):
            return default
    except Exception:
        pass
    try:
        return int(float(v))
    except Exception:
        return default


def load_schedules(*, force_sync: bool = False) -> List[Schedule]:
    rs = get_rules_snapshot(force_sync=force_sync)
    df = pd.read_excel(rs.local_path, sheet_name="schedules", engine="openpyxl")
    if df is None or df.empty:
        return []

    df.columns = [str(c).strip().lower() for c in df.columns]

    # minimal required
    for c in ("id", "enabled", "job_type", "schedule_type"):
        if c not in df.columns:
            raise ValueError(f"[schedules] missing column: {c}")

    out: List[Schedule] = []
    for _, r in df.iterrows():
        enabled = _i(r.get("enabled"), 0)
        if enabled != 1:
            continue

        job_type = str(r.get("job_type") or "").strip()
        stype = str(r.get("schedule_type") or "").strip().lower()
        every = _i(r.get("every_seconds"), 0)
        cron = str(r.get("cron") or "").strip()

        if not job_type:
            continue
        if stype not in {"every_seconds", "cron"}:
            continue
        if stype == "every_seconds" and every <= 0:
            continue
        if stype == "cron" and not cron:
            continue

        # optional columns (default 0 if absent)
        out.append(
            Schedule(
                id=str(r.get("id") or "").strip(),
                enabled=1,
                job_type=job_type,
                schedule_type=stype,
                every_seconds=every,
                cron=cron,
                jitter_sec=_i(r.get("jitter_sec"), 0),
                max_runtime_sec=_i(r.get("max_runtime_sec"), 0),
                coalesce=_i(r.get("coalesce"), 0),
            )
        )

    return out