# core/job_dispatch.py
"""Shared job worker pool for Playwright jobs (Phase 3a / Variant B).

TASK-49.D production ``stop_isolated_job_executor`` (Q-EX2):

- Isolated ownership is an **admission identity bind**, not ``thread_name_prefix``
  and not sender ownership proof.
- Foreign / unbound ``get_job_executor`` / ``dispatch_job_*`` use marks the pool
  mixed; isolated stop then refuses before shutdown and before cached results.
- Never creates an executor only to shut it down; never ``cancel_futures=True``;
  never blocks the PTB loop with ``shutdown(wait=True)``.
- ``shutdown_called`` is truthful (absent stop does not claim shutdown was
  invoked). Permanent recreate refusal is tracked separately.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional

from core.job_runner import Actor, request_job

log = logging.getLogger(__name__)

_DEFAULT_MAX_WORKERS = 2
_JOB_EXECUTOR: Optional[ThreadPoolExecutor] = None
_OBSERVE_EXECUTOR: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
# idle → stopping (shutdown called) → stopped (no live job-worker threads).
_EXECUTOR_STOP_STATE = "idle"
_SHUTDOWN_CALLED = False
_RECREATE_REFUSED = False
_OWNER_ADMISSION_TOKEN: int | None = None
_MIXED_OR_FOREIGN = False
_EXECUTOR_OBJECT_ID: int | None = None
_WAS_ABSENT_AT_STOP = False
_CACHED_TERMINAL: "JobExecutorStopResult | None" = None


class JobExecutorStoppedError(RuntimeError):
    """Raised when ``get_job_executor`` would recreate a permanently stopped pool."""


class JobExecutorOwnershipError(RuntimeError):
    """Isolated stop refused: mixed / foreign / unproven executor ownership."""

    def __init__(self, reason: str, *, remainder: tuple[str, ...] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.remainder = remainder or (reason,)


@dataclass(frozen=True, slots=True)
class JobExecutorStopResult:
    """Structured production stop outcome for the process-global job executor."""

    ok: bool
    reason: str | None
    attempted: bool
    shutdown_called: bool
    recreate_refused: bool
    stopping: bool
    stopped: bool
    executor_was_absent: bool
    executor_object_id: int | None
    owner_admission_token: int | None
    ownership_ok: bool
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


def _raise_if_recreate_refused() -> None:
    if _RECREATE_REFUSED or _SHUTDOWN_CALLED or _EXECUTOR_STOP_STATE in (
        "stopping",
        "stopped",
    ):
        raise JobExecutorStoppedError(
            "antares job executor permanently stopped; refusing recreate"
        )


def get_job_executor() -> ThreadPoolExecutor:
    """Return the process-global job executor (create once) — foreign/unbound path.

    Use from non-admission callers (scheduler / legacy dispatch). If the pool is
    already admission-bound, this marks **mixed** ownership so isolated stop
    refuses. Prefer ``bind_job_executor_to_admission`` for Antares isolated submits.
    """

    global _JOB_EXECUTOR, _EXECUTOR_OBJECT_ID, _MIXED_OR_FOREIGN
    with _executor_lock:
        _raise_if_recreate_refused()
        if _JOB_EXECUTOR is not None:
            if _OWNER_ADMISSION_TOKEN is not None:
                _MIXED_OR_FOREIGN = True
            return _JOB_EXECUTOR
        _JOB_EXECUTOR = ThreadPoolExecutor(
            max_workers=_max_workers(),
            thread_name_prefix="job-worker",
        )
        _EXECUTOR_OBJECT_ID = id(_JOB_EXECUTOR)
        # Unbound create: not isolated-owned until an admission binds (cannot
        # bind a pre-existing unbound pool — that is mixed/unproven for stop).
        return _JOB_EXECUTOR


def bind_job_executor_to_admission(admission: Any) -> ThreadPoolExecutor:
    """Get/create the pool bound to *this* ``WorkAdmission`` (isolated ownership).

    Exact ``id(admission)`` is the ownership identity for ``stop_isolated_job_executor``.
    Binding a different admission, or using a pool first created unbound/foreign,
    marks mixed ownership and isolated stop will refuse.
    """

    global _JOB_EXECUTOR, _EXECUTOR_OBJECT_ID, _OWNER_ADMISSION_TOKEN, _MIXED_OR_FOREIGN
    token = id(admission)
    with _executor_lock:
        _raise_if_recreate_refused()
        if _JOB_EXECUTOR is not None:
            if _OWNER_ADMISSION_TOKEN is None:
                # Created via foreign/unbound get — cannot prove isolated ownership.
                _MIXED_OR_FOREIGN = True
            elif _OWNER_ADMISSION_TOKEN != token:
                _MIXED_OR_FOREIGN = True
            return _JOB_EXECUTOR
        _JOB_EXECUTOR = ThreadPoolExecutor(
            max_workers=_max_workers(),
            thread_name_prefix="job-worker",
        )
        _EXECUTOR_OBJECT_ID = id(_JOB_EXECUTOR)
        if _OWNER_ADMISSION_TOKEN is None:
            _OWNER_ADMISSION_TOKEN = token
        elif _OWNER_ADMISSION_TOKEN != token:
            _MIXED_OR_FOREIGN = True
        return _JOB_EXECUTOR


def _reset_job_executor_for_tests() -> None:
    """Test-only reset. Production stop must never call this."""

    global _JOB_EXECUTOR, _OBSERVE_EXECUTOR, _EXECUTOR_STOP_STATE, _SHUTDOWN_CALLED
    global _RECREATE_REFUSED, _OWNER_ADMISSION_TOKEN, _MIXED_OR_FOREIGN
    global _EXECUTOR_OBJECT_ID, _WAS_ABSENT_AT_STOP, _CACHED_TERMINAL
    with _executor_lock:
        if _JOB_EXECUTOR is not None:
            _JOB_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        if _OBSERVE_EXECUTOR is not None and _OBSERVE_EXECUTOR is not _JOB_EXECUTOR:
            with contextlib.suppress(Exception):
                _OBSERVE_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        _JOB_EXECUTOR = None
        _OBSERVE_EXECUTOR = None
        _EXECUTOR_STOP_STATE = "idle"
        _SHUTDOWN_CALLED = False
        _RECREATE_REFUSED = False
        _OWNER_ADMISSION_TOKEN = None
        _MIXED_OR_FOREIGN = False
        _EXECUTOR_OBJECT_ID = None
        _WAS_ABSENT_AT_STOP = False
        _CACHED_TERMINAL = None


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
    recreate_refused: bool,
    stopping: bool,
    stopped: bool,
    executor_was_absent: bool,
    ownership_ok: bool,
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
        recreate_refused=recreate_refused,
        stopping=stopping,
        stopped=stopped,
        executor_was_absent=executor_was_absent,
        executor_object_id=_EXECUTOR_OBJECT_ID,
        owner_admission_token=_OWNER_ADMISSION_TOKEN,
        ownership_ok=ownership_ok,
        live_thread_names=names,
        live_thread_count=len(names),
        error_type=error_type,
        error_text=error_text,
    )


def _ownership_refuse_reason(admission: Any) -> str | None:
    """Return refuse reason, or None if isolated ownership is proven for *admission*."""

    token = id(admission)
    if _MIXED_OR_FOREIGN:
        return "ownership_mixed"
    if _OWNER_ADMISSION_TOKEN is not None and _OWNER_ADMISSION_TOKEN != token:
        return "ownership_foreign"
    # Live / observe ref without an admission bind → unproven.
    live_ref = _JOB_EXECUTOR if _JOB_EXECUTOR is not None else _OBSERVE_EXECUTOR
    if live_ref is not None and _OWNER_ADMISSION_TOKEN is None:
        return "ownership_unproven"
    # Absent + never mixed + unbound: isolated admission may claim absent stop
    # (no create, recreate refused) — not a claim over a foreign live pool.
    if live_ref is None and _OWNER_ADMISSION_TOKEN is None:
        return None
    if _OWNER_ADMISSION_TOKEN == token:
        return None
    return "ownership_unproven"


async def stop_isolated_job_executor(
    *,
    admission: Any,
    timeout: float | None = 30.0,
) -> JobExecutorStopResult:
    """Production stop for the admission-bound Antares job executor (Q-EX2).

    ``admission`` must be the exact ``WorkAdmission`` that owns the pool bind.
    Ownership is checked **before** shutdown and **before** returning any
    cached terminal result. ``timeout is None`` observes an already-started
    shutdown until threads are gone (no new shutdown, no new deadline).
    """

    global _EXECUTOR_STOP_STATE, _SHUTDOWN_CALLED, _JOB_EXECUTOR, _OBSERVE_EXECUTOR
    global _RECREATE_REFUSED, _OWNER_ADMISSION_TOKEN, _WAS_ABSENT_AT_STOP
    global _CACHED_TERMINAL, _EXECUTOR_OBJECT_ID

    ownership_reason = _ownership_refuse_reason(admission)
    if ownership_reason is not None:
        # No side effects: do not shutdown, do not flip recreate/stop state.
        live = _live_job_worker_threads(
            _JOB_EXECUTOR if _JOB_EXECUTOR is not None else _OBSERVE_EXECUTOR
        )
        return _snapshot_stop_fields(
            ok=False,
            reason=ownership_reason,
            attempted=False,
            shutdown_called=_SHUTDOWN_CALLED,
            recreate_refused=_RECREATE_REFUSED,
            stopping=_EXECUTOR_STOP_STATE == "stopping",
            stopped=_EXECUTOR_STOP_STATE == "stopped" and not live,
            executor_was_absent=_WAS_ABSENT_AT_STOP
            or (_JOB_EXECUTOR is None and _OBSERVE_EXECUTOR is None and not _SHUTDOWN_CALLED),
            ownership_ok=False,
            live=live,
        )

    # Ownership OK — cached terminal only after proof.
    if _CACHED_TERMINAL is not None and _CACHED_TERMINAL.stopped:
        return _CACHED_TERMINAL

    deadline: float | None
    if timeout is None:
        deadline = None
    else:
        deadline = time.monotonic() + max(0.0, float(timeout))

    error_type: str | None = None
    error_text: str | None = None
    executor: ThreadPoolExecutor | None

    with _executor_lock:
        executor = _JOB_EXECUTOR if _JOB_EXECUTOR is not None else _OBSERVE_EXECUTOR

        if (
            _EXECUTOR_STOP_STATE == "stopped"
            and not _live_job_worker_threads(executor)
        ):
            result = _snapshot_stop_fields(
                ok=True,
                reason=None,
                attempted=True,
                shutdown_called=_SHUTDOWN_CALLED,
                recreate_refused=True,
                stopping=False,
                stopped=True,
                executor_was_absent=_WAS_ABSENT_AT_STOP or executor is None,
                ownership_ok=True,
                live=(),
            )
            _CACHED_TERMINAL = result
            return result

        if executor is None and not _SHUTDOWN_CALLED:
            # Absent: do not create. Refuse recreate without claiming shutdown().
            _RECREATE_REFUSED = True
            _EXECUTOR_STOP_STATE = "stopped"
            _WAS_ABSENT_AT_STOP = True
            if _OWNER_ADMISSION_TOKEN is None:
                _OWNER_ADMISSION_TOKEN = id(admission)
            result = _snapshot_stop_fields(
                ok=True,
                reason=None,
                attempted=True,
                shutdown_called=False,
                recreate_refused=True,
                stopping=False,
                stopped=True,
                executor_was_absent=True,
                ownership_ok=True,
                live=(),
            )
            _CACHED_TERMINAL = result
            return result

        if not _SHUTDOWN_CALLED:
            if deadline is not None and time.monotonic() >= deadline:
                return _snapshot_stop_fields(
                    ok=False,
                    reason="deadline_before_executor_shutdown",
                    attempted=True,
                    shutdown_called=False,
                    recreate_refused=_RECREATE_REFUSED,
                    stopping=False,
                    stopped=False,
                    executor_was_absent=False,
                    ownership_ok=True,
                    live=_live_job_worker_threads(executor),
                )
            assert executor is not None
            try:
                # Production: never cancel Accepted Futures.
                executor.shutdown(wait=False, cancel_futures=False)
            except TypeError:
                executor.shutdown(wait=False)
            except Exception as exc:  # noqa: BLE001
                error_type = type(exc).__name__
                error_text = str(exc)[:200]
                return _snapshot_stop_fields(
                    ok=False,
                    reason=f"executor_shutdown_failed:{error_type}",
                    attempted=True,
                    shutdown_called=False,
                    recreate_refused=_RECREATE_REFUSED,
                    stopping=False,
                    stopped=False,
                    executor_was_absent=False,
                    ownership_ok=True,
                    live=_live_job_worker_threads(executor),
                    error_type=error_type,
                    error_text=error_text,
                )
            _SHUTDOWN_CALLED = True
            _RECREATE_REFUSED = True
            _EXECUTOR_STOP_STATE = "stopping"
            _OBSERVE_EXECUTOR = executor
            # Drop get-path reference so submit/get cannot reuse; keep observe ref.
            _JOB_EXECUTOR = None
            if _EXECUTOR_OBJECT_ID is None:
                _EXECUTOR_OBJECT_ID = id(executor)
        else:
            _EXECUTOR_STOP_STATE = "stopping"
            _RECREATE_REFUSED = True
            if executor is not None:
                _OBSERVE_EXECUTOR = executor
                _JOB_EXECUTOR = None

    # Observe threads without blocking the caller event loop (no wait=True).
    observe = _OBSERVE_EXECUTOR
    while True:
        live = _live_job_worker_threads(observe)
        if not live:
            with _executor_lock:
                _EXECUTOR_STOP_STATE = "stopped"
                _JOB_EXECUTOR = None
                # Keep _OBSERVE_EXECUTOR cleared after terminal; identity preserved.
                _OBSERVE_EXECUTOR = None
                _RECREATE_REFUSED = True
            result = _snapshot_stop_fields(
                ok=True,
                reason=None,
                attempted=True,
                shutdown_called=True,
                recreate_refused=True,
                stopping=False,
                stopped=True,
                executor_was_absent=False,
                ownership_ok=True,
                live=(),
            )
            _CACHED_TERMINAL = result
            return result

        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with _executor_lock:
                    _EXECUTOR_STOP_STATE = "stopping"
                # Partial snapshot only — not a cached terminal owner result.
                return _snapshot_stop_fields(
                    ok=False,
                    reason="deadline_executor_threads",
                    attempted=True,
                    shutdown_called=True,
                    recreate_refused=True,
                    stopping=True,
                    stopped=False,
                    executor_was_absent=False,
                    ownership_ok=True,
                    live=live,
                )
            slice_timeout = min(0.05, remaining)
        else:
            slice_timeout = 0.05

        def _join_slice(
            threads: tuple[threading.Thread, ...] = live,
            timeout: float = slice_timeout,
        ) -> None:
            per = timeout / max(1, len(threads))
            for thread in threads:
                thread.join(timeout=per)

        await asyncio.to_thread(_join_slice)
        await asyncio.sleep(0)
