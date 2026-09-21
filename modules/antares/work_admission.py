"""Isolated Antares work admission. Default unbound — mixed is unchanged."""

from __future__ import annotations

import asyncio
import enum
import logging
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Callable

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
    """Seal first, then wake the lifecycle Event on the loop thread."""

    admission.seal()
    target = loop
    running: asyncio.AbstractEventLoop | None
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if target is None:
        target = running
    if running is not None and running is target:
        stop.set()
        return
    if target is not None:
        target.call_soon_threadsafe(stop.set)
        return
    stop.set()


def observe_admitted_future(future: Future, logger: object) -> Callable[[Future], None]:
    def _done(cf: Future) -> None:
        try:
            cf.result()
        except Exception:
            logger.exception("admitted job failed after waiter stopped")

    return _done


async def await_admitted_future(future: Future, logger: object):
    """Wait for an accepted concurrent Future without cancelling it."""

    loop = asyncio.get_running_loop()
    af: asyncio.Future = loop.create_future()

    def _done(cf: Future) -> None:
        if af.done():
            observe_admitted_future(cf, logger)(cf)
            return
        try:
            af.set_result(cf.result())
        except Exception as exc:
            af.set_exception(exc)

    def _schedule(cf: Future) -> None:
        try:
            loop.call_soon_threadsafe(_done, cf)
        except RuntimeError:
            observe_admitted_future(cf, logger)(cf)

    future.add_done_callback(_schedule)
    try:
        return await af
    except asyncio.CancelledError:
        if not future.done():
            future.add_done_callback(observe_admitted_future(future, logger))
        raise
