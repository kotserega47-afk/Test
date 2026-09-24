"""TASK-36 isolated schedule admission (S1–S18, S7b/c/d, S12b)."""

from __future__ import annotations

import ast
import logging
import subprocess
import sys
import threading
from concurrent.futures import wait
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from core.job_dispatch import _reset_job_executor_for_tests, get_job_executor
from core.scheduler_clocks_control import (
    _reset_scheduler_clocks_state_for_tests,
    request_scheduler_clocks_reset,
)
from core.schedules import Schedule
from modules.antares.hourly_gate import HourlyGate, peek_hourly_gate
from modules.antares.schedule_timing import _next_cron_run, _parse_cron_min_hour
from modules.antares.scheduler import IsolatedScheduleState, tick
from modules.antares.work_admission import (
    AdmissionAccepted,
    AdmissionRejected,
    AdmissionState,
    WorkAdmission,
)
from tests.unit.isolated_child_env import child_subprocess_kwargs, isolated_child_env

MSK = ZoneInfo("Europe/Moscow")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_clocks_and_executor():
    _reset_scheduler_clocks_state_for_tests()
    _reset_job_executor_for_tests()
    yield
    _reset_scheduler_clocks_state_for_tests()
    _reset_job_executor_for_tests()


def _sched(
    job_type: str,
    schedule_type: str = "every_seconds",
    *,
    every_seconds: int = 60,
    cron: str = "",
    sid: str = "s1",
) -> Schedule:
    return Schedule(
        id=sid,
        enabled=1,
        job_type=job_type,
        schedule_type=schedule_type,
        every_seconds=every_seconds,
        cron=cron,
        jitter_sec=0,
        max_runtime_sec=0,
        coalesce=0,
    )


def _open() -> WorkAdmission:
    admission = WorkAdmission()
    admission._bind_instance()
    admission.open()
    return admission


def _closed() -> WorkAdmission:
    admission = WorkAdmission()
    admission._bind_instance()
    return admission


def _dt(hour: int = 10, minute: int = 5, day: int = 7) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=MSK)


@dataclass
class _JobStub:
    calls: list
    started: threading.Event
    release: threading.Event
    error: BaseException | None = None
    hold: bool = False

    def __call__(self, job_type, actor, *, force_rules_sync: bool = False):
        self.calls.append((job_type, actor.kind, force_rules_sync))
        self.started.set()
        if self.hold:
            if not self.release.wait(timeout=5):
                raise RuntimeError("job stub was not released")
        if self.error is not None:
            raise self.error
        return f"jid-{job_type}-{len(self.calls)}"


def _install_stub(monkeypatch, **kwargs) -> _JobStub:
    stub = _JobStub(
        calls=[],
        started=threading.Event(),
        release=threading.Event(),
        **kwargs,
    )
    monkeypatch.setattr("modules.antares.work_admission.request_job", stub)
    return stub


class _LogSeen(logging.Handler):
    """Set Event after a matching log record is actually emitted."""

    def __init__(self, event: threading.Event, needle: str) -> None:
        super().__init__(level=logging.ERROR)
        self._event = event
        self._needle = needle
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self._needle in record.getMessage():
                self.count += 1
                self._event.set()
        except Exception:
            pass


class _ObservingLock:
    def __init__(self, inner, *, holder, waiter, holder_acquired, waiter_blocked):
        self._inner = inner
        self._holder = holder
        self._waiter = waiter
        self.holder_acquired = holder_acquired
        self.waiter_blocked = waiter_blocked
        self.observer_error: BaseException | None = None
        self.hold_release_until_waiter = True

    def acquire(self, blocking=True, timeout=-1):
        me = threading.current_thread()
        if me is self._waiter and self._inner.locked():
            self.waiter_blocked.set()
        if timeout is None or timeout < 0:
            acquired = self._inner.acquire(blocking)
        else:
            acquired = self._inner.acquire(blocking, timeout)
        if acquired and me is self._holder:
            self.holder_acquired.set()
        return acquired

    def release(self):
        try:
            if (
                threading.current_thread() is self._holder
                and self.hold_release_until_waiter
            ):
                if not self.waiter_blocked.wait(timeout=5):
                    self.observer_error = AssertionError("waiter was not observed blocked on lock")
        finally:
            self._inner.release()

    def locked(self):
        return self._inner.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def _thread(name: str, fn, errors: dict) -> threading.Thread:
    def _run() -> None:
        try:
            fn()
        except BaseException as exc:
            errors[name] = exc

    return threading.Thread(target=_run, name=name)


def _join_threads(threads: list[threading.Thread], *, unlock=None, timeout: float = 15) -> None:
    try:
        for thread in threads:
            thread.join(timeout=timeout)
    finally:
        if unlock is not None:
            unlock()
        for thread in threads:
            thread.join(timeout=timeout)
    alive = [thread.name for thread in threads if thread.is_alive()]
    if alive:
        pytest.fail(f"threads still alive: {alive}")


def _raise_thread_errors(errors: dict, extra: BaseException | None = None) -> None:
    items = list(errors.items())
    if extra is not None:
        items.append(("observer", extra))
    if not items:
        return
    name, exc = items[0]
    raise AssertionError(f"worker thread {name} failed: {exc!r}") from exc


def _due_interval(state: IsolatedScheduleState, job_type: str = "wallet") -> IsolatedScheduleState:
    state.next_every[job_type] = 0.0
    return state


def _sealed() -> WorkAdmission:
    admission = WorkAdmission()
    admission._bind_instance()
    admission.seal()
    return admission


def _count_executor(monkeypatch) -> list:
    real = get_job_executor()
    hits: list = []

    class _Wrap:
        def submit(self, fn, *args, **kwargs):
            hits.append((fn, args, kwargs))
            return real.submit(fn, *args, **kwargs)

    monkeypatch.setattr("modules.antares.work_admission.get_job_executor", lambda: _Wrap())
    return hits


def test_s1_interval_due_open_one_submit(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    state = IsolatedScheduleState()
    now_ts = 100.0
    now_dt = _dt()
    tick(state, now_ts=now_ts, now_dt=now_dt, schedules=[_sched("wallet", every_seconds=30)], admission=admission)
    assert stub.calls == []
    assert state.next_every["wallet"] == 130.0
    result = tick(
        state,
        now_ts=130.0,
        now_dt=now_dt,
        schedules=[_sched("wallet", every_seconds=30)],
        admission=admission,
    )
    assert len(result.accepted) == 1
    assert isinstance(result.accepted[0], AdmissionAccepted)
    wait([result.accepted[0].future], timeout=5)
    assert stub.calls == [("wallet", "scheduler", False)]
    assert state.next_every["wallet"] == 160.0


def test_s2_cron_arm_then_due(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    state = IsolatedScheduleState()
    now_dt = _dt(hour=1, minute=0)
    schedules = [_sched("wallet", "cron", cron="0 2 * * *")]
    tick(state, now_ts=1.0, now_dt=now_dt, schedules=schedules, admission=admission)
    assert stub.calls == []
    expected = _next_cron_run(now_dt, "0 2 * * *")
    assert state.next_cron["wallet"] == expected
    due_dt = expected
    result = tick(state, now_ts=2.0, now_dt=due_dt, schedules=schedules, admission=admission)
    assert len(result.accepted) == 1
    wait([result.accepted[0].future], timeout=5)
    assert stub.calls == [("wallet", "scheduler", False)]
    assert state.next_cron["wallet"] == _next_cron_run(due_dt, "0 2 * * *")


def test_s3_foreign_key_no_admission_no_clocks(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    state = IsolatedScheduleState()
    tick(
        state,
        now_ts=10.0,
        now_dt=_dt(),
        schedules=[_sched("raccoon_hourly", every_seconds=1)],
        admission=admission,
    )
    assert stub.calls == []
    assert state.next_every == {}
    assert state.next_cron == {}


def test_s4_seal_before_submit_lock_barrier(monkeypatch) -> None:
    """OPEN at tick start; seal wins after hourly peek, before submit_job_if_open."""
    stub = _install_stub(monkeypatch)
    hits = _count_executor(monkeypatch)
    admission = _open()
    state = IsolatedScheduleState()
    state.next_every["hourly"] = 0.0
    holder_acquired = threading.Event()
    waiter_blocked = threading.Event()
    in_prepare = threading.Event()
    outcome: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def _params(*, job):
        in_prepare.set()
        assert holder_acquired.wait(timeout=5)
        return {"intraday_interval_minutes": 15}

    monkeypatch.setattr("modules.antares.hourly_gate.get_job_params", _params)

    def _seal() -> None:
        assert in_prepare.wait(timeout=5)
        admission.seal()

    def _tick() -> None:
        outcome["r"] = tick(
            state,
            now_ts=50.0,
            now_dt=_dt(10, 5),
            schedules=[_sched("hourly", every_seconds=10)],
            admission=admission,
        )

    tick_thread = _thread("sched-tick", _tick, errors)
    seal_thread = _thread("sched-seal", _seal, errors)
    observed = _ObservingLock(
        admission._lock,
        holder=seal_thread,
        waiter=tick_thread,
        holder_acquired=holder_acquired,
        waiter_blocked=waiter_blocked,
    )
    admission._lock = observed
    try:
        tick_thread.start()
        seal_thread.start()
        _join_threads([seal_thread, tick_thread])
    finally:
        stub.release.set()
    _raise_thread_errors(errors, observed.observer_error)
    result = outcome["r"]
    assert isinstance(result.rejected[0], AdmissionRejected)
    assert result.rejected[0].state is AdmissionState.SEALED
    assert stub.calls == []
    assert hits == []
    assert state.next_every["hourly"] == 0.0
    assert state.hourly_gate.last_intraday_key is None


def test_s5_submit_before_seal_future_lives(monkeypatch) -> None:
    stub = _install_stub(monkeypatch, hold=True)
    admission = _open()
    state = _due_interval(IsolatedScheduleState())
    holder_acquired = threading.Event()
    waiter_blocked = threading.Event()
    in_submit = threading.Event()
    outcome: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    real = get_job_executor()

    class _HoldSubmit:
        def submit(self, fn, *args, **kwargs):
            assert admission._lock.locked()
            in_submit.set()
            waiter_blocked.wait(timeout=5)
            return real.submit(fn, *args, **kwargs)

    monkeypatch.setattr("modules.antares.work_admission.get_job_executor", lambda: _HoldSubmit())

    def _tick() -> None:
        outcome["r"] = tick(
            state,
            now_ts=50.0,
            now_dt=_dt(),
            schedules=[_sched("wallet", every_seconds=10)],
            admission=admission,
        )

    def _seal() -> None:
        assert in_submit.wait(timeout=5)
        admission.seal()

    tick_thread = _thread("sched-tick", _tick, errors)
    seal_thread = _thread("sched-seal", _seal, errors)
    observed = _ObservingLock(
        admission._lock,
        holder=tick_thread,
        waiter=seal_thread,
        holder_acquired=holder_acquired,
        waiter_blocked=waiter_blocked,
    )
    observed.hold_release_until_waiter = False
    admission._lock = observed
    try:
        tick_thread.start()
        seal_thread.start()
        _join_threads([tick_thread, seal_thread], unlock=stub.release.set)
    finally:
        stub.release.set()
    _raise_thread_errors(errors, observed.observer_error)
    result = outcome["r"]
    fut = result.accepted[0].future
    assert not fut.cancelled()
    assert fut.result(timeout=5) == "jid-wallet-1"
    assert admission.state is AdmissionState.SEALED


def test_sealed_empty_clocks_interval_and_cron_no_arm(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    hits = _count_executor(monkeypatch)
    admission = _sealed()
    state = IsolatedScheduleState()
    result = tick(
        state,
        now_ts=10.0,
        now_dt=_dt(hour=1, minute=0),
        schedules=[
            _sched("wallet", every_seconds=30, sid="i"),
            _sched("rate", "cron", cron="0 2 * * *", sid="c"),
        ],
        admission=admission,
    )
    assert result.accepted == []
    assert result.rejected == []
    assert stub.calls == []
    assert hits == []
    assert state.next_every == {}
    assert state.next_cron == {}


def test_sealed_due_hourly_gate_skip_does_not_consume(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    hits = _count_executor(monkeypatch)
    admission = _sealed()
    now_dt = _dt(10, 7)
    state = IsolatedScheduleState()
    state.next_every["hourly"] = 0.0
    state.next_cron["hourly"] = now_dt
    state.hourly_gate.last_intraday_key = "20260607-0040"
    state.hourly_gate.last_final_key = "20260606"
    with patch("modules.antares.hourly_gate.get_job_params", return_value={"intraday_interval_minutes": 15}):
        interval = tick(
            state,
            now_ts=50.0,
            now_dt=now_dt,
            schedules=[_sched("hourly", every_seconds=10, sid="i")],
            admission=admission,
        )
        cron = tick(
            state,
            now_ts=50.0,
            now_dt=now_dt,
            schedules=[_sched("hourly", "cron", cron="7 10 * * *", sid="c")],
            admission=admission,
        )
    assert interval.accepted == [] and interval.rejected == []
    assert cron.accepted == [] and cron.rejected == []
    assert stub.calls == []
    assert hits == []
    assert state.next_every["hourly"] == 0.0
    assert state.next_cron["hourly"] == now_dt
    assert state.hourly_gate.last_intraday_key == "20260607-0040"
    assert state.hourly_gate.last_final_key == "20260606"


def test_sealed_reset_clocks_without_arm(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    hits = _count_executor(monkeypatch)
    admission = _sealed()
    state = IsolatedScheduleState()
    state.next_every["wallet"] = 99.0
    state.next_cron["rate"] = _dt()
    state.hourly_gate.last_intraday_key = "keep-me"
    request_scheduler_clocks_reset("sealed-reset")
    tick(
        state,
        now_ts=10.0,
        now_dt=_dt(),
        schedules=[_sched("wallet", every_seconds=5), _sched("rate", "cron", cron="0 2 * * *")],
        admission=admission,
    )
    assert stub.calls == []
    assert hits == []
    assert state.next_every == {}
    assert state.next_cron == {}
    assert state.hourly_gate.last_intraday_key == "keep-me"


def test_s6_closed_rejects_without_consuming(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _closed()
    state = _due_interval(IsolatedScheduleState())
    result = tick(
        state,
        now_ts=50.0,
        now_dt=_dt(),
        schedules=[_sched("wallet", every_seconds=10)],
        admission=admission,
    )
    assert result.rejected[0].state is AdmissionState.BOUND_CLOSED
    assert stub.calls == []
    assert state.next_every["wallet"] == 0.0


def test_s7_submit_exception_does_not_consume(monkeypatch) -> None:
    _install_stub(monkeypatch)

    class _Boom:
        def submit(self, *a, **k):
            raise RuntimeError("submit failed")

    monkeypatch.setattr("modules.antares.work_admission.get_job_executor", lambda: _Boom())
    admission = _open()
    state = _due_interval(IsolatedScheduleState())
    result = tick(
        state,
        now_ts=50.0,
        now_dt=_dt(),
        schedules=[_sched("wallet", every_seconds=10)],
        admission=admission,
    )
    assert result.submit_exceptions
    assert result.accepted == []
    assert state.next_every["wallet"] == 0.0


def test_s7b_two_interval_rows_one_attempt(monkeypatch) -> None:
    _install_stub(monkeypatch)

    class _Boom:
        n = 0

        def submit(self, *a, **k):
            type(self).n += 1
            raise RuntimeError("submit failed")

    monkeypatch.setattr("modules.antares.work_admission.get_job_executor", lambda: _Boom())
    admission = _open()
    state = _due_interval(IsolatedScheduleState())
    rows = [
        _sched("wallet", every_seconds=10, sid="a"),
        _sched("wallet", every_seconds=10, sid="b"),
    ]
    tick(state, now_ts=50.0, now_dt=_dt(), schedules=rows, admission=admission)
    assert _Boom.n == 1
    assert state.next_every["wallet"] == 0.0


def test_two_interval_rows_rejected_one_admission_attempt(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    hits = _count_executor(monkeypatch)
    admission = _closed()
    state = _due_interval(IsolatedScheduleState())
    rows = [
        _sched("wallet", every_seconds=10, sid="a"),
        _sched("wallet", every_seconds=10, sid="b"),
    ]
    result = tick(state, now_ts=50.0, now_dt=_dt(), schedules=rows, admission=admission)
    assert len(result.rejected) == 1
    assert result.rejected[0].state is AdmissionState.BOUND_CLOSED
    assert hits == []
    assert stub.calls == []
    assert state.next_every["wallet"] == 0.0


def test_hourly_submit_exception_same_bucket_retries(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    real = get_job_executor()

    class _BoomOnce:
        n = 0

        def submit(self, fn, *a, **k):
            type(self).n += 1
            if type(self).n == 1:
                raise RuntimeError("submit failed")
            return real.submit(fn, *a, **k)

    monkeypatch.setattr("modules.antares.work_admission.get_job_executor", lambda: _BoomOnce())
    admission = _open()
    state = IsolatedScheduleState()
    state.next_every["hourly"] = 0.0
    now_dt = _dt(10, 5)
    rows = [_sched("hourly", every_seconds=10)]
    with patch("modules.antares.hourly_gate.get_job_params", return_value={"intraday_interval_minutes": 15}):
        first = tick(state, now_ts=50.0, now_dt=now_dt, schedules=rows, admission=admission)
        assert first.submit_exceptions
        assert state.next_every["hourly"] == 0.0
        assert state.hourly_gate.last_intraday_key is None
        second = tick(state, now_ts=50.0, now_dt=now_dt, schedules=rows, admission=admission)
    assert len(second.accepted) == 1
    wait([second.accepted[0].future], timeout=5)
    assert stub.calls == [("hourly", "scheduler", False)]
    assert state.hourly_gate.last_intraday_key == "20260607-0040"


def test_final_daily_reject_and_submit_error_do_not_commit_key(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    params = {"final_daily_time": "02:30"}
    fire_dt = _dt(2, 30)
    rows = [_sched("hourly", "cron", cron="30 2 * * *")]
    with patch("modules.antares.hourly_gate.get_job_params", return_value=params):
        closed = IsolatedScheduleState()
        closed.next_cron["hourly"] = fire_dt
        rejected = tick(closed, now_ts=2.0, now_dt=fire_dt, schedules=rows, admission=_closed())
        assert rejected.rejected
        assert closed.hourly_gate.last_final_key is None
        assert closed.next_cron["hourly"] == fire_dt

        class _Boom:
            def submit(self, *a, **k):
                raise RuntimeError("submit failed")

        monkeypatch.setattr("modules.antares.work_admission.get_job_executor", lambda: _Boom())
        boom_state = IsolatedScheduleState()
        boom_state.next_cron["hourly"] = fire_dt
        boom = tick(boom_state, now_ts=2.0, now_dt=fire_dt, schedules=rows, admission=_open())
        assert boom.submit_exceptions
        assert boom_state.hourly_gate.last_final_key is None
        assert boom_state.next_cron["hourly"] == fire_dt
    assert stub.calls == []


def test_s7c_next_tick_retries_unconsumed(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)

    class _BoomOnce:
        n = 0
        real = None

        def submit(self, fn, *a, **k):
            type(self).n += 1
            if type(self).n == 1:
                raise RuntimeError("submit failed")
            return self.real.submit(fn, *a, **k)

    boom = _BoomOnce()
    boom.real = get_job_executor()
    monkeypatch.setattr("modules.antares.work_admission.get_job_executor", lambda: boom)
    admission = _open()
    state = _due_interval(IsolatedScheduleState())
    rows = [_sched("wallet", every_seconds=10)]
    first = tick(state, now_ts=50.0, now_dt=_dt(), schedules=rows, admission=admission)
    assert first.submit_exceptions
    assert state.next_every["wallet"] == 0.0
    second = tick(state, now_ts=50.0, now_dt=_dt(), schedules=rows, admission=admission)
    assert len(second.accepted) == 1
    wait([second.accepted[0].future], timeout=5)
    assert stub.calls == [("wallet", "scheduler", False)]
    assert state.next_every["wallet"] == 60.0


def test_s7d_invalid_cron_at_due_no_submit(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    armed = datetime(2026, 6, 7, 2, 0, 5, tzinfo=MSK)
    state = IsolatedScheduleState()
    state.next_cron["wallet"] = armed
    result = tick(
        state,
        now_ts=50.0,
        now_dt=armed,
        schedules=[_sched("wallet", "cron", cron="not-a-cron")],
        admission=admission,
    )
    assert stub.calls == []
    assert result.accepted == []
    assert state.next_cron["wallet"] == armed


def test_s8_hourly_rejected_keeps_bucket(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _closed()
    state = IsolatedScheduleState()
    state.next_every["hourly"] = 0.0
    now_dt = _dt(10, 5)
    with patch("modules.antares.hourly_gate.get_job_params", return_value={"intraday_interval_minutes": 15}):
        first = tick(
            state,
            now_ts=50.0,
            now_dt=now_dt,
            schedules=[_sched("hourly", every_seconds=10)],
            admission=admission,
        )
        assert first.rejected
        assert state.hourly_gate.last_intraday_key is None
        assert state.next_every["hourly"] == 0.0
        admission.open()
        second = tick(
            state,
            now_ts=50.0,
            now_dt=now_dt,
            schedules=[_sched("hourly", every_seconds=10)],
            admission=admission,
        )
    assert len(second.accepted) == 1
    wait([second.accepted[0].future], timeout=5)
    assert stub.calls == [("hourly", "scheduler", False)]
    assert state.hourly_gate.last_intraday_key == "20260607-0040"


def test_s9_hourly_accepted_same_bucket_no_duplicate(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    state = IsolatedScheduleState()
    state.next_every["hourly"] = 0.0
    now_dt = _dt(10, 5)
    with patch("modules.antares.hourly_gate.get_job_params", return_value={"intraday_interval_minutes": 15}):
        first = tick(
            state,
            now_ts=50.0,
            now_dt=now_dt,
            schedules=[_sched("hourly", every_seconds=10)],
            admission=admission,
        )
        assert len(first.accepted) == 1
        wait([first.accepted[0].future], timeout=5)
        state.next_every["hourly"] = 0.0
        second = tick(
            state,
            now_ts=80.0,
            now_dt=now_dt,
            schedules=[_sched("hourly", every_seconds=10)],
            admission=admission,
        )
    assert second.accepted == []
    assert stub.calls == [("hourly", "scheduler", False)]
    assert state.next_every["hourly"] == 90.0
    assert state.hourly_gate.last_intraday_key == "20260607-0040"


def test_s10_final_daily_fire_already_not_due(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    params = {"final_daily_time": "02:30"}
    fire_dt = _dt(2, 30)
    early_dt = _dt(2, 29)
    with patch("modules.antares.hourly_gate.get_job_params", return_value=params):
        waiting = IsolatedScheduleState()
        waiting.next_cron["hourly"] = early_dt
        skip = tick(
            waiting,
            now_ts=1.0,
            now_dt=early_dt,
            schedules=[_sched("hourly", "cron", cron="30 2 * * *")],
            admission=admission,
        )
        assert skip.accepted == []
        assert waiting.hourly_gate.last_final_key is None
        assert waiting.next_cron["hourly"] == _next_cron_run(early_dt, "30 2 * * *")

        firing = IsolatedScheduleState()
        firing.next_cron["hourly"] = fire_dt
        accepted = tick(
            firing,
            now_ts=2.0,
            now_dt=fire_dt,
            schedules=[_sched("hourly", "cron", cron="30 2 * * *")],
            admission=admission,
        )
        assert len(accepted.accepted) == 1
        wait([accepted.accepted[0].future], timeout=5)
        assert firing.hourly_gate.last_final_key == "20260607"

        firing.next_cron["hourly"] = fire_dt
        again = tick(
            firing,
            now_ts=3.0,
            now_dt=fire_dt,
            schedules=[_sched("hourly", "cron", cron="30 2 * * *")],
            admission=admission,
        )
        assert again.accepted == []
        assert firing.hourly_gate.last_final_key == "20260607"
    assert stub.calls == [("hourly", "scheduler", False)]


def test_s11_gate_skip_writes_next_not_last(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    state = IsolatedScheduleState()
    state.next_every["hourly"] = 0.0
    state.hourly_gate.last_intraday_key = "20260607-0040"
    with patch("modules.antares.hourly_gate.get_job_params", return_value={"intraday_interval_minutes": 15}):
        tick(
            state,
            now_ts=50.0,
            now_dt=_dt(10, 7),
            schedules=[_sched("hourly", every_seconds=10)],
            admission=admission,
        )
    assert stub.calls == []
    assert state.next_every["hourly"] == 60.0
    assert state.hourly_gate.last_intraday_key == "20260607-0040"


def test_s12_future_error_logged_once_no_rollback(monkeypatch) -> None:
    stub = _install_stub(monkeypatch, hold=True, error=RuntimeError("job failed"))
    admission = _open()
    state = IsolatedScheduleState()
    state.next_every["wallet"] = 0.0
    state.hourly_gate.last_intraday_key = "keep-gate"
    logged = threading.Event()
    logger = logging.getLogger("modules.antares.scheduler")
    orig_exc = logger.exception
    count = {"n": 0}

    def _exc(*args, **kwargs):
        try:
            return orig_exc(*args, **kwargs)
        finally:
            if args and "scheduled job failed" in str(args[0]):
                count["n"] += 1
                logged.set()

    monkeypatch.setattr(logger, "exception", _exc)
    try:
        result = tick(
            state,
            now_ts=50.0,
            now_dt=_dt(),
            schedules=[_sched("wallet", every_seconds=10)],
            admission=admission,
        )
        assert len(result.accepted) == 1
        assert state.next_every["wallet"] == 60.0
        stub.release.set()
        assert logged.wait(timeout=5)
        assert count["n"] == 1
        assert stub.calls == [("wallet", "scheduler", False)]
        assert state.hourly_gate.last_intraday_key == "keep-gate"
        again = tick(
            state,
            now_ts=55.0,
            now_dt=_dt(),
            schedules=[_sched("wallet", every_seconds=10)],
            admission=admission,
        )
        assert again.accepted == []
        assert stub.calls == [("wallet", "scheduler", False)]
        assert state.next_every["wallet"] == 60.0
        assert count["n"] == 1
    finally:
        stub.release.set()


def test_s12b_future_already_done_callback_sees_commit(monkeypatch) -> None:
    stub = _install_stub(monkeypatch, error=RuntimeError("already done"))
    admission = _open()
    orig = admission.submit_job_if_open

    def _wait_done(job_type, actor, **kwargs):
        outcome = orig(job_type, actor, **kwargs)
        if isinstance(outcome, AdmissionAccepted):
            wait([outcome.future], timeout=5)
            assert outcome.future.done()
        return outcome

    monkeypatch.setattr(admission, "submit_job_if_open", _wait_done)
    state = IsolatedScheduleState()
    state.next_every["wallet"] = 0.0
    state.hourly_gate.last_intraday_key = "keep-gate"
    during_cb: dict[str, object] = {}
    logged = threading.Event()
    handler = _LogSeen(logged, "scheduled job failed")
    logger = logging.getLogger("modules.antares.scheduler")
    from concurrent.futures import Future as CFFuture

    orig_add = CFFuture.add_done_callback

    def _add(self, cb):
        def _wrapped(fut):
            during_cb["next_every"] = dict(state.next_every)
            during_cb["last_intraday"] = state.hourly_gate.last_intraday_key
            cb(fut)

        orig_add(self, _wrapped)

    monkeypatch.setattr(CFFuture, "add_done_callback", _add)
    logger.addHandler(handler)
    orig_exc = logger.exception

    def _exc(*args, **kwargs):
        try:
            return orig_exc(*args, **kwargs)
        finally:
            if args and "scheduled job failed" in str(args[0]):
                logged.set()

    monkeypatch.setattr(logger, "exception", _exc)
    try:
        result = tick(
            state,
            now_ts=50.0,
            now_dt=_dt(),
            schedules=[_sched("wallet", every_seconds=10)],
            admission=admission,
        )
        assert logged.wait(timeout=5)
        assert result.accepted[0].future.done()
        assert during_cb["next_every"] == {"wallet": 60.0}
        assert during_cb["last_intraday"] == "keep-gate"
        assert state.next_every["wallet"] == 60.0
        assert handler.count == 1
        assert stub.calls == [("wallet", "scheduler", False)]
    finally:
        logger.removeHandler(handler)


def test_s13_observer_attach_error_keeps_accepted_no_duplicate(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    state = _due_interval(IsolatedScheduleState())

    def _boom(future, job_type):  # noqa: ANN001
        raise RuntimeError("callback attach")

    monkeypatch.setattr(
        "modules.antares.scheduler._observe_accepted_future",
        _boom,
    )
    result = tick(
        state,
        now_ts=50.0,
        now_dt=_dt(),
        schedules=[_sched("wallet", every_seconds=10)],
        admission=admission,
    )
    assert len(result.accepted) == 1
    assert state.next_every["wallet"] == 60.0
    wait([result.accepted[0].future], timeout=5)
    assert stub.calls == [("wallet", "scheduler", False)]
    again = tick(
        state,
        now_ts=50.0,
        now_dt=_dt(),
        schedules=[_sched("wallet", every_seconds=10)],
        admission=admission,
    )
    assert again.accepted == []
    assert stub.calls == [("wallet", "scheduler", False)]
    assert state.next_every["wallet"] == 60.0


def test_s14_reset_clocks_keeps_gate(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    state = IsolatedScheduleState()
    state.next_every["wallet"] = 99.0
    state.next_cron["hourly"] = _dt()
    state.hourly_gate.last_intraday_key = "keep-me"
    request_scheduler_clocks_reset("test")
    tick(state, now_ts=10.0, now_dt=_dt(), schedules=[_sched("wallet", every_seconds=5)], admission=admission)
    assert stub.calls == []
    assert "hourly" not in state.next_cron
    assert state.next_every["wallet"] == 15.0
    assert state.hourly_gate.last_intraday_key == "keep-me"


def test_s15_two_interval_rows_first_accepted(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    state = _due_interval(IsolatedScheduleState())
    rows = [
        _sched("wallet", every_seconds=10, sid="a"),
        _sched("wallet", every_seconds=10, sid="b"),
    ]
    result = tick(state, now_ts=50.0, now_dt=_dt(), schedules=rows, admission=admission)
    assert len(result.accepted) == 1
    wait([result.accepted[0].future], timeout=5)
    assert stub.calls == [("wallet", "scheduler", False)]
    assert state.next_every["wallet"] == 60.0


def test_s16_interval_and_cron_two_clocks(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    now_dt = _dt()
    state = IsolatedScheduleState()
    state.next_every["wallet"] = 0.0
    state.next_cron["wallet"] = now_dt
    rows = [
        _sched("wallet", every_seconds=10, sid="i"),
        _sched("wallet", "cron", cron="5 10 * * *", sid="c"),
    ]
    result = tick(state, now_ts=50.0, now_dt=now_dt, schedules=rows, admission=admission)
    assert len(result.accepted) == 2
    wait([item.future for item in result.accepted], timeout=5)
    assert [c[0] for c in stub.calls] == ["wallet", "wallet"]
    assert state.next_every["wallet"] == 60.0
    assert state.next_cron["wallet"] == _next_cron_run(now_dt, "5 10 * * *")


def test_s17_forbidden_imports_in_new_process(tmp_path) -> None:
    env = isolated_child_env(tmp_path)
    script = r"""
import sys
import modules.antares.scheduler  # noqa: F401
import modules.antares.hourly_gate  # noqa: F401
import modules.antares.schedule_timing  # noqa: F401
forbidden = [n for n in ("scheduler", "integrations.telegram_bot", "integrations.tg_commands") if n in sys.modules]
if forbidden:
    raise SystemExit("forbidden: " + ",".join(forbidden))
print("ok")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        **child_subprocess_kwargs(),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout


def test_s18_get_job_params_outside_admission_lock(monkeypatch) -> None:
    stub = _install_stub(monkeypatch)
    admission = _open()
    seen_lock: list[bool] = []

    def _params(*, job):
        seen_lock.append(admission._lock.locked())
        return {"intraday_interval_minutes": 15}

    monkeypatch.setattr("modules.antares.hourly_gate.get_job_params", _params)
    state = IsolatedScheduleState()
    state.next_every["hourly"] = 0.0
    result = tick(
        state,
        now_ts=50.0,
        now_dt=_dt(10, 5),
        schedules=[_sched("hourly", every_seconds=10)],
        admission=admission,
    )
    assert seen_lock == [False]
    assert len(result.accepted) == 1
    wait([result.accepted[0].future], timeout=5)
    assert stub.calls == [("hourly", "scheduler", False)]


def test_boot_run_source_does_not_start_schedules() -> None:
    src = (ROOT / "apps" / "antares.py").read_text(encoding="utf-8")
    assert "modules.antares.scheduler" not in src
    assert "IsolatedScheduleState" not in src
    assembly = (ROOT / "modules" / "antares" / "assembly.py").read_text(encoding="utf-8")
    assert "modules.antares.scheduler" not in assembly
    assert "IsolatedScheduleState" not in assembly


def _mixed_formula_ns(get_job_params):
    src = (ROOT / "scheduler.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep_fn = {"_parse_cron_min_hour", "_next_cron_run", "_parse_hhmm", "evaluate_hourly_gate"}
    keep_cls = {"HourlyGate"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in keep_fn:
            body.append(node)
        elif isinstance(node, ast.ClassDef) and node.name in keep_cls:
            body.append(node)
    mod = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {
        "Tuple": tuple,
        "Optional": Optional,
        "datetime": datetime,
        "timedelta": timedelta,
        "dataclass": dataclass,
        "get_job_params": get_job_params,
    }
    exec(compile(mod, "scheduler.py", "exec"), ns)
    return ns


def test_cron_formulas_match_mixed_source_without_importing_scheduler() -> None:
    mixed = _mixed_formula_ns(lambda **k: {})
    nows = [
        datetime(2026, 6, 7, 10, 0, tzinfo=MSK),
        datetime(2026, 6, 7, 10, 0, 5, tzinfo=MSK),
        datetime(2026, 6, 7, 10, 0, 6, tzinfo=MSK),
        datetime(2026, 6, 7, 2, 5, tzinfo=MSK),
        datetime(2026, 6, 7, 23, 59, tzinfo=MSK),
    ]
    exprs = ["0 * * * *", "5 2 * * *", "0 0 * * *", "59 23 * * *"]
    for now in nows:
        for expr in exprs:
            assert _parse_cron_min_hour(expr) == mixed["_parse_cron_min_hour"](expr)
            assert _next_cron_run(now, expr) == mixed["_next_cron_run"](now, expr)


def test_hourly_formulas_match_mixed_source_without_importing_scheduler() -> None:
    params_list = [
        {},
        {"intraday_interval_minutes": 15},
        {"intraday_interval_minutes": 60},
        {"final_daily_time": "02:30"},
        {"intraday_interval_minutes": 15, "final_daily_time": "10:05"},
    ]
    nows = [
        datetime(2026, 6, 7, 10, 5, tzinfo=MSK),
        datetime(2026, 6, 7, 10, 7, tzinfo=MSK),
        datetime(2026, 6, 7, 2, 30, tzinfo=MSK),
        datetime(2026, 6, 7, 2, 29, tzinfo=MSK),
        datetime(2026, 6, 7, 11, 5, tzinfo=MSK),
    ]
    for params in params_list:
        mixed = _mixed_formula_ns(lambda **k: params)
        with patch("modules.antares.hourly_gate.get_job_params", return_value=params):
            for now in nows:
                gate_iso = HourlyGate()
                gate_mix = mixed["HourlyGate"]()
                peek = peek_hourly_gate(now, gate_iso)
                fired, reason = mixed["evaluate_hourly_gate"](now, gate_mix)
                assert peek.should_fire is fired
                assert peek.reason == reason
                assert gate_iso.last_intraday_key is None
                assert gate_iso.last_final_key is None
                if peek.candidate_intraday_key is not None:
                    assert gate_mix.last_intraday_key == peek.candidate_intraday_key
                if peek.candidate_final_key is not None:
                    assert gate_mix.last_final_key == peek.candidate_final_key
                if not fired:
                    assert gate_mix.last_intraday_key is None
                    assert gate_mix.last_final_key is None
