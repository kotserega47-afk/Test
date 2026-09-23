"""Isolated Antares schedule tick. Does not import mixed scheduler.py. Caller drives ticks sequentially."""

from __future__ import annotations

import logging
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime

from core.job_runner import Actor
from core.scheduler_clocks_control import _apply_scheduler_clock_reset_if_requested
from core.schedules import Schedule
from modules.antares.assembly import ANTARES_ASSEMBLY_JOB_TYPES
from modules.antares.hourly_gate import HourlyGate, HourlyGatePeek, commit_hourly_gate, peek_hourly_gate
from modules.antares.schedule_timing import _next_cron_run
from modules.antares.work_admission import (
    AdmissionAccepted,
    AdmissionRejected,
    AdmissionState,
    WorkAdmission,
)

log = logging.getLogger(__name__)

_INTERVAL = "every_seconds"
_CRON = "cron"


@dataclass
class IsolatedScheduleState:
    next_every: dict[str, float] = field(default_factory=dict)
    next_cron: dict[str, datetime] = field(default_factory=dict)
    hourly_gate: HourlyGate = field(default_factory=HourlyGate)


@dataclass
class TickResult:
    accepted: list[AdmissionAccepted] = field(default_factory=list)
    rejected: list[AdmissionRejected] = field(default_factory=list)
    submit_exceptions: list[BaseException] = field(default_factory=list)


def _observe_accepted_future(future: Future, job_type: str) -> None:
    def _on_done(fut: Future) -> None:
        try:
            fut.result()
        except Exception:
            log.exception("scheduled job failed: job_type=%s", job_type)

    future.add_done_callback(_on_done)


def _prune_inactive(state: IsolatedScheduleState, schedules: list[Schedule]) -> None:
    active = {
        s.job_type
        for s in schedules
        if s.job_type in ANTARES_ASSEMBLY_JOB_TYPES
    }
    for k in list(state.next_every.keys()):
        if k not in active:
            state.next_every.pop(k, None)
    for k in list(state.next_cron.keys()):
        if k not in active:
            state.next_cron.pop(k, None)


def _admit(
    *,
    state: IsolatedScheduleState,
    admission: WorkAdmission,
    result: TickResult,
    attempted: set[tuple[str, str]],
    job_type: str,
    schedule_type: str,
    prepared_interval: float | None,
    prepared_cron: datetime | None,
    peek: HourlyGatePeek | None,
) -> None:
    clock = (job_type, schedule_type)
    if clock in attempted:
        return
    try:
        outcome = admission.submit_job_if_open(job_type, Actor(kind="scheduler"))
    except Exception as exc:
        attempted.add(clock)
        result.submit_exceptions.append(exc)
        log.exception("scheduled job submit failed: %s", job_type)
        return
    attempted.add(clock)
    if isinstance(outcome, AdmissionRejected):
        result.rejected.append(outcome)
        return
    if not isinstance(outcome, AdmissionAccepted):
        return
    if prepared_interval is not None:
        state.next_every[job_type] = prepared_interval
    if prepared_cron is not None:
        state.next_cron[job_type] = prepared_cron
    if peek is not None:
        commit_hourly_gate(state.hourly_gate, peek)
    result.accepted.append(outcome)
    try:
        _observe_accepted_future(outcome.future, job_type)
    except Exception:
        log.exception("scheduled job observer attach failed: job_type=%s", job_type)


def tick(
    state: IsolatedScheduleState,
    *,
    now_ts: float,
    now_dt: datetime,
    schedules: list[Schedule],
    admission: WorkAdmission,
) -> TickResult:
    """Apply reset, arm, peek, submit, commit. One state — sequential ticks only."""
    _apply_scheduler_clock_reset_if_requested(
        state.next_every,
        state.next_cron,
        logger=log,
    )
    _prune_inactive(state, schedules)
    result = TickResult()
    attempted: set[tuple[str, str]] = set()
    # Snapshot only: does not reserve admission. submit_job_if_open still decides.
    already_sealed = admission.state is AdmissionState.SEALED

    for schedule in schedules:
        jt = schedule.job_type
        if jt not in ANTARES_ASSEMBLY_JOB_TYPES:
            continue
        if already_sealed:
            continue

        if schedule.schedule_type == _INTERVAL:
            clock = (jt, _INTERVAL)
            if clock in attempted:
                continue
            ts_next = state.next_every.get(jt)
            if ts_next is None:
                state.next_every[jt] = now_ts + max(1, int(schedule.every_seconds))
                continue
            if now_ts < ts_next:
                continue
            prepared_interval = now_ts + max(1, int(schedule.every_seconds))
            peek: HourlyGatePeek | None = None
            if jt == "hourly":
                try:
                    peek = peek_hourly_gate(now_dt, state.hourly_gate)
                except Exception:
                    log.exception("hourly gate prepare failed")
                    continue
                if not peek.should_fire:
                    state.next_every[jt] = prepared_interval
                    continue
            _admit(
                state=state,
                admission=admission,
                result=result,
                attempted=attempted,
                job_type=jt,
                schedule_type=_INTERVAL,
                prepared_interval=prepared_interval,
                prepared_cron=None,
                peek=peek,
            )

        elif schedule.schedule_type == _CRON:
            clock = (jt, _CRON)
            if clock in attempted:
                continue
            dt_next = state.next_cron.get(jt)
            if dt_next is None:
                try:
                    state.next_cron[jt] = _next_cron_run(now_dt, schedule.cron)
                except Exception:
                    log.warning("bad cron for %s: %s", jt, schedule.cron, exc_info=True)
                continue
            if now_dt < dt_next:
                continue
            try:
                prepared_cron = _next_cron_run(now_dt, schedule.cron)
            except Exception:
                log.warning(
                    "cron prepare failed for %s: %s",
                    jt,
                    schedule.cron,
                    exc_info=True,
                )
                continue
            peek = None
            if jt == "hourly":
                try:
                    peek = peek_hourly_gate(now_dt, state.hourly_gate)
                except Exception:
                    log.exception("hourly gate prepare failed")
                    continue
                if not peek.should_fire:
                    state.next_cron[jt] = prepared_cron
                    continue
            _admit(
                state=state,
                admission=admission,
                result=result,
                attempted=attempted,
                job_type=jt,
                schedule_type=_CRON,
                prepared_interval=None,
                prepared_cron=prepared_cron,
                peek=peek,
            )

    return result
