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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from modules.antares.work_admission import AdmissionState, WorkAdmission

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
    """Immutable intermediate or terminal view.

    ``snapshot()`` is **read-only**: it never mutates session state or publishes
    Events. ``drain_expired`` / ``may_start_new_destructive_phases`` are derived
    from the current clock (watcher is not required for permission truth).

    ``owner_task_alive_at_publish`` / ``cleanup_task_alive_at_publish`` are
    values frozen at the moment this snapshot object was built. For a terminal
    snapshot they stay fixed; use :meth:`ShutdownSession.current_task_liveness`
    for current Task liveness.
    """

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
    owner_task_alive_at_publish: bool
    cleanup_task_alive_at_publish: bool


@dataclass(frozen=True)
class TaskLiveness:
    """Current Task liveness (not frozen into a terminal snapshot)."""

    owner_task_alive: bool
    cleanup_task_alive: bool


@dataclass(frozen=True)
class ShutdownTerminalResult:
    """Available only at ``SESSION_TERMINAL``."""

    snapshot: ShutdownSnapshot
    primary: BaseException | None
    cleanup_error: BaseException | None
    cleanup_result: Any
    caller_causes: tuple[BaseException, ...] = ()


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
        # Frozen lifecycle context identity (no swap after create).
        self._admission = host.admission
        self._stop = host.stop
        self._loop = host.loop
        self._path = path
        self._cause = cause
        self._primary = primary
        self._caller_causes: list[BaseException] = []
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
        self._error_tags: list[str] = []

        self._owner_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_callback: CleanupCallback | None = None
        self._cleanup_started = False
        self._cleanup_finalize_lock = False

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

    @property
    def primary(self) -> BaseException | None:
        return self._primary

    @property
    def caller_causes(self) -> tuple[BaseException, ...]:
        return tuple(self._caller_causes)

    def _publish(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def _owner_alive(self) -> bool:
        return self._owner_task is not None and not self._owner_task.done()

    def _cleanup_alive(self) -> bool:
        return self._cleanup_task is not None and not self._cleanup_task.done()

    def _require_owner_loop(self) -> None:
        self._host.require_owner_loop()
        if (
            self._host.admission is not self._admission
            or self._host.stop is not self._stop
            or self._host.loop is not self._loop
        ):
            raise ShutdownSessionError("lifecycle context identity changed")

    def _drain_deadline_passed(self) -> bool:
        if self._drain_expired:
            return True
        if self._shutdown_deadline is None:
            return False
        return self._clock.monotonic() >= self._shutdown_deadline

    def _may_start_new_destructive_phases(self) -> bool:
        if self._terminal is not None:
            return False
        if self._cleanup_started:
            return False
        if self._path is not ShutdownPath.POST_OPEN:
            return False
        if self._drain_deadline_passed():
            return False
        return True

    def _effective_session_state(self) -> SessionState:
        """Derived view for read-only snapshots (does not mutate ``_state``)."""

        if self._terminal is not None:
            return SessionState.SESSION_TERMINAL
        if self._cleanup_started:
            return self._state
        if (
            self._path is ShutdownPath.POST_OPEN
            and self._drain_deadline_passed()
            and self._state is SessionState.RUNNING
        ):
            return SessionState.DRAIN_SNAPSHOT
        return self._state

    def _compute_remainder(self) -> tuple[str, ...]:
        """Current remainders + durable diagnostics (not stale cleared leftovers)."""

        out: list[str] = []
        if self._drain_deadline_passed():
            out.append("drain_deadline_exceeded")
        if self._path is ShutdownPath.POST_OPEN and not self._producers_attested:
            out.append("producers_incomplete")
        if self._cleanup_status is CleanupStatus.NOT_STARTED:
            out.append("ptb_cleanup_not_started")
        elif self._cleanup_status in (
            CleanupStatus.IN_PROGRESS,
            CleanupStatus.IN_PROGRESS_OBSERVE_EXCEEDED,
        ):
            out.append("ptb_cleanup_in_progress")
        if self._application_http is ApplicationHttpState.OPEN:
            # Still-open HTTP is a real leftover only until successful cleanup close.
            if self._cleanup_status is CleanupStatus.NOT_STARTED:
                out.append("application_http_open")
            elif self._cleanup_status in (
                CleanupStatus.IN_PROGRESS,
                CleanupStatus.IN_PROGRESS_OBSERVE_EXCEEDED,
            ):
                out.append("application_http_open")
            elif self._cleanup_error is not None:
                out.append("application_http_open")
        for tag in self._error_tags:
            if tag not in out:
                out.append(tag)
        return tuple(out)

    def _note_drain_expired(self) -> None:
        """Owner-loop only: persist drain expiry flag / state (idempotent)."""

        self._require_owner_loop()
        if self._terminal is not None:
            self._drain_expired = True
            return
        newly = not self._drain_expired
        self._drain_expired = True
        if newly and not self._cleanup_started and self._state is SessionState.RUNNING:
            self._state = SessionState.DRAIN_SNAPSHOT

    def _build_snapshot(self) -> ShutdownSnapshot:
        overall: bool | None
        if self._terminal is None:
            overall = None
        else:
            overall = False if self._cleanup_error is not None else None
        return ShutdownSnapshot(
            session_state=self._effective_session_state(),
            path=self._path,
            cause=self._cause,
            shutdown_deadline=self._shutdown_deadline,
            cleanup_observe_deadline=self._cleanup_observe_deadline,
            cleanup_status=self._cleanup_status,
            application_http=self._application_http,
            drain_expired=self._drain_deadline_passed(),
            may_start_new_destructive_phases=self._may_start_new_destructive_phases(),
            producers_complete_attested=self._producers_attested,
            is_terminal=self._terminal is not None,
            overall_ok=overall,
            remainder=self._compute_remainder(),
            primary_exc_type=_exc_type_name(self._primary),
            cleanup_error_type=_exc_type_name(self._cleanup_error),
            owner_task_alive_at_publish=self._owner_alive(),
            cleanup_task_alive_at_publish=self._cleanup_alive(),
        )

    def snapshot(self) -> ShutdownSnapshot:
        """Read-only view. Never mutates state or publishes Events."""

        if self._terminal is not None:
            return self._terminal.snapshot
        return self._build_snapshot()

    def current_task_liveness(self) -> TaskLiveness:
        """Current owner/cleanup Task liveness (not the frozen terminal fields)."""

        return TaskLiveness(
            owner_task_alive=self._owner_alive(),
            cleanup_task_alive=self._cleanup_alive(),
        )

    def terminal_result(self) -> ShutdownTerminalResult | None:
        return self._terminal

    def _set_terminal(self) -> None:
        if self._terminal is not None:
            return
        self._state = SessionState.SESSION_TERMINAL
        overall: bool | None = False if self._cleanup_error is not None else None
        snap = ShutdownSnapshot(
            session_state=SessionState.SESSION_TERMINAL,
            path=self._path,
            cause=self._cause,
            shutdown_deadline=self._shutdown_deadline,
            cleanup_observe_deadline=self._cleanup_observe_deadline,
            cleanup_status=self._cleanup_status,
            application_http=self._application_http,
            drain_expired=self._drain_deadline_passed(),
            may_start_new_destructive_phases=False,
            producers_complete_attested=self._producers_attested,
            is_terminal=True,
            overall_ok=overall,
            remainder=self._compute_remainder(),
            primary_exc_type=_exc_type_name(self._primary),
            cleanup_error_type=_exc_type_name(self._cleanup_error),
            owner_task_alive_at_publish=self._owner_alive(),
            cleanup_task_alive_at_publish=False,
        )
        self._terminal = ShutdownTerminalResult(
            snapshot=snap,
            primary=self._primary,
            cleanup_error=self._cleanup_error,
            cleanup_result=self._cleanup_result,
            caller_causes=tuple(self._caller_causes),
        )
        self._cleanup_finished.set()
        self._publish()

    def _add_error_tag(self, tag: str) -> None:
        if tag not in self._error_tags:
            self._error_tags.append(tag)

    def _note_cleanup_aborted(
        self, exc: BaseException, remainder_key: str
    ) -> None:
        if self._cleanup_error is None:
            self._cleanup_error = exc
        self._add_error_tag(remainder_key)
        # Task finished / aborted — not "still in progress"; no successful HTTP claim.
        self._cleanup_status = CleanupStatus.DONE
        self._application_http = ApplicationHttpState.OPEN

    def _finalize_cleanup_task_end(self, task: asyncio.Task[Any]) -> None:
        """Ensure SESSION_TERMINAL when owned cleanup Task ends (incl. pre-start cancel)."""

        if self._terminal is not None or self._cleanup_finalize_lock:
            return
        self._cleanup_finalize_lock = True
        try:
            if task.cancelled():
                self._note_cleanup_aborted(
                    asyncio.CancelledError(), "cleanup_task_cancelled"
                )
                self._set_terminal()
                return
            exc = task.exception()
            if exc is not None and self._cleanup_error is None:
                self._cleanup_error = exc
                self._add_error_tag("cleanup_task_exception")
                self._cleanup_status = CleanupStatus.DONE
                self._application_http = ApplicationHttpState.OPEN
                self._set_terminal()
        finally:
            self._cleanup_finalize_lock = False

    def _start_owner_task(self) -> None:
        if self._owner_task is not None:
            return
        if self._path is ShutdownPath.STARTUP:
            self._shutdown_deadline = None
        else:
            self._shutdown_deadline = self._clock.monotonic() + self._drain_timeout
        self._owner_task = self._loop.create_task(
            self._owner_main(), name="antares-shutdown-session-owner"
        )
        self._owner_task.add_done_callback(self._on_owner_done)

    def _on_owner_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._add_error_tag("owner_task_exception")
            if self._primary is None:
                self._primary = exc
            self._publish()

    async def _owner_main(self) -> None:
        try:
            if self._path is ShutdownPath.POST_OPEN:
                await self._post_open_arm_effects()
                self._arm_effects_done.set()
                self._publish()
                drain_watch = self._loop.create_task(
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
                await self._startup_arm_effects()
                self._arm_effects_done.set()
                self._publish()
                await self._cleanup_finished.wait()
        finally:
            if self._cleanup_task is not None and self._cleanup_task.done():
                self._finalize_cleanup_task_end(self._cleanup_task)

    async def _post_open_arm_effects(self) -> None:
        self._admission.seal()
        self._stop.set()

    async def _startup_arm_effects(self) -> None:
        # Contract: seal if admission is bound; no post-OPEN drain.
        if self._admission.state is not AdmissionState.UNBOUND:
            self._admission.seal()

    async def _watch_drain_deadline(self) -> None:
        deadline = self._shutdown_deadline
        if deadline is None:
            return
        await self._clock.sleep_until(deadline)
        if self._terminal is not None:
            return
        self._note_drain_expired()
        self._publish()

    def record_lifecycle_primary(self, primary: BaseException, cause: ShutdownCause) -> None:
        """Merge cancel/error primary into a live post-OPEN session (not waiter detach)."""

        self._require_owner_loop()
        if self._terminal is not None:
            raise ShutdownSessionError("session already terminal")
        if self._path is not ShutdownPath.POST_OPEN:
            raise ShutdownSessionError("lifecycle primary only for post-OPEN session")
        if self._primary is None:
            self._primary = primary
        else:
            self._caller_causes.append(primary)
        if self._cause is ShutdownCause.REQUEST_STOP and cause in (
            ShutdownCause.CANCEL_AFTER_OPEN,
            ShutdownCause.ERROR_AFTER_OPEN,
        ):
            self._cause = cause
        self._publish()

    def accept_producers_complete(
        self, attestation: ProducersCompleteAttestation
    ) -> None:
        self._require_owner_loop()
        if not isinstance(attestation, ProducersCompleteAttestation):
            raise ShutdownSessionError("producers attestation required")
        if self._path is not ShutdownPath.POST_OPEN:
            raise ShutdownSessionError("producers attestation only for post-OPEN path")
        if self._terminal is not None:
            raise ShutdownSessionError("session already terminal")
        self._producers_attested = True
        self._publish()

    def start_cleanup(self, callback: CleanupCallback) -> asyncio.Task[None]:
        """Start cleanup at most once on the owner loop.

        Post-OPEN full cleanup requires a prior :meth:`accept_producers_complete`.
        Startup path uses this without producers attestation.
        """

        self._require_owner_loop()
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

        self._cleanup_started = True
        self._cleanup_callback = callback
        self._cleanup_status = CleanupStatus.IN_PROGRESS
        self._state = SessionState.CLEANUP_IN_PROGRESS
        self._cleanup_observe_deadline = (
            self._clock.monotonic() + self._cleanup_observe_timeout
        )
        self._cleanup_task = self._loop.create_task(
            self._run_cleanup_observer(), name="antares-shutdown-cleanup"
        )
        self._cleanup_task.add_done_callback(self._on_cleanup_task_done)
        self._publish()
        return self._cleanup_task

    def _on_cleanup_task_done(self, task: asyncio.Task[None]) -> None:
        self._finalize_cleanup_task_end(task)

    async def _run_cleanup_observer(self) -> None:
        assert self._cleanup_callback is not None
        observe_deadline = self._cleanup_observe_deadline
        assert observe_deadline is not None

        observe_watch = self._loop.create_task(
            self._watch_cleanup_observe(observe_deadline),
            name="antares-shutdown-cleanup-observe",
        )
        aborted = False
        try:
            try:
                self._cleanup_result = await self._cleanup_callback()
            except asyncio.CancelledError as exc:
                aborted = True
                self._note_cleanup_aborted(exc, "cleanup_cancelled")
            except BaseException as exc:
                aborted = True
                self._cleanup_error = exc
                self._add_error_tag("cleanup_failed")
                self._cleanup_status = CleanupStatus.DONE
                self._application_http = ApplicationHttpState.OPEN
            finally:
                observe_watch.cancel()
                try:
                    await observe_watch
                except asyncio.CancelledError:
                    pass

            if not aborted and self._cleanup_error is None:
                self._cleanup_status = CleanupStatus.DONE
                if self._path is ShutdownPath.STARTUP:
                    self._application_http = ApplicationHttpState.STARTUP_CLEANED
                else:
                    self._application_http = ApplicationHttpState.CLEANUP_DONE
            if self._terminal is None:
                self._set_terminal()
        except asyncio.CancelledError as exc:
            # Outer cancel (task cancelled while in finally/await observe).
            if self._terminal is None:
                self._note_cleanup_aborted(exc, "cleanup_task_cancelled")
                self._set_terminal()
            # Do not re-raise: terminal is published; owner must not hang.

    async def _watch_cleanup_observe(self, deadline: float) -> None:
        await self._clock.sleep_until(deadline)
        if (
            self._cleanup_task is not None
            and not self._cleanup_task.done()
            and self._terminal is None
        ):
            self._cleanup_status = CleanupStatus.IN_PROGRESS_OBSERVE_EXCEEDED
            self._state = SessionState.CLEANUP_IN_PROGRESS
            self._publish()

    async def wait_arm_effects(self) -> None:
        self._require_owner_loop()
        await self._arm_effects_done.wait()

    async def wait_terminal(self) -> ShutdownTerminalResult:
        """Wait for SESSION_TERMINAL. Cancelling this await does not cancel owner/cleanup."""

        self._require_owner_loop()
        if self._terminal is not None:
            return self._terminal
        while self._terminal is None:
            changed = self._changed
            finished = self._loop.create_task(self._cleanup_finished.wait())
            pulsed = self._loop.create_task(changed.wait())
            try:
                done, pending = await asyncio.wait(
                    {finished, pulsed},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                finished.cancel()
                pulsed.cancel()
                await asyncio.gather(finished, pulsed, return_exceptions=True)
                raise
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if not task.cancelled():
                    task.result()
        assert self._terminal is not None
        return self._terminal

    async def wait_until(
        self, predicate: Callable[[ShutdownSnapshot], bool]
    ) -> ShutdownSnapshot:
        """Wait until snapshot matches ``predicate``. Cancel detaches this waiter only.

        If the session is already terminal and ``predicate`` is false, returns the
        terminal snapshot (same as after a wake) instead of hanging.
        """

        self._require_owner_loop()
        snap = self.snapshot()
        if predicate(snap):
            return snap
        if snap.is_terminal:
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
    """Lifecycle-owned host: at most one session; frozen admission/stop/loop identity."""

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
        self._admission = admission
        self._stop = stop
        self._loop = loop
        self.drain_timeout = float(drain_timeout)
        self.cleanup_observe_timeout = float(cleanup_observe_timeout)
        self.clock = clock if clock is not None else Clock()
        self._session: ShutdownSession | None = None

    @property
    def admission(self) -> WorkAdmission:
        return self._admission

    @property
    def stop(self) -> asyncio.Event:
        return self._stop

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    @property
    def session(self) -> ShutdownSession | None:
        return self._session

    def require_owner_loop(self) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise ShutdownSessionError(
                "shutdown session arm/await requires the owner running loop"
            ) from exc
        if running is not self._loop:
            raise ShutdownSessionError("foreign event loop rejected")

    def _arm(
        self,
        *,
        path: ShutdownPath,
        cause: ShutdownCause,
        primary: BaseException | None,
        had_open: bool,
    ) -> ShutdownSession:
        self.require_owner_loop()

        if path is ShutdownPath.POST_OPEN and not had_open:
            raise ShutdownSessionError(
                "post-OPEN arm requires proof that admission was OPEN; "
                "current SEALED alone is insufficient"
            )
        if path is ShutdownPath.STARTUP:
            if had_open:
                raise ShutdownSessionError(
                    "startup failure path cannot be used after admission was OPEN"
                )
            # Actual admission state: cannot bypass producer gate while OPEN.
            if self._admission.state is AdmissionState.OPEN:
                raise ShutdownSessionError(
                    "startup failure refused while admission is OPEN"
                )

        if self._session is not None:
            existing = self._session
            if existing.terminal_result() is not None:
                # Read-only repeat: no new deadline, no mutation of result.
                return existing
            if existing.path is not path:
                raise ShutdownSessionError(
                    f"incompatible arm path {path.value} for live "
                    f"{existing.path.value} session"
                )
            if primary is not None and path is ShutdownPath.POST_OPEN:
                existing.record_lifecycle_primary(primary, cause)
            return existing

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
        return self._arm(
            path=ShutdownPath.STARTUP,
            cause=ShutdownCause.STARTUP_FAILURE,
            primary=primary,
            had_open=had_open,
        )

    def snapshot(self) -> ShutdownSnapshot | None:
        if self._session is None:
            return None
        return self._session.snapshot()
