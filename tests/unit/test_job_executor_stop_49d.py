"""TASK-49.D: production ``stop_isolated_job_executor`` (Q-EX2) regressions."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import core.job_dispatch as job_dispatch
from core.job_dispatch import (
    JobExecutorStoppedError,
    _reset_job_executor_for_tests,
    bind_job_executor_to_admission,
    get_job_executor,
    stop_isolated_job_executor,
)
from modules.antares.work_admission import WorkAdmission


@pytest.fixture(autouse=True)
def _reset_executor():
    _reset_job_executor_for_tests()
    yield
    _reset_job_executor_for_tests()


def test_absent_executor_does_not_create_and_shutdown_called_false() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        assert job_dispatch._JOB_EXECUTOR is None
        result = await stop_isolated_job_executor(admission=admission, timeout=1.0)
        assert result.ok is True
        assert result.executor_was_absent is True
        assert result.stopped is True
        assert result.shutdown_called is False
        assert result.recreate_refused is True
        assert result.ownership_ok is True
        assert job_dispatch._JOB_EXECUTOR is None
        with pytest.raises(JobExecutorStoppedError):
            get_job_executor()
        # Stable repeated diagnostics.
        again = await stop_isolated_job_executor(admission=admission, timeout=1.0)
        assert again.executor_was_absent is True
        assert again.shutdown_called is False
        assert again.recreate_refused is True
        assert again.executor_object_id == result.executor_object_id

    asyncio.run(_main())


def test_full_stop_joins_job_worker_threads() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        ex = bind_job_executor_to_admission(admission)
        object_id = id(ex)
        started = threading.Event()
        release = threading.Event()

        def _hold() -> None:
            started.set()
            release.wait(timeout=5)

        fut = ex.submit(_hold)
        assert started.wait(timeout=2)
        stop_task = asyncio.create_task(
            stop_isolated_job_executor(admission=admission, timeout=5.0)
        )
        await asyncio.sleep(0.05)
        for _ in range(20):
            if job_dispatch._SHUTDOWN_CALLED:
                break
            await asyncio.sleep(0.02)
        assert job_dispatch._SHUTDOWN_CALLED is True
        release.set()
        result = await stop_task
        assert result.ok is True
        assert result.stopped is True
        assert result.live_thread_count == 0
        assert result.executor_object_id == object_id
        assert result.executor_was_absent is False
        fut.result(timeout=2)
        with pytest.raises(JobExecutorStoppedError):
            get_job_executor()
        with pytest.raises(JobExecutorStoppedError):
            bind_job_executor_to_admission(admission)

    asyncio.run(_main())


def test_cancel_futures_false_and_no_wait_true() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        ex = bind_job_executor_to_admission(admission)
        calls: list[tuple] = []
        orig = ex.shutdown

        def _spy(*args, **kwargs):
            calls.append((args, dict(kwargs)))
            return orig(*args, **kwargs)

        ex.shutdown = _spy  # type: ignore[method-assign]
        result = await stop_isolated_job_executor(admission=admission, timeout=2.0)
        assert result.ok is True
        assert calls, "shutdown must be called"
        _args, kwargs = calls[0]
        assert kwargs.get("wait") is False
        assert kwargs.get("cancel_futures") is False

    asyncio.run(_main())


def test_ownership_refuse_before_shutdown_and_cached() -> None:
    async def _main() -> None:
        owner = WorkAdmission()
        foreign = WorkAdmission()
        bind_job_executor_to_admission(owner)
        refused = await stop_isolated_job_executor(admission=foreign, timeout=1.0)
        assert refused.ok is False
        assert refused.attempted is False
        assert refused.reason == "ownership_foreign"
        assert refused.ownership_ok is False
        assert job_dispatch._SHUTDOWN_CALLED is False
        assert job_dispatch._RECREATE_REFUSED is False

        # Unbound foreign create → mixed/unproven for isolated stop.
        _reset_job_executor_for_tests()
        get_job_executor()
        other = WorkAdmission()
        mixed = await stop_isolated_job_executor(admission=other, timeout=1.0)
        assert mixed.ok is False
        assert mixed.attempted is False
        assert mixed.reason in ("ownership_unproven", "ownership_mixed")
        assert job_dispatch._SHUTDOWN_CALLED is False

    asyncio.run(_main())


def test_mixed_dispatch_marks_refuse() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        bind_job_executor_to_admission(admission)
        # Foreign get of an admission-owned pool marks mixed.
        get_job_executor()
        assert job_dispatch._MIXED_OR_FOREIGN is True
        refused = await stop_isolated_job_executor(admission=admission, timeout=1.0)
        assert refused.ok is False
        assert refused.reason == "ownership_mixed"
        assert refused.attempted is False
        assert job_dispatch._SHUTDOWN_CALLED is False

    asyncio.run(_main())


def test_repeat_and_concurrent_share_one_shutdown() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        ex = bind_job_executor_to_admission(admission)
        release = threading.Event()
        started = threading.Event()

        def _hold() -> None:
            started.set()
            release.wait(timeout=5)

        ex.submit(_hold)
        assert started.wait(timeout=2)

        shutdown_calls = 0
        orig = ex.shutdown

        def _spy(*args, **kwargs):
            nonlocal shutdown_calls
            shutdown_calls += 1
            return orig(*args, **kwargs)

        ex.shutdown = _spy  # type: ignore[method-assign]

        t1 = asyncio.create_task(
            stop_isolated_job_executor(admission=admission, timeout=5.0)
        )
        t2 = asyncio.create_task(
            stop_isolated_job_executor(admission=admission, timeout=5.0)
        )
        await asyncio.sleep(0.05)
        release.set()
        r1, r2 = await asyncio.gather(t1, t2)
        assert r1.stopped and r2.stopped
        assert shutdown_calls == 1
        r3 = await stop_isolated_job_executor(admission=admission, timeout=1.0)
        assert r3.ok and r3.stopped
        assert shutdown_calls == 1
        assert r3.executor_object_id == r1.executor_object_id

    asyncio.run(_main())


def test_deadline_before_shutdown_does_not_start() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        ex = bind_job_executor_to_admission(admission)
        release = threading.Event()
        started = threading.Event()

        def _hold() -> None:
            started.set()
            release.wait(timeout=5)

        ex.submit(_hold)
        assert started.wait(timeout=2)
        result = await stop_isolated_job_executor(admission=admission, timeout=0.0)
        assert result.ok is False
        assert result.reason == "deadline_before_executor_shutdown"
        assert result.shutdown_called is False
        assert job_dispatch._SHUTDOWN_CALLED is False
        assert job_dispatch._EXECUTOR_STOP_STATE == "idle"
        release.set()
        done = await stop_isolated_job_executor(admission=admission, timeout=5.0)
        assert done.stopped is True

    asyncio.run(_main())


def test_deadline_during_observe_then_continue_same_shutdown() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        ex = bind_job_executor_to_admission(admission)
        release = threading.Event()
        started = threading.Event()

        def _hold() -> None:
            started.set()
            release.wait(timeout=10)

        ex.submit(_hold)
        assert started.wait(timeout=2)

        partial = await stop_isolated_job_executor(admission=admission, timeout=0.05)
        assert partial.shutdown_called is True
        assert partial.stopped is False
        assert partial.stopping is True
        assert partial.reason == "deadline_executor_threads"
        # Not a cached terminal.
        assert job_dispatch._CACHED_TERMINAL is None

        release.set()
        final = await stop_isolated_job_executor(admission=admission, timeout=None)
        assert final.ok is True
        assert final.stopped is True
        assert final.executor_object_id == partial.executor_object_id

    asyncio.run(_main())


def test_does_not_touch_default_executor() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        default = ThreadPoolExecutor(max_workers=1, thread_name_prefix="default-pool")
        try:
            ran = threading.Event()

            def _mark() -> None:
                ran.set()

            fut = default.submit(_mark)
            fut.result(timeout=2)
            assert ran.is_set()
            bind_job_executor_to_admission(admission)
            await stop_isolated_job_executor(admission=admission, timeout=2.0)
            fut2 = default.submit(lambda: 7)
            assert fut2.result(timeout=2) == 7
        finally:
            default.shutdown(wait=True, cancel_futures=True)

    asyncio.run(_main())


def test_ptb_loop_stays_responsive_during_stop() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        ex = bind_job_executor_to_admission(admission)
        release = threading.Event()
        started = threading.Event()

        def _hold() -> None:
            started.set()
            time.sleep(0.2)
            release.set()

        ex.submit(_hold)
        assert started.wait(timeout=2)
        ticks = 0

        async def _ticker() -> None:
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.02)
                ticks += 1

        stop_task = asyncio.create_task(
            stop_isolated_job_executor(admission=admission, timeout=5.0)
        )
        tick_task = asyncio.create_task(_ticker())
        await asyncio.gather(stop_task, tick_task)
        assert ticks >= 3
        assert release.wait(timeout=2)

    asyncio.run(_main())
