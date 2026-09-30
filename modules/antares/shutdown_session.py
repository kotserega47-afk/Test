"""Owner shutdown-session primitive (TASK-49.S).

Lifecycle host owns one :class:`ShutdownSessionHost` per admission/stop context.
This module does **not** wire into ``run_ptb_lifecycle``, ``request_antares_stop``
callers, WE/registry/sender/executor, or Telegram.

Future lifecycle host **must** keep the owner event loop alive until
``SESSION_TERMINAL`` (or process death). This primitive never closes the loop,
never starts a new loop, and never promises bounded process exit.

Q-PTB1 remains open: full post-OPEN cleanup requires an explicit
:class:`ProducersCompleteAttestation` from a future producer-wait primitive
(or a test harness). This module never invents ``producers_complete=True``.
"""

from __future__ import annotations

import asyncio
import enum
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from modules.antares.work_admission import WorkAdmission

CleanupCallback = Callable[[], Awaitable[Any]]


class ShutdownSessionError(RuntimeError):
    """Illegal arm / loop / context / cleanup transition."""


class ShutdownCause(enum.Enum):
    REQUEST_STOP = "request_stop"
    CANCEL_AFTER_OPEN = "cancel_after_open"
    ERROR_AFTER_OPEN = "error_after_open"
    STARTUP_FAILURE = "startup_failure"


class SessionState(enum.Enum):
    RUNNING = "running"
    DRAIN_SNAPSHOT = "drain_snapshot"
    CLEANUP_IN_PROGRESS = "cleanup_in_progress"
    SESSION_TERMINAL = "session_terminal"


class CleanupStatus(enum.Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    IN_PROGRESS_OBSERVE_EXCEEDED = "in_progress_observe_exceeded"
    DONE = "done"


class ApplicationHttpState(enum.Enum):
    OPEN = "open"
    CLEANUP_DONE = "cleanup_done"
    STARTUP_CLEANED = "startup_cleaned"


class ShutdownPath(enum.Enum):
    POST_OPEN = "post_open"
    STARTUP = "startup"


@dataclass(frozen=True)
class ProducersCompleteAttestation:
    """Opaque proof that PTB producers are complete.

    Production code must obtain this from an accepted Q-PTB1 primitive.
    Tests may build one via :meth:`for_tests` only.
    """

    _mark: str = field(default="producers_complete", repr=False)

    @classmethod
    def for_tests(cls) -> ProducersCompleteAttestation:
        return cls(_mark="test")


@dataclass(frozen=True)
class ShutdownSnapshot:
    """Immutable intermediate or terminal view. ``is_terminal`` distinguishes."""

    session_state: SessionState
    path: ShutdownPath
    cause: ShutdownCause
    shutdown_deadline: float | None
    cleanup_observe_deadline: float | None
    cleanup_status: CleanupStatus
    application_http: ApplicationHttpState
    drain_expired: bool
    may_start_new_destructive_phases: bool
    producers_complete_attested: bool
    is_terminal: bool
    overall_ok: bool | None
    remainder: tuple[str, ...]
    primary_exc_type: str | None
    cleanup_error_type: str | None
    owner_task_alive: bool
    cleanup_task_alive: bool


@dataclass(frozen=True)
class ShutdownTerminalResult:
    """Available only at ``SESSION_TERMINAL``."""

    snapshot: ShutdownSnapshot
    primary: BaseException | None
    cleanup_error: BaseException | None
    cleanup_result: Any


class Clock:
    """Monotonic clock + awaitable sleep-until for drain/cleanup observe budgets."""

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep_until(self, deadline: float) -> None:
        delay = deadline - self.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)


class ControllableClock(Clock):
    """Test clock: advance wakes waiters without wall-clock sleeps."""

    def __init__(self, start: float = 1_000.0) -> None:
        self._now = float(start)
        self._generation = 0
        self._wake: asyncio.Event | None = None

    def bind_loop(self) -> None:
        self._wake = asyncio.Event()

    def monotonic(self) -> float:
        return self._now

    async def sleep_until(self, deadline: float) -> None:
        if self._wake is None:
            self._wake = asyncio.Event()
        while self._now < deadline:
            gen = self._generation
            self._wake.clear()
            if self._now >= deadline or self._generation != gen:
                continue
            await self._wake.wait()

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)
        self._generation += 1
        if self._wake is not None:
            self._wake.set()


def _exc_type_name(exc: BaseException | None) -> str | None:
    if exc is None:
        return None
    return type(exc).__name__


class ShutdownSession:
    """One owner shutdown-session (strong-refs owner + cleanup Tasks)."""

    def __init__(
        self,
        *,
        host: ShutdownSessionHost,
        path: ShutdownPath,
        cause: ShutdownCause,
        primary: BaseException | None,
        drain_timeout: float,
        cleanup_observe_timeout: float,
        clock: Clock,
        had_open: bool,
    ) -> None:
        self._host = host
        self._path = path
        self._cause = cause
        self._primary = primary
        self._drain_timeout = float(drain_timeout)
        self._cleanup_observe_timeout = float(cleanup_observe_timeout)
        self._clock = clock
        self._had_open = had_open

        self._shutdown_deadline: float | None = None
        self._cleanup_observe_deadline: float | None = None
        self._state = SessionState.RUNNING
        self._cleanup_status = CleanupStatus.NOT_STARTED
        self._application_http = ApplicationHttpState.OPEN
        self._drain_expired = False
        self._producers_attested = False
        self._cleanup_error: BaseException | None = None
        self._cleanup_result: Any = None
        self._terminal: ShutdownTerminalResult | None = None
        self._remainder: list[str] = []

        self._owner_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_callback: CleanupCallback | None = None
        self._cleanup_started = False

        self._changed = asyncio.Event()
        self._cleanup_finished = asyncio.Event()
        self._arm_effects_done = asyncio.Event()

    @property
    def owner_task(self) -> asyncio.Task[None] | None:
        return self._owner_task

    @property
    def cleanup_task(self) -> asyncio.Task[None] | None:
        return self._cleanup_task

    @property
    def shutdown_deadline(self) -> float | None:
        return self._shutdown_deadline

    @property
    def path(self) -> ShutdownPath:
        return self._path

    @property
    def cause(self) -> ShutdownCause:
        return self._cause

    def _publish(self) -> None:
        self._changed.set()
        # Allow subsequent waiters to block again after consuming a pulse.
        self._changed = asyncio.Event()

    def _owner_alive(self) -> bool:
        return self._owner_task is not None and not self._owner_task.done()

    def _cleanup_alive(self) -> bool:
        return self._cleanup_task is not None and not self._cleanup_task.done()

    def snapshot(self) -> ShutdownSnapshot:
        overall: bool | None
        if self._terminal is None:
            overall = None
        else:
            # Without mandatory phase results, graceful success is unavailable.
            overall = False if self._cleanup_error is not None else None
        return ShutdownSnapshot(
            session_state=self._state,
            path=self._path,
            cause=self._cause,
            shutdown_deadline=self._shutdown_deadline,
            cleanup_observe_deadline=self._cleanup_observe_deadline,
            cleanup_status=self._cleanup_status,
            application_http=self._application_http,
            drain_expired=self._drain_expired,
            may_start_new_destructive_phases=(
                self._path is ShutdownPath.POST_OPEN and not self._drain_expired
            ),
            producers_complete_attested=self._producers_attested,
            is_terminal=self._terminal is not None,
            overall_ok=overall,
            remainder=tuple(self._remainder),
            primary_exc_type=_exc_type_name(self._primary),
            cleanup_error_type=_exc_type_name(self._cleanup_error),
            owner_task_alive=self._owner_alive(),
            cleanup_task_alive=self._cleanup_alive(),
        )

    def terminal_result(self) -> ShutdownTerminalResult | None:
        return self._terminal

    def _set_terminal(self) -> None:
        if self._terminal is not None:
            return
        self._state = SessionState.SESSION_TERMINAL
        snap = self.snapshot()
        # Recompute overall on the terminal snapshot object.
        overall: bool | None = False if self._cleanup_error is not None else None
        snap = ShutdownSnapshot(
            session_state=SessionState.SESSION_TERMINAL,
            path=snap.path,
            cause=snap.cause,
            shutdown_deadline=snap.shutdown_deadline,
            cleanup_observe_deadline=snap.cleanup_observe_deadline,
            cleanup_status=self._cleanup_status,
            application_http=self._application_http,
            drain_expired=snap.drain_expired,
            may_start_new_destructive_phases=False,
            producers_complete_attested=snap.producers_complete_attested,
            is_terminal=True,
            overall_ok=overall,
            remainder=tuple(self._remainder),
            primary_exc_type=snap.primary_exc_type,
            cleanup_error_type=_exc_type_name(self._cleanup_error),
            owner_task_alive=self._owner_alive(),
            cleanup_task_alive=False,
        )
        self._terminal = ShutdownTerminalResult(
            snapshot=snap,
            primary=self._primary,
            cleanup_error=self._cleanup_error,
            cleanup_result=self._cleanup_result,
        )
        self._cleanup_finished.set()
        self._publish()

    def _consume_task_exception(self, task: asyncio.Task[Any]) -> None:
        if not task.done() or task.cancelled():
            return
        exc = task.exception()
        if exc is not None and self._cleanup_error is None and task is self._cleanup_task:
            self._cleanup_error = exc
            rem = "cleanup_task_exception"
            if rem not in self._remainder:
                self._remainder.append(rem)

    def _start_owner_task(self) -> None:
        if self._owner_task is not None:
            return
        if self._path is ShutdownPath.STARTUP:
            self._shutdown_deadline = None
        else:
            self._shutdown_deadline = self._clock.monotonic() + self._drain_timeout
        self._owner_task = asyncio.create_task(
            self._owner_main(), name="antares-shutdown-session-owner"
        )
        self._owner_task.add_done_callback(self._on_owner_done)

    def _on_owner_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            rem = "owner_task_exception"
            if rem not in self._remainder:
                self._remainder.append(rem)
            if self._primary is None:
                self._primary = exc
            # Ensure exception is retrieved (done_callback already did).
            self._publish()

    async def _owner_main(self) -> None:
        try:
            if self._path is ShutdownPath.POST_OPEN:
                await self._post_open_arm_effects()
                self._arm_effects_done.set()
                self._publish()
                drain_watch = asyncio.create_task(
                    self._watch_drain_deadline(), name="antares-shutdown-drain-watch"
                )
                try:
                    await self._cleanup_finished.wait()
                finally:
                    drain_watch.cancel()
                    try:
                        await drain_watch
                    except asyncio.CancelledError:
                        pass
            else:
                self._arm_effects_done.set()
                # Startup: cleanup must be started by host; wait for terminal.
                await self._cleanup_finished.wait()
        finally:
            if self._cleanup_task is not None:
                self._consume_task_exception(self._cleanup_task)

    async def _post_open_arm_effects(self) -> None:
        # seal → stop.set on owner loop (idempotent if request_stop already did).
        self._host.admission.seal()
        self._host.stop.set()

    async def _watch_drain_deadline(self) -> None:
        deadline = self._shutdown_deadline
        if deadline is None:
            return
        await self._clock.sleep_until(deadline)
        if self._terminal is not None:
            return
        if self._cleanup_started:
            return
        self._drain_expired = True
        if "drain_deadline_exceeded" not in self._remainder:
            self._remainder.append("drain_deadline_exceeded")
        if not self._producers_attested:
            if "producers_incomplete" not in self._remainder:
                self._remainder.append("producers_incomplete")
            if "ptb_cleanup_not_started" not in self._remainder:
                self._remainder.append("ptb_cleanup_not_started")
            if "application_http_open" not in self._remainder:
                self._remainder.append("application_http_open")
        self._state = SessionState.DRAIN_SNAPSHOT
        self._publish()

    def accept_producers_complete(
        self, attestation: ProducersCompleteAttestation
    ) -> None:
        if not isinstance(attestation, ProducersCompleteAttestation):
            raise ShutdownSessionError("producers attestation required")
        if self._path is not ShutdownPath.POST_OPEN:
            raise ShutdownSessionError("producers attestation only for post-OPEN path")
        if self._terminal is not None:
            raise ShutdownSessionError("session already terminal")
        self._producers_attested = True
        self._publish()

    def start_cleanup(self, callback: CleanupCallback) -> asyncio.Task[None]:
        """Start cleanup at most once.

        Post-OPEN full cleanup requires a prior :meth:`accept_producers_complete`.
        Startup path uses this without producers attestation.
        """

        if self._cleanup_started:
            if self._cleanup_task is None:
                raise ShutdownSessionError("cleanup started without task")
            return self._cleanup_task
        if self._terminal is not None:
            raise ShutdownSessionError("cannot start cleanup after SESSION_TERMINAL")
        if self._path is ShutdownPath.POST_OPEN and not self._producers_attested:
            raise ShutdownSessionError(
                "post-OPEN cleanup requires ProducersCompleteAttestation (Q-PTB1 open)"
            )
        if self._path is ShutdownPath.POST_OPEN and self._drain_expired:
            # Full cleanup after drain expiry is still allowed once producers are
            # attested on the same non-terminal session (contract §5.4 late path);
            # it does not renew the drain budget.
            pass

        self._cleanup_started = True
        self._cleanup_callback = callback
        self._cleanup_status = CleanupStatus.IN_PROGRESS
        self._state = SessionState.CLEANUP_IN_PROGRESS
        self._cleanup_observe_deadline = (
            self._clock.monotonic() + self._cleanup_observe_timeout
        )
        self._cleanup_task = asyncio.create_task(
            self._run_cleanup_observer(), name="antares-shutdown-cleanup"
        )
        self._cleanup_task.add_done_callback(self._on_cleanup_task_done)
        self._publish()
        return self._cleanup_task

    def _on_cleanup_task_done(self, task: asyncio.Task[None]) -> None:
        self._consume_task_exception(task)

    async def _run_cleanup_observer(self) -> None:
        assert self._cleanup_callback is not None
        observe_deadline = self._cleanup_observe_deadline
        assert observe_deadline is not None

        observe_watch = asyncio.create_task(
            self._watch_cleanup_observe(observe_deadline),
            name="antares-shutdown-cleanup-observe",
        )
        try:
            try:
                self._cleanup_result = await self._cleanup_callback()
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                self._cleanup_error = exc
                if "cleanup_failed" not in self._remainder:
                    self._remainder.append("cleanup_failed")
            finally:
                observe_watch.cancel()
                try:
                    await observe_watch
                except asyncio.CancelledError:
                    pass

            self._cleanup_status = CleanupStatus.DONE
            if self._path is ShutdownPath.STARTUP:
                self._application_http = ApplicationHttpState.STARTUP_CLEANED
            else:
                self._application_http = ApplicationHttpState.CLEANUP_DONE
            self._set_terminal()
        except asyncio.CancelledError:
            if "cleanup_cancelled" not in self._remainder:
                self._remainder.append("cleanup_cancelled")
            raise

    async def _watch_cleanup_observe(self, deadline: float) -> None:
        await self._clock.sleep_until(deadline)
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_status = CleanupStatus.IN_PROGRESS_OBSERVE_EXCEEDED
            if "cleanup_observe_exceeded" not in self._remainder:
                self._remainder.append("cleanup_observe_exceeded")
            # Still CLEANUP_IN_PROGRESS session-wise; not SESSION_TERMINAL.
            self._state = SessionState.CLEANUP_IN_PROGRESS
            self._publish()

    async def wait_arm_effects(self) -> None:
        await self._arm_effects_done.wait()

    async def wait_terminal(self) -> ShutdownTerminalResult:
        """Wait for SESSION_TERMINAL. Cancelling this await does not cancel owner/cleanup."""

        if self._terminal is not None:
            return self._terminal
        while self._terminal is None:
            changed = self._changed
            finished = asyncio.create_task(self._cleanup_finished.wait())
            pulsed = asyncio.create_task(changed.wait())
            try:
                done, pending = await asyncio.wait(
                    {finished, pulsed},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                finished.cancel()
                pulsed.cancel()
                await asyncio.gather(finished, pulsed, return_exceptions=True)
                # Detach this waiter only; session continues.
                raise
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                # Retrieve results/exceptions so they are not left pending.
                if not task.cancelled():
                    task.result()
        assert self._terminal is not None
        return self._terminal

    async def wait_until(
        self, predicate: Callable[[ShutdownSnapshot], bool]
    ) -> ShutdownSnapshot:
        """Wait until snapshot matches ``predicate``. Cancel detaches this waiter only."""

        snap = self.snapshot()
        if predicate(snap):
            return snap
        while True:
            changed = self._changed
            try:
                await changed.wait()
            except asyncio.CancelledError:
                raise
            snap = self.snapshot()
            if predicate(snap):
                return snap
            if snap.is_terminal:
                return snap


class ShutdownSessionHost:
    """Lifecycle-owned host: at most one session; no global completed-session registry."""

    def __init__(
        self,
        *,
        admission: WorkAdmission,
        stop: asyncio.Event,
        loop: asyncio.AbstractEventLoop,
        drain_timeout: float = 30.0,
        cleanup_observe_timeout: float = 5.0,
        clock: Clock | None = None,
    ) -> None:
        self.admission = admission
        self.stop = stop
        self.loop = loop
        self.drain_timeout = float(drain_timeout)
        self.cleanup_observe_timeout = float(cleanup_observe_timeout)
        self.clock = clock if clock is not None else Clock()
        self._session: ShutdownSession | None = None

    @property
    def session(self) -> ShutdownSession | None:
        return self._session

    def _require_owner_loop(self) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise ShutdownSessionError(
                "shutdown session arm/await requires the owner running loop"
            ) from exc
        if running is not self.loop:
            raise ShutdownSessionError("foreign event loop rejected")

    def _arm(
        self,
        *,
        path: ShutdownPath,
        cause: ShutdownCause,
        primary: BaseException | None,
        had_open: bool,
    ) -> ShutdownSession:
        self._require_owner_loop()
        if self._session is not None:
            existing = self._session
            if existing.terminal_result() is not None:
                # Repeat after terminal: observe only; no new deadline / destructive arm.
                return existing
            # Join same live session (no deadline refresh, no second owner Task).
            return existing

        if path is ShutdownPath.POST_OPEN and not had_open:
            raise ShutdownSessionError(
                "post-OPEN arm requires proof that admission was OPEN; "
                "current SEALED alone is insufficient"
            )
        if path is ShutdownPath.STARTUP and had_open:
            raise ShutdownSessionError(
                "startup failure path cannot be used after admission was OPEN"
            )

        session = ShutdownSession(
            host=self,
            path=path,
            cause=cause,
            primary=primary,
            drain_timeout=self.drain_timeout,
            cleanup_observe_timeout=self.cleanup_observe_timeout,
            clock=self.clock,
            had_open=had_open,
        )
        self._session = session
        session._start_owner_task()
        return session

    def arm_request_stop(self, *, had_open: bool) -> ShutdownSession:
        """Arm graceful request-stop (post-OPEN). Does not invent OPEN from SEALED."""

        return self._arm(
            path=ShutdownPath.POST_OPEN,
            cause=ShutdownCause.REQUEST_STOP,
            primary=None,
            had_open=had_open,
        )

    def arm_cancel_after_open(
        self, primary: BaseException, *, had_open: bool = True
    ) -> ShutdownSession:
        if not isinstance(primary, BaseException):
            raise ShutdownSessionError("cancel-after-OPEN requires a primary exception")
        cause = (
            ShutdownCause.CANCEL_AFTER_OPEN
            if isinstance(primary, asyncio.CancelledError)
            else ShutdownCause.ERROR_AFTER_OPEN
        )
        return self._arm(
            path=ShutdownPath.POST_OPEN,
            cause=cause,
            primary=primary,
            had_open=had_open,
        )

    def arm_startup_failure(
        self, primary: BaseException, *, had_open: bool = False
    ) -> ShutdownSession:
        if had_open:
            raise ShutdownSessionError(
                "startup failure must not be used when admission was OPEN"
            )
        return self._arm(
            path=ShutdownPath.STARTUP,
            cause=ShutdownCause.STARTUP_FAILURE,
            primary=primary,
            had_open=False,
        )

    def snapshot(self) -> ShutdownSnapshot | None:
        if self._session is None:
            return None
        return self._session.snapshot()
