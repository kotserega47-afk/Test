"""TASK-49.D: production ``stop_isolated_job_executor`` (Q-EX2) regressions."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.job_dispatch import (
    JobExecutorStoppedError,
    _reset_job_executor_for_tests,
    get_job_executor,
    stop_isolated_job_executor,
)
import core.job_dispatch as job_dispatch


@pytest.fixture(autouse=True)
def _reset_executor():
    _reset_job_executor_for_tests()
    yield
    _reset_job_executor_for_tests()


def test_absent_executor_does_not_create() -> None:
    async def _main() -> None:
        assert job_dispatch._JOB_EXECUTOR is None
        result = await stop_isolated_job_executor(timeout=1.0)
        assert result.ok is True
        assert result.executor_was_absent is True
        assert result.stopped is True
        assert result.shutdown_called is True
        assert job_dispatch._JOB_EXECUTOR is None
        with pytest.raises(JobExecutorStoppedError):
            get_job_executor()

    asyncio.run(_main())


def test_full_stop_joins_job_worker_threads() -> None:
    async def _main() -> None:
        ex = get_job_executor()
        started = threading.Event()
        release = threading.Event()

        def _hold() -> None:
            started.set()
            release.wait(timeout=5)

        fut = ex.submit(_hold)
        assert started.wait(timeout=2)
        # shutdown(wait=False) alone must not prove threads done.
        stop_task = asyncio.create_task(stop_isolated_job_executor(timeout=5.0))
        await asyncio.sleep(0.05)
        mid = None
        # Give the stop a chance to call shutdown(wait=False) while thread lives.
        for _ in range(20):
            from core import job_dispatch as jd

            if jd._SHUTDOWN_CALLED:
                mid = True
                break
            await asyncio.sleep(0.02)
        assert mid is True
        release.set()
        result = await stop_task
        assert result.ok is True
        assert result.stopped is True
        assert result.live_thread_count == 0
        fut.result(timeout=2)
        with pytest.raises(JobExecutorStoppedError):
            get_job_executor()

    asyncio.run(_main())


def test_cancel_futures_false_and_no_wait_true() -> None:
    async def _main() -> None:
        ex = get_job_executor()
        calls: list[tuple] = []
        orig = ex.shutdown

        def _spy(*args, **kwargs):
            calls.append((args, dict(kwargs)))
            return orig(*args, **kwargs)

        ex.shutdown = _spy  # type: ignore[method-assign]
        result = await stop_isolated_job_executor(timeout=2.0)
        assert result.ok is True
        assert calls, "shutdown must be called"
        _args, kwargs = calls[0]
        assert kwargs.get("wait") is False
        assert kwargs.get("cancel_futures") is False

    asyncio.run(_main())


def test_repeat_and_concurrent_share_one_shutdown() -> None:
    async def _main() -> None:
        ex = get_job_executor()
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

        t1 = asyncio.create_task(stop_isolated_job_executor(timeout=5.0))
        t2 = asyncio.create_task(stop_isolated_job_executor(timeout=5.0))
        await asyncio.sleep(0.05)
        release.set()
        r1, r2 = await asyncio.gather(t1, t2)
        assert r1.stopped and r2.stopped
        assert shutdown_calls == 1
        r3 = await stop_isolated_job_executor(timeout=1.0)
        assert r3.ok and r3.stopped
        assert shutdown_calls == 1

    asyncio.run(_main())


def test_deadline_before_shutdown_does_not_start() -> None:
    async def _main() -> None:
        ex = get_job_executor()
        release = threading.Event()
        started = threading.Event()

        def _hold() -> None:
            started.set()
            release.wait(timeout=5)

        ex.submit(_hold)
        assert started.wait(timeout=2)
        # Zero budget before first shutdown call.
        result = await stop_isolated_job_executor(timeout=0.0)
        assert result.ok is False
        assert result.reason == "deadline_before_executor_shutdown"
        assert result.shutdown_called is False
        from core import job_dispatch as jd

        assert jd._SHUTDOWN_CALLED is False
        assert jd._EXECUTOR_STOP_STATE == "idle"
        release.set()
        # Clean up via production stop with budget.
        done = await stop_isolated_job_executor(timeout=5.0)
        assert done.stopped is True

    asyncio.run(_main())


def test_deadline_during_observe_leaves_stopping_same_owner() -> None:
    async def _main() -> None:
        ex = get_job_executor()
        release = threading.Event()
        started = threading.Event()

        def _hold() -> None:
            started.set()
            release.wait(timeout=10)

        ex.submit(_hold)
        assert started.wait(timeout=2)

        # Tiny budget: shutdown starts, threads still live → stopping partial.
        partial = await stop_isolated_job_executor(timeout=0.05)
        assert partial.shutdown_called is True
        assert partial.stopped is False
        assert partial.stopping is True
        assert partial.reason == "deadline_executor_threads"

        release.set()
        # Rejoin without needing a "new" procedure — same permanent stop.
        final = await stop_isolated_job_executor(timeout=5.0)
        assert final.ok is True
        assert final.stopped is True

    asyncio.run(_main())


def test_does_not_touch_default_executor() -> None:
    async def _main() -> None:
        default = ThreadPoolExecutor(max_workers=1, thread_name_prefix="default-pool")
        try:
            ran = threading.Event()

            def _mark() -> None:
                ran.set()

            fut = default.submit(_mark)
            fut.result(timeout=2)
            assert ran.is_set()
            get_job_executor()
            await stop_isolated_job_executor(timeout=2.0)
            # Default pool still accepts work.
            fut2 = default.submit(lambda: 7)
            assert fut2.result(timeout=2) == 7
        finally:
            default.shutdown(wait=True, cancel_futures=True)

    asyncio.run(_main())


def test_ptb_loop_stays_responsive_during_stop() -> None:
    async def _main() -> None:
        ex = get_job_executor()
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

        stop_task = asyncio.create_task(stop_isolated_job_executor(timeout=5.0))
        tick_task = asyncio.create_task(_ticker())
        await asyncio.gather(stop_task, tick_task)
        assert ticks >= 3
        assert release.wait(timeout=2)

    asyncio.run(_main())
