# core/job_dispatch.py
"""Shared job worker pool for Playwright jobs (Phase 3a / Variant B).

TASK-49.D adds production ``stop_isolated_job_executor`` (Q-EX2). The process-global
``job-worker`` pool is the Antares isolated job executor. Shutdown never uses
``cancel_futures=True``, never creates an executor only to stop it, and never
blocks the caller event loop on ``wait=True``. After a production stop, the pool
must not be recreated implicitly.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from core.job_runner import Actor, request_job

log = logging.getLogger(__name__)

_DEFAULT_MAX_WORKERS = 2
_JOB_EXECUTOR: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
# idle → stopping (shutdown called) → stopped (no live job-worker threads).
_EXECUTOR_STOP_STATE = "idle"
_SHUTDOWN_CALLED = False


class JobExecutorStoppedError(RuntimeError):
    """Raised when ``get_job_executor`` would recreate a permanently stopped pool."""


@dataclass(frozen=True, slots=True)
class JobExecutorStopResult:
    """Structured production stop outcome for the process-global job executor."""

    ok: bool
    reason: str | None
    attempted: bool
    shutdown_called: bool
    stopping: bool
    stopped: bool
    executor_was_absent: bool
    live_thread_names: tuple[str, ...]
    live_thread_count: int
    error_type: str | None
    error_text: str | None


def _dispatch_enabled() -> bool:
    return os.getenv("JOB_DISPATCH_VIA_EXECUTOR", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "n",
    }


def _max_workers() -> int:
    raw = os.getenv("JOB_EXECUTOR_MAX_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return _DEFAULT_MAX_WORKERS


def get_job_executor() -> ThreadPoolExecutor:
    """Return the process-global Antares job executor (create once).

    After ``stop_isolated_job_executor`` has permanently stopped the pool, this
    refuses recreation so shutdown cannot be undone by a later submit path.
    """

    global _JOB_EXECUTOR
    with _executor_lock:
        if _SHUTDOWN_CALLED or _EXECUTOR_STOP_STATE in ("stopping", "stopped"):
            # Even if a shutdown executor object is still held for observation,
            # never hand it out for new work / never recreate.
            if _JOB_EXECUTOR is not None and _EXECUTOR_STOP_STATE == "stopping":
                # Mid-stop: still refuse new creates; callers must not submit.
                raise JobExecutorStoppedError(
                    "antares job executor permanently stopping; refusing recreate"
                )
            raise JobExecutorStoppedError(
                "antares job executor permanently stopped; refusing recreate"
            )
        if _JOB_EXECUTOR is not None:
            return _JOB_EXECUTOR
        _JOB_EXECUTOR = ThreadPoolExecutor(
            max_workers=_max_workers(),
            thread_name_prefix="job-worker",
        )
        return _JOB_EXECUTOR


def _reset_job_executor_for_tests() -> None:
    """Test-only reset. Production stop must never call this."""

    global _JOB_EXECUTOR, _EXECUTOR_STOP_STATE, _SHUTDOWN_CALLED
    with _executor_lock:
        if _JOB_EXECUTOR is not None:
            _JOB_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _JOB_EXECUTOR = None
        _EXECUTOR_STOP_STATE = "idle"
        _SHUTDOWN_CALLED = False


def _log_future_exception(future: Future, *, job_type: str, actor_kind: str) -> None:
    exc = future.exception()
    if exc is None:
        return
    log.exception(
        "scheduled job worker failed: job_type=%s actor=%s",
        job_type,
        actor_kind,
        exc_info=exc,
    )


def dispatch_job_background(
    job_type: str,
    actor: Actor,
    *,
    force_rules_sync: bool = False,
) -> None:
    """Enqueue a job for the scheduler without blocking the caller.

    Locking and ``job_started`` / ``job_failed`` events remain inside ``request_job``.
    Exceptions raised from the worker future are logged via ``add_done_callback``.
    """

    actor_kind = actor.kind

    def _done(fut: Future) -> None:
        _log_future_exception(fut, job_type=job_type, actor_kind=actor_kind)

    future = get_job_executor().submit(
        request_job,
        job_type,
        actor,
        force_rules_sync=force_rules_sync,
    )
    future.add_done_callback(_done)


def dispatch_job_sync(
    job_type: str,
    actor: Actor,
    *,
    force_rules_sync: bool = False,
) -> str:
    if not _dispatch_enabled():
        return request_job(job_type, actor, force_rules_sync=force_rules_sync)

    future = get_job_executor().submit(
        request_job,
        job_type,
        actor,
        force_rules_sync=force_rules_sync,
    )
    return future.result()


async def dispatch_job_async(
    job_type: str,
    actor: Actor,
    *,
    force_rules_sync: bool = False,
) -> str:
    if not _dispatch_enabled():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: request_job(job_type, actor, force_rules_sync=force_rules_sync),
        )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        get_job_executor(),
        lambda: request_job(job_type, actor, force_rules_sync=force_rules_sync),
    )


def _live_job_worker_threads(
    executor: ThreadPoolExecutor | None,
) -> tuple[threading.Thread, ...]:
    if executor is None:
        return ()
    threads = getattr(executor, "_threads", None) or ()
    return tuple(t for t in threads if t.is_alive())


def _snapshot_stop_fields(
    *,
    ok: bool,
    reason: str | None,
    attempted: bool,
    shutdown_called: bool,
    stopping: bool,
    stopped: bool,
    executor_was_absent: bool,
    live: tuple[threading.Thread, ...],
    error_type: str | None = None,
    error_text: str | None = None,
) -> JobExecutorStopResult:
    names = tuple(t.name for t in live)
    return JobExecutorStopResult(
        ok=ok,
        reason=reason,
        attempted=attempted,
        shutdown_called=shutdown_called,
        stopping=stopping,
        stopped=stopped,
        executor_was_absent=executor_was_absent,
        live_thread_names=names,
        live_thread_count=len(names),
        error_type=error_type,
        error_text=error_text,
    )


async def stop_isolated_job_executor(
    *,
    timeout: float = 30.0,
) -> JobExecutorStopResult:
    """Production stop for the process-global Antares ``job-worker`` executor (Q-EX2).

    - Does **not** create an executor only to shut it down.
    - Never uses ``cancel_futures=True`` (Accepted work must not be cancelled).
    - Never uses ``shutdown(wait=True)`` on the caller / PTB loop.
    - After ``shutdown(wait=False)``, observes live ``job-worker`` threads with
      non-blocking joins until terminal or timeout.
    - Repeat / concurrent callers share the same shutdown; after stop the pool is
      not recreated by ``get_job_executor``.
    """

    global _EXECUTOR_STOP_STATE, _SHUTDOWN_CALLED, _JOB_EXECUTOR

    deadline = time.monotonic() + max(0.0, float(timeout))
    attempted = False
    shutdown_just_called = False
    error_type: str | None = None
    error_text: str | None = None
    executor: ThreadPoolExecutor | None

    with _executor_lock:
        executor = _JOB_EXECUTOR
        if (
            _EXECUTOR_STOP_STATE == "stopped"
            and not _live_job_worker_threads(executor)
        ):
            return _snapshot_stop_fields(
                ok=True,
                reason=None,
                attempted=True,
                shutdown_called=_SHUTDOWN_CALLED,
                stopping=False,
                stopped=True,
                executor_was_absent=executor is None,
                live=(),
            )

        if executor is None and not _SHUTDOWN_CALLED:
            # Absent: do not create. Mark permanently stopped so get cannot revive.
            _SHUTDOWN_CALLED = True
            _EXECUTOR_STOP_STATE = "stopped"
            return _snapshot_stop_fields(
                ok=True,
                reason=None,
                attempted=True,
                shutdown_called=True,
                stopping=False,
                stopped=True,
                executor_was_absent=True,
                live=(),
            )

        attempted = True
        if not _SHUTDOWN_CALLED:
            if time.monotonic() >= deadline:
                return _snapshot_stop_fields(
                    ok=False,
                    reason="deadline_before_executor_shutdown",
                    attempted=True,
                    shutdown_called=False,
                    stopping=False,
                    stopped=False,
                    executor_was_absent=False,
                    live=_live_job_worker_threads(executor),
                )
            assert executor is not None
            try:
                # Production: never cancel Accepted Futures.
                executor.shutdown(wait=False, cancel_futures=False)
            except TypeError:
                # Older signatures without cancel_futures — still wait=False only.
                executor.shutdown(wait=False)
            except Exception as exc:  # noqa: BLE001
                error_type = type(exc).__name__
                error_text = str(exc)[:200]
                return _snapshot_stop_fields(
                    ok=False,
                    reason=f"executor_shutdown_failed:{error_type}",
                    attempted=True,
                    shutdown_called=False,
                    stopping=False,
                    stopped=False,
                    executor_was_absent=False,
                    live=_live_job_worker_threads(executor),
                    error_type=error_type,
                    error_text=error_text,
                )
            _SHUTDOWN_CALLED = True
            _EXECUTOR_STOP_STATE = "stopping"
            shutdown_just_called = True
        else:
            _EXECUTOR_STOP_STATE = "stopping"

    # Observe threads without blocking the caller event loop (no wait=True).
    assert executor is not None or _SHUTDOWN_CALLED
    while True:
        live = _live_job_worker_threads(executor)
        if not live:
            with _executor_lock:
                _EXECUTOR_STOP_STATE = "stopped"
                # Drop the reference so get_job_executor cannot revive or reuse.
                _JOB_EXECUTOR = None
            return _snapshot_stop_fields(
                ok=True,
                reason=None,
                attempted=True,
                shutdown_called=True,
                stopping=False,
                stopped=True,
                executor_was_absent=False,
                live=(),
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            with _executor_lock:
                _EXECUTOR_STOP_STATE = "stopping"
            return _snapshot_stop_fields(
                ok=False,
                reason="deadline_executor_threads",
                attempted=True,
                shutdown_called=True,
                stopping=True,
                stopped=False,
                executor_was_absent=False,
                live=live,
            )

        # Non-blocking join slices on a worker thread.
        slice_timeout = min(0.05, remaining)

        def _join_slice(
            threads: tuple[threading.Thread, ...] = live,
            timeout: float = slice_timeout,
        ) -> None:
            per = timeout / max(1, len(threads))
            for thread in threads:
                thread.join(timeout=per)

        await asyncio.to_thread(_join_slice)
        await asyncio.sleep(0)
        _ = shutdown_just_called  # claim recorded above for diagnostics
