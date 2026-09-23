"""Isolated HourlyGate peek/commit. Mixed scheduler.HourlyGate is not imported or mutated here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from core.config_manager import get_job_params


def _parse_hhmm(s: str) -> Optional[Tuple[int, int]]:
    s = (s or "").strip()
    if not s:
        return None
    if ":" not in s:
        return None
    hh_s, mm_s = s.split(":", 1)
    try:
        hh = int(hh_s)
        mm = int(mm_s)
    except Exception:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


@dataclass
class HourlyGate:
    last_intraday_key: Optional[str] = None  # YYYYMMDD-HHMM bucket
    last_final_key: Optional[str] = None  # YYYYMMDD final date key


@dataclass(frozen=True)
class HourlyGatePeek:
    should_fire: bool
    reason: str
    candidate_intraday_key: Optional[str] = None
    candidate_final_key: Optional[str] = None


def peek_hourly_gate(now: datetime, gate: HourlyGate) -> HourlyGatePeek:
    """I/O via get_job_params. Does not write gate.last_*."""
    params = get_job_params(job="hourly")

    intraday_min = int(params.get("intraday_interval_minutes") or 0)
    final_time = _parse_hhmm(str(params.get("final_daily_time") or ""))

    if intraday_min <= 0 and final_time is None:
        return HourlyGatePeek(
            False,
            (
                "no_gate_config: set job_params intraday_interval_minutes or "
                "final_daily_time for job=hourly"
            ),
        )

    fired = False
    fire_parts: list[str] = []
    candidate_intraday: Optional[str] = None
    candidate_final: Optional[str] = None

    if intraday_min > 0:
        total_min = now.hour * 60 + now.minute
        bucket = total_min // intraday_min
        key = f"{now:%Y%m%d}-{bucket:04d}"
        if gate.last_intraday_key != key:
            candidate_intraday = key
            fired = True
            fire_parts.append(f"intraday_interval_minutes={intraday_min} bucket={key}")

    if final_time is not None:
        hh, mm = final_time
        if now.hour == hh and now.minute == mm:
            key = f"{now:%Y%m%d}"
            if gate.last_final_key != key:
                candidate_final = key
                fired = True
                fire_parts.append(f"final_daily_time={hh:02d}:{mm:02d}")

    if fired:
        return HourlyGatePeek(
            True,
            "; ".join(fire_parts),
            candidate_intraday,
            candidate_final,
        )

    if intraday_min > 0:
        total_min = now.hour * 60 + now.minute
        bucket = total_min // intraday_min
        key = f"{now:%Y%m%d}-{bucket:04d}"
        if gate.last_intraday_key == key:
            return HourlyGatePeek(False, f"intraday_already_fired bucket={key}")
        return HourlyGatePeek(
            False,
            f"intraday_waiting bucket={key} interval={intraday_min}m",
        )

    hh, mm = final_time  # type: ignore[misc]
    return HourlyGatePeek(
        False,
        (
            f"final_not_due now={now.hour:02d}:{now.minute:02d} "
            f"target={hh:02d}:{mm:02d}"
        ),
    )


def commit_hourly_gate(gate: HourlyGate, peek: HourlyGatePeek) -> None:
    if peek.candidate_intraday_key is not None:
        gate.last_intraday_key = peek.candidate_intraday_key
    if peek.candidate_final_key is not None:
        gate.last_final_key = peek.candidate_final_key
