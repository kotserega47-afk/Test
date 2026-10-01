"""Isolated Antares work admission. Default unbound — mixed is unchanged."""

from __future__ import annotations

import asyncio
import enum
import logging
import secrets
import threading
from concurrent.futures import Future
from dataclasses import dataclass

from core.job_dispatch import bind_job_executor_to_admission
from core.job_runner import Actor, request_job
from modules.antares.auto_enable_continuation import (
    AutoEnableContinuationRecord,
    IsolatedAutoEnableEnqueueRejected,
    bind_thread_continuation,
    clear_thread_continuation,
    current_auto_enable_continuation,
    make_auto_enable_wrapper,
)

log = logging.getLogger(__name__)

ADMISSION_CLOSED_REPLY = "⚠️ Сейчас не принимаем новую работу."

_bound: "WorkAdmission | None" = None
_bound_gate = threading.Lock()


class AdmissionState(enum.Enum):
    UNBOUND = "unbound"
    BOUND_CLOSED = "bound_closed"
    OPEN = "open"
    SEALED = "sealed"


class AdmissionTransitionError(RuntimeError):
    """Illegal bind/open/seal transition."""


class AdmissionStopError(RuntimeError):
    """Stop requested from a non-loop thread without an owner loop."""


@dataclass(frozen=True)
class AdmissionRejected:
    state: AdmissionState


@dataclass(frozen=True)
class AdmissionAccepted:
    future: Future


@dataclass(frozen=True)
class AdmissionQueued:
    """Successful queue admit. Diagnostic qsize is not this object."""


class WorkAdmission:
    """Process-local admission: bound/closed → open → sealed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = AdmissionState.UNBOUND
        self._ae_continuations: dict[str, AutoEnableContinuationRecord] = {}
        self._accepted_executor_futures: set[Future] = set()
        self._accepted_executor_waiters: list[asyncio.Future] = []
        self._accepted_executor_wait_armed = threading.Event()

    @property
    def state(self) -> AdmissionState:
        with self._lock:
            return self._state

    def _bind_instance(self) -> None:
        with self._lock:
            if self._state is not AdmissionState.UNBOUND:
                raise AdmissionTransitionError(
                    f"bind requires unbound, got {self._state.value}"
                )
            self._state = AdmissionState.BOUND_CLOSED

    def open(self) -> None:
        with self._lock:
            if self._state is not AdmissionState.BOUND_CLOSED:
                raise AdmissionTransitionError(
                    f"open requires bound_closed, got {self._state.value}"
                )
            self._state = AdmissionState.OPEN

    def seal(self) -> None:
        with self._lock:
            if self._state is AdmissionState.UNBOUND:
                raise AdmissionTransitionError("seal requires a bound admission")
            self._state = AdmissionState.SEALED

    def _register_accepted_executor_future(self, future: Future) -> None:
        """Insert under the same lock as the OPEN-check and executor.submit."""

        self._accepted_executor_futures.add(future)

    def _attach_accepted_executor_callback(self, future: Future) -> None:
        """One accounting callback. Must run after releasing ``_lock``."""

        future.add_done_callback(self._on_accepted_executor_future_done)

    def _on_accepted_executor_future_done(self, future: Future) -> None:
        with self._lock:
            self._accepted_executor_futures.discard(future)
            waiters: list[asyncio.Future] = []
            if not self._accepted_executor_futures:
                waiters = list(self._accepted_executor_waiters)
                self._accepted_executor_waiters.clear()
        for waiter in waiters:
            self._complete_accepted_executor_waiter(waiter)

    def _complete_accepted_executor_waiter(self, waiter: asyncio.Future) -> None:
        def _set() -> None:
            if not waiter.done():
                waiter.set_result(None)

        try:
            waiter.get_loop().call_soon_threadsafe(_set)
        except RuntimeError:
            pass

    def accepted_executor_futures(self) -> tuple[Future, ...]:
        with self._lock:
            return tuple(self._accepted_executor_futures)

    async def wait_accepted_executor_work(self) -> None:
        """Wait until no Accepted executor Futures remain on this admission.

        This is **not** a full isolated drain. Completing after ``seal()``
        does not prove WE queues, registry daemons, sender, or resource
        shutdown are idle.

        Cancelling or timing out this await does not cancel those Futures,
        does not revoke Auto-Enable continuation, and does not unregister
        live work. A later wait uses the same submit-time callback.
        """

        loop = asyncio.get_running_loop()
        while True:
            waiter: asyncio.Future = loop.create_future()
            with self._lock:
                if not self._accepted_executor_futures:
                    return
                self._accepted_executor_waiters.append(waiter)
                self._accepted_executor_wait_armed.set()
            try:
                await waiter
            except asyncio.CancelledError:
                with self._lock:
                    try:
                        self._accepted_executor_waiters.remove(waiter)
                    except ValueError:
                        pass
                raise

    def submit_if_open(
        self,
        executor,
        fn,
        /,
        *args,
        **kwargs,
    ) -> AdmissionAccepted | AdmissionRejected:
        with self._lock:
            if self._state is not AdmissionState.OPEN:
                return AdmissionRejected(self._state)
            future = executor.submit(fn, *args, **kwargs)
            self._register_accepted_executor_future(future)
        self._attach_accepted_executor_callback(future)
        return AdmissionAccepted(future)

    def submit_auto_enable_run_if_open(
        self,
        executor,
        fn,
        /,
        *args,
        **kwargs,
    ) -> AdmissionAccepted | AdmissionRejected:
        with self._lock:
            if self._state is not AdmissionState.OPEN:
                return AdmissionRejected(self._state)
            token = secrets.token_urlsafe(16)
            record = AutoEnableContinuationRecord(token=token, admission=self)
            self._ae_continuations[token] = record
            try:
                wrapper = make_auto_enable_wrapper(self, token, fn, args, kwargs)
                future = executor.submit(wrapper)
            except BaseException:
                self._ae_continuations.pop(token, None)
                record.state = "revoked"
                raise
            record.orchestrator_future = future
            self._register_accepted_executor_future(future)
        self._attach_accepted_executor_callback(future)
        return AdmissionAccepted(future)

    def activate_auto_enable_continuation(self, token: str) -> None:
        with self._lock:
            record = self._ae_continuations.get(token)
            if record is None or record.state != "pending":
                raise IsolatedAutoEnableEnqueueRejected(
                    "auto-enable continuation cannot be activated"
                )
            record.state = "active"
            record.owner_thread = threading.current_thread()
            bind_thread_continuation(record)

    def revoke_auto_enable_continuation(self, token: str) -> None:
        with self._lock:
            record = self._ae_continuations.pop(token, None)
            if record is not None:
                record.state = "revoked"
            current = current_auto_enable_continuation()
            if current is not None and (record is None or current is record):
                clear_thread_continuation()

    def put_nowait_if_open(self, queue, item) -> AdmissionQueued | AdmissionRejected:
        with self._lock:
            if self._state is not AdmissionState.OPEN:
                return AdmissionRejected(self._state)
            queue.put_nowait(item)
            return AdmissionQueued()

    def submit_job_if_open(
        self,
        job_type: str,
        actor: Actor,
        *,
        force_rules_sync: bool = False,
    ) -> AdmissionAccepted | AdmissionRejected:
        return self.submit_if_open(
            bind_job_executor_to_admission(self),
            request_job,
            job_type,
            actor,
            force_rules_sync=force_rules_sync,
        )


class AdmittedJob:
    """Observe an accepted Future immediately; do not cancel it with the waiter."""

    def __init__(
        self,
        future: Future,
        logger: object,
        loop: asyncio.AbstractEventLoop,
        label: str,
        *,
        kind: str,
    ) -> None:
        self.future = future
        self._logger = logger
        self._loop = loop
        self._label = label
        self._kind = kind
        self._af: asyncio.Future = loop.create_future()
        self._lock = threading.Lock()
        self._job_logged = False
        self._error: BaseException | None = None
        self._result = None
        self._done = threading.Event()
        future.add_done_callback(self._on_done)

    def _on_done(self, cf: Future) -> None:
        try:
            value = cf.result()
        except Exception as exc:
            with self._lock:
                self._error = exc
                log_now = not self._job_logged
                self._job_logged = True
                self._done.set()
            if log_now:
                if self._kind == "job":
                    self._logger.exception("admitted %s job failed", self._label)
                else:
                    self._logger.exception("admitted %s work failed", self._label)
            self._notify()
            return
        with self._lock:
            self._result = value
            self._done.set()
        self._notify()

    def _notify(self) -> None:
        def _set() -> None:
            if not self._af.done():
                self._af.set_result(None)

        try:
            self._loop.call_soon_threadsafe(_set)
        except RuntimeError:
            pass

    @property
    def job_error(self) -> BaseException | None:
        with self._lock:
            return self._error

    def job_logged(self) -> bool:
        with self._lock:
            return self._job_logged

    def _take(self):
        with self._lock:
            if not self._done.is_set():
                raise RuntimeError("admitted job is not finished")
            if self._error is not None:
                raise self._error
            return self._result

    async def wait(self):
        try:
            if self._done.is_set():
                return self._take()
            if self._loop.is_closed():
                await asyncio.to_thread(self._done.wait)
                return self._take()
            await self._af
            return self._take()
        except asyncio.CancelledError:
            raise


def watch_admitted_future(
    future: Future,
    logger: object,
    *,
    job_type: str | None = None,
    work: str | None = None,
) -> AdmittedJob:
    """Attach observation before any await on the caller."""

    if (job_type is None) == (work is None):
        raise TypeError("watch_admitted_future requires exactly one of job_type or work")
    if job_type is not None:
        return AdmittedJob(
            future, logger, asyncio.get_running_loop(), job_type, kind="job"
        )
    return AdmittedJob(future, logger, asyncio.get_running_loop(), work, kind="work")


def bound_admission() -> WorkAdmission | None:
    return _bound


def require_valid_auto_enable_continuation(admission: WorkAdmission) -> AutoEnableContinuationRecord:
    """Refuse isolated enqueue unless this thread holds an active continuation on *this* instance."""

    with admission._lock:
        record = current_auto_enable_continuation()
        if record is None:
            raise IsolatedAutoEnableEnqueueRejected(
                "isolated auto-enable enqueue requires an active continuation"
            )
        if record.admission is not admission:
            raise IsolatedAutoEnableEnqueueRejected(
                "auto-enable continuation belongs to another admission"
            )
        live = admission._ae_continuations.get(record.token)
        if live is not record or record.state != "active":
            raise IsolatedAutoEnableEnqueueRejected(
                "auto-enable continuation is not active"
            )
        if record.owner_thread is not threading.current_thread():
            raise IsolatedAutoEnableEnqueueRejected(
                "auto-enable continuation is bound to another thread"
            )
        return record


def bind_antares_admission(admission: WorkAdmission) -> None:
    global _bound
    with _bound_gate:
        if _bound is not None:
            raise AdmissionTransitionError("antares admission already bound")
        admission._bind_instance()
        _bound = admission


def reset_antares_admission_for_tests() -> None:
    global _bound
    with _bound_gate:
        _bound = None


def request_antares_stop(
    stop: asyncio.Event,
    admission: WorkAdmission,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Seal first, then wake ``stop`` on the owner loop thread only."""

    admission.seal()
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        if loop is not None and loop is not running:
            raise AdmissionStopError("request_antares_stop loop is not the running loop")
        stop.set()
        return
    if loop is None:
        raise AdmissionStopError(
            "request_antares_stop from a non-loop thread requires owner loop"
        )
    loop.call_soon_threadsafe(stop.set)
