"""Isolated Antares work admission. Default unbound — mixed is unchanged."""

from __future__ import annotations

import asyncio
import enum
import logging
import threading
from concurrent.futures import Future
from dataclasses import dataclass

from core.job_dispatch import get_job_executor
from core.job_runner import Actor, request_job

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


class WorkAdmission:
    """Process-local admission: bound/closed → open → sealed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = AdmissionState.UNBOUND

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

    def submit_job_if_open(
        self,
        job_type: str,
        actor: Actor,
        *,
        force_rules_sync: bool = False,
    ) -> AdmissionAccepted | AdmissionRejected:
        with self._lock:
            if self._state is not AdmissionState.OPEN:
                return AdmissionRejected(self._state)
            try:
                future = get_job_executor().submit(
                    request_job,
                    job_type,
                    actor,
                    force_rules_sync=force_rules_sync,
                )
            except Exception:
                raise
            return AdmissionAccepted(future)


class AdmittedJob:
    """Observe an accepted Future immediately; do not cancel it with the waiter."""

    def __init__(self, future: Future, logger: object, loop: asyncio.AbstractEventLoop) -> None:
        self.future = future
        self._logger = logger
        self._loop = loop
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
                self._logger.exception("admitted wallet job failed")
            self._publish(None, exc)
            return
        with self._lock:
            self._result = value
            self._done.set()
        self._publish(value, None)

    def _publish(self, value, exc: BaseException | None) -> None:
        def _set() -> None:
            if self._af.done():
                return
            if exc is not None:
                self._af.set_exception(exc)
            else:
                self._af.set_result(value)

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
            if self._loop.is_closed() or (self._done.is_set() and not self._af.done()):
                await asyncio.to_thread(self._done.wait)
                return self._take()
            return await self._af
        except asyncio.CancelledError:
            raise


def watch_admitted_future(future: Future, logger: object) -> AdmittedJob:
    """Attach observation before any await on the caller."""

    return AdmittedJob(future, logger, asyncio.get_running_loop())


def bound_admission() -> WorkAdmission | None:
    return _bound


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
