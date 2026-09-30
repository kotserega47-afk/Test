"""Q-PTB1 PTB producer-wait primitive (TASK-49.A).

Surveyed stack: **python-telegram-bot 22.8**.

Producers in the supported isolated Antares lifecycle graph
(``run_ptb_lifecycle`` / ``SimpleUpdateProcessor``):

1. Updates entering ``Application.update_queue`` (fetcher ``__update_fetcher``).
2. In-flight ``__process_update_wrapper`` / queue ``_unfinished_tasks``.
3. ``Application.process_update`` (fetcher path **and** any direct call) — tracked
   by wrapping at host install time.
4. ``Application.create_task`` work in ``__create_task_tasks`` (concurrent update
   wrappers, ``block=False`` handlers, error-handler tasks).
5. ``SimpleUpdateProcessor.current_concurrent_updates``.

How **new** producers are stopped (closed set):

- Seal :class:`~modules.antares.ptb_update_intake.AntaresUpdateIntakeQueue`
  (refuses update ``put``; allows PTB ``_STOP_SIGNAL`` for later ``app.stop``).
- Updater must not be running (polling/webhook feed refused).
- Host must be installed **before** producers are in flight (explicit refuse
  otherwise — untracked sequential work cannot wake observation safely).
- After **proven** COMPLETE, new ``process_update`` / ``Application.create_task``
  calls are **rejected** until :meth:`enter_cleanup_phase` (cleanup start).
  Already-accepted in-flight work continues until it finishes; idle proof does
  not replace this entry gate.
- This module never calls ``Application.stop`` / ``shutdown`` and never closes
  Bot HTTP.

**Supported-mode constraint (not silently ignored):** handlers / Antares code
must not spawn untracked ``asyncio.create_task`` outside
``Application.create_task``. That path is outside the observed set; wiring that
needs it is a design blocker for truthful attestation (this primitive refuses
to pretend those tasks are covered).

Completion: under seal, idle epoch stable across one ``call_soon`` barrier
(not ``sleep``-as-proof). Deadline publishes incomplete to waiters without
ending owner observation or cancelling producer Tasks.
"""

from __future__ import annotations

import asyncio
import enum
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from telegram.ext._baseupdateprocessor import SimpleUpdateProcessor

from modules.antares.application_lifecycle import unsupported_application_reasons
from modules.antares.ptb_update_intake import AntaresUpdateIntakeQueue
from modules.antares.shutdown_session import (
    Clock,
    ProducersCompleteAttestation,
    ShutdownSession,
)


class PtbProducerWaitError(RuntimeError):
    """Unsupported configuration or illegal producer-wait transition."""


class ProducerWaitStatus(enum.Enum):
    IDLE = "idle"
    WAITING = "waiting"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(frozen=True)
class ProducerWaitSnapshot:
    status: ProducerWaitStatus
    intake_sealed: bool
    seal_generation: int | None
    queue_empty: bool
    unfinished_tasks: int
    create_task_alive: int
    concurrent_updates: int
    process_update_inflight: int
    application_running: bool
    updater_running: bool
    producers_complete: bool
    remainder: tuple[str, ...]
    owner_procedure_id: int | None
    observation_error_type: str | None = None


@dataclass(frozen=True)
class ProducerWaitOutcome:
    """Result delivered to a waiter (may be incomplete while owner still observes)."""

    snapshot: ProducerWaitSnapshot
    attestation: ProducersCompleteAttestation | None

    def __post_init__(self) -> None:
        complete = self.attestation is not None
        if complete != self.snapshot.producers_complete:
            raise ValueError(
                "ProducerWaitOutcome inconsistency: attestation and "
                "snapshot.producers_complete disagree"
            )
        if complete and self.snapshot.status is not ProducerWaitStatus.COMPLETE:
            raise ValueError(
                "ProducerWaitOutcome inconsistency: attestation requires COMPLETE status"
            )


def _create_task_tasks(app: Any) -> set[asyncio.Task[Any]]:
    tasks = getattr(app, "_Application__create_task_tasks", None)
    if not isinstance(tasks, set):
        raise PtbProducerWaitError("Application create_task task set unavailable")
    return tasks


def _queue_unfinished(queue: asyncio.Queue[Any]) -> int:
    return int(getattr(queue, "_unfinished_tasks", 0))


def _require_supported_app(app: Any) -> AntaresUpdateIntakeQueue:
    reasons = list(unsupported_application_reasons(app))
    queue = getattr(app, "update_queue", None)
    if not isinstance(queue, AntaresUpdateIntakeQueue):
        reasons.append("update_queue.not_antares_intake")
    if reasons:
        raise PtbProducerWaitError(
            "unsupported Application for Q-PTB1 producer wait: " + ", ".join(reasons)
        )
    assert isinstance(queue, AntaresUpdateIntakeQueue)
    if not isinstance(app.update_processor, SimpleUpdateProcessor):
        raise PtbProducerWaitError("SimpleUpdateProcessor required")
    return queue


class PtbProducerWaitHost:
    """Lifecycle-owned host: one owner observation procedure per Application."""

    def __init__(
        self,
        *,
        application: Any,
        loop: asyncio.AbstractEventLoop,
        clock: Clock | None = None,
    ) -> None:
        self._app = application
        self._app_token = id(application)
        self._loop = loop
        self._clock = clock if clock is not None else Clock()
        self._queue = _require_supported_app(application)
        self._issuer_id = id(self)
        self._status = ProducerWaitStatus.IDLE
        self._owner_task: asyncio.Task[None] | None = None
        self._procedure_id: int | None = None
        self._procedure_deadline: float | None = None
        self._seal_generation: int | None = None
        self._complete_outcome: ProducerWaitOutcome | None = None
        self._refused_outcome: ProducerWaitOutcome | None = None
        self._observation_error: BaseException | None = None
        self._progress = asyncio.Event()
        self._process_update_inflight = 0
        self._issued: ProducersCompleteAttestation | None = None
        self._producer_entries_closed = False
        self._cleanup_phase = False
        self._orig_process_update: Any = None
        self._orig_create_task: Any = None
        self._assert_clean_install_preconditions()
        self._install_entry_trackers()

    def _assert_clean_install_preconditions(self) -> None:
        """Refuse install if producers already run untracked (no infinite wait)."""

        unfinished = _queue_unfinished(self._queue)
        live = [t for t in _create_task_tasks(self._app) if not t.done()]
        concurrent = int(self._app.update_processor.current_concurrent_updates)
        if unfinished or live or concurrent or not self._queue.empty():
            raise PtbProducerWaitError(
                "producer-wait host must be installed before producers are in flight; "
                f"unfinished={unfinished} create_tasks={len(live)} "
                f"concurrent={concurrent} queue_empty={self._queue.empty()}"
            )

    def _install_entry_trackers(self) -> None:
        """Track + gate process_update and Application.create_task."""

        self._orig_process_update = self._app.process_update
        self._orig_create_task = self._app.create_task

        async def tracked_process_update(update: object) -> None:
            if self._producer_entries_closed and not self._cleanup_phase:
                raise PtbProducerWaitError(
                    "new process_update refused after producers_complete "
                    "(entries closed until cleanup)"
                )
            self._process_update_inflight += 1
            self._pulse_progress()
            try:
                await self._orig_process_update(update)
            finally:
                self._process_update_inflight -= 1
                self._pulse_progress()

        def gated_create_task(
            coroutine: Any,
            update: object | None = None,
            name: str | None = None,
        ) -> asyncio.Task[Any]:
            if self._producer_entries_closed and not self._cleanup_phase:
                if asyncio.iscoroutine(coroutine):
                    coroutine.close()
                raise PtbProducerWaitError(
                    "new Application.create_task refused after producers_complete "
                    "(entries closed until cleanup)"
                )
            task = self._orig_create_task(coroutine, update=update, name=name)
            self._pulse_progress()
            return task

        self._app.process_update = tracked_process_update  # type: ignore[method-assign]
        self._app.create_task = gated_create_task  # type: ignore[method-assign]

    def enter_cleanup_phase(self) -> None:
        """Allow PTB stop/cleanup paths after proof; keeps intake seal."""

        self.require_owner_loop()
        self._cleanup_phase = True
        self._pulse_progress()

    def _pulse_progress(self) -> None:
        self._progress.set()
        self._progress = asyncio.Event()

    @property
    def application(self) -> Any:
        return self._app

    @property
    def application_token(self) -> int:
        return self._app_token

    @property
    def issuer_id(self) -> int:
        return self._issuer_id

    @property
    def intake(self) -> AntaresUpdateIntakeQueue:
        return self._queue

    @property
    def owner_task(self) -> asyncio.Task[None] | None:
        return self._owner_task

    @property
    def procedure_id(self) -> int | None:
        return self._procedure_id

    @property
    def procedure_deadline(self) -> float | None:
        return self._procedure_deadline

    def require_owner_loop(self) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise PtbProducerWaitError(
                "producer wait requires the owner running loop"
            ) from exc
        if running is not self._loop:
            raise PtbProducerWaitError("foreign event loop rejected")

    def _live_create_tasks(self) -> list[asyncio.Task[Any]]:
        return [t for t in _create_task_tasks(self._app) if not t.done()]

    def _remainder_now(self) -> list[str]:
        updater = getattr(self._app, "updater", None)
        updater_running = bool(updater is not None and getattr(updater, "running", False))
        rem: list[str] = []
        if not self._queue.sealed:
            rem.append("intake_not_sealed")
        if updater_running:
            rem.append("updater_running")
        if not getattr(self._app, "running", False):
            rem.append("application_not_running")
        unfinished = _queue_unfinished(self._queue)
        if unfinished or not self._queue.empty():
            rem.append("queue_or_unfinished")
        if self._live_create_tasks():
            rem.append("create_task_alive")
        if self._app.update_processor.current_concurrent_updates:
            rem.append("concurrent_updates_alive")
        if self._process_update_inflight:
            rem.append("process_update_inflight")
        if self._observation_error is not None:
            rem.append("observation_error")
        return rem

    def _idle_epoch(self) -> tuple[Any, ...]:
        live = self._live_create_tasks()
        return (
            self._queue.seal_generation if self._queue.sealed else None,
            _queue_unfinished(self._queue),
            self._queue.empty(),
            frozenset(id(t) for t in live),
            int(self._app.update_processor.current_concurrent_updates),
            int(self._process_update_inflight),
            bool(getattr(self._app, "running", False)),
            bool(
                getattr(self._app, "updater", None) is not None
                and getattr(self._app.updater, "running", False)
            ),
        )

    def _epoch_is_idle(self, epoch: tuple[Any, ...]) -> bool:
        seal_gen, unfinished, empty, live_ids, concurrent, inflight, running, updater_running = (
            epoch
        )
        return (
            seal_gen is not None
            and unfinished == 0
            and empty
            and not live_ids
            and concurrent == 0
            and inflight == 0
            and running
            and not updater_running
        )

    def snapshot(self) -> ProducerWaitSnapshot:
        if self._complete_outcome is not None:
            return self._complete_outcome.snapshot
        if self._refused_outcome is not None and self._status is ProducerWaitStatus.REFUSED:
            return self._refused_outcome.snapshot
        live = self._live_create_tasks()
        unfinished = _queue_unfinished(self._queue)
        concurrent = int(self._app.update_processor.current_concurrent_updates)
        updater = getattr(self._app, "updater", None)
        updater_running = bool(updater is not None and getattr(updater, "running", False))
        err = (
            type(self._observation_error).__name__
            if self._observation_error is not None
            else None
        )
        status = self._status
        if (
            status is ProducerWaitStatus.WAITING
            and self._procedure_deadline is not None
            and self._clock.monotonic() >= self._procedure_deadline
        ):
            status = ProducerWaitStatus.INCOMPLETE
        return ProducerWaitSnapshot(
            status=status,
            intake_sealed=self._queue.sealed,
            seal_generation=self._seal_generation,
            queue_empty=self._queue.empty() and unfinished == 0,
            unfinished_tasks=unfinished,
            create_task_alive=len(live),
            concurrent_updates=concurrent,
            process_update_inflight=self._process_update_inflight,
            application_running=bool(getattr(self._app, "running", False)),
            updater_running=updater_running,
            producers_complete=False,
            remainder=tuple(self._remainder_now()),
            owner_procedure_id=self._procedure_id,
            observation_error_type=err,
        )

    def seal_intake(self) -> int:
        self.require_owner_loop()
        if id(self._app) != self._app_token:
            raise PtbProducerWaitError("Application identity changed")
        gen = self._queue.seal()
        self._seal_generation = gen
        self._pulse_progress()
        return gen

    def validate_attestation(self, attestation: ProducersCompleteAttestation) -> bool:
        """True only for the attestation issued by this host's completed procedure."""

        if self._issued is None or attestation is not self._issued:
            # Identity compare by value fields if same object was lost.
            if self._issued is None:
                return False
            if attestation.is_test_harness:
                return False
            if (
                attestation.issuer_id != self._issuer_id
                or attestation.procedure_id != self._procedure_id
                or attestation.application_token != self._app_token
                or attestation.intake_generation != self._seal_generation
                or attestation.intake_generation != self._queue.seal_generation
                or not attestation.matches_secret(self._issued)
            ):
                return False
        if self._complete_outcome is None or self._complete_outcome.attestation is None:
            return False
        if not self._queue.sealed:
            return False
        if attestation.intake_generation != self._queue.seal_generation:
            return False
        if id(self._app) != self._app_token:
            return False
        if not getattr(self._app, "running", False):
            # Attestation remains valid for accept after producers completed while
            # app still running at mint time; accept-time running is preferred but
            # stop may happen later. Require seal+issuer match only.
            pass
        return True

    def _issue_attestation(self) -> ProducersCompleteAttestation:
        assert self._procedure_id is not None
        assert self._seal_generation is not None
        att = ProducersCompleteAttestation._issue_from_producer_wait(  # noqa: SLF001
            issuer_id=self._issuer_id,
            procedure_id=self._procedure_id,
            application_token=self._app_token,
            intake_generation=self._seal_generation,
            secret=secrets.token_bytes(16),
        )
        self._issued = att
        return att

    async def _call_soon_barrier(self) -> None:
        """Drain already-scheduled ready callbacks once (not sleep-as-proof)."""

        marker = asyncio.Event()
        self._loop.call_soon(marker.set)
        await marker.wait()

    async def _wait_progress_or_deadline_pulse(
        self, *, pulse_deadline: float | None
    ) -> None:
        """Wait for producer progress; never cancel producer Tasks."""

        progress = self._progress
        live = self._live_create_tasks()
        helpers: list[asyncio.Task[Any]] = []
        try:
            if live:
                # Completion of a producer wakes us; do not cancel these.
                prod_wait = self._loop.create_task(
                    asyncio.wait(live, return_when=asyncio.FIRST_COMPLETED),
                    name="antares-ptb-producer-progress",
                )
                helpers.append(prod_wait)
            prog_wait = self._loop.create_task(
                progress.wait(), name="antares-ptb-progress-event"
            )
            helpers.append(prog_wait)
            if pulse_deadline is not None:
                pulse = self._loop.create_task(
                    self._clock.sleep_until(pulse_deadline),
                    name="antares-ptb-deadline-pulse",
                )
                helpers.append(pulse)
            if not helpers:
                await self._call_soon_barrier()
                return
            done, pending = await asyncio.wait(
                helpers, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None and task.get_name() == "antares-ptb-producer-progress":
                    # wait() itself should not raise; ignore
                    pass
        finally:
            for task in helpers:
                if not task.done():
                    task.cancel()
            if helpers:
                await asyncio.gather(*helpers, return_exceptions=True)

    async def _owner_observe(self) -> None:
        try:
            if id(self._app) != self._app_token:
                raise PtbProducerWaitError("Application identity changed")
            _require_supported_app(self._app)
            if not self._queue.sealed:
                self.seal_intake()
            else:
                self._seal_generation = self._queue.seal_generation

            updater = getattr(self._app, "updater", None)
            if updater is not None and getattr(updater, "running", False):
                self._status = ProducerWaitStatus.REFUSED
                self._refused_outcome = ProducerWaitOutcome(
                    snapshot=self._build_terminalish_snapshot(ProducerWaitStatus.REFUSED),
                    attestation=None,
                )
                return
            if not getattr(self._app, "running", False):
                self._status = ProducerWaitStatus.REFUSED
                self._refused_outcome = ProducerWaitOutcome(
                    snapshot=self._build_terminalish_snapshot(ProducerWaitStatus.REFUSED),
                    attestation=None,
                )
                return

            self._status = ProducerWaitStatus.WAITING
            while True:
                epoch = self._idle_epoch()
                if self._epoch_is_idle(epoch):
                    await self._call_soon_barrier()
                    epoch2 = self._idle_epoch()
                    if epoch2 == epoch and self._epoch_is_idle(epoch2):
                        att = self._issue_attestation()
                        self._status = ProducerWaitStatus.COMPLETE
                        # Close new producer entries until cleanup (not only accept-time).
                        self._producer_entries_closed = True
                        snap = ProducerWaitSnapshot(
                            status=ProducerWaitStatus.COMPLETE,
                            intake_sealed=True,
                            seal_generation=self._seal_generation,
                            queue_empty=True,
                            unfinished_tasks=0,
                            create_task_alive=0,
                            concurrent_updates=0,
                            process_update_inflight=0,
                            application_running=True,
                            updater_running=False,
                            producers_complete=True,
                            remainder=(),
                            owner_procedure_id=self._procedure_id,
                        )
                        self._complete_outcome = ProducerWaitOutcome(
                            snapshot=snap, attestation=att
                        )
                        self._pulse_progress()
                        return
                await self._wait_progress_or_deadline_pulse(pulse_deadline=None)
        except BaseException as exc:
            self._observation_error = exc
            self._status = ProducerWaitStatus.FAILED
            self._pulse_progress()
            raise

    def _build_terminalish_snapshot(self, status: ProducerWaitStatus) -> ProducerWaitSnapshot:
        live = self._live_create_tasks()
        unfinished = _queue_unfinished(self._queue)
        updater = getattr(self._app, "updater", None)
        updater_running = bool(updater is not None and getattr(updater, "running", False))
        return ProducerWaitSnapshot(
            status=status,
            intake_sealed=self._queue.sealed,
            seal_generation=self._seal_generation,
            queue_empty=self._queue.empty() and unfinished == 0,
            unfinished_tasks=unfinished,
            create_task_alive=len(live),
            concurrent_updates=int(self._app.update_processor.current_concurrent_updates),
            process_update_inflight=self._process_update_inflight,
            application_running=bool(getattr(self._app, "running", False)),
            updater_running=updater_running,
            producers_complete=False,
            remainder=tuple(self._remainder_now()),
            owner_procedure_id=self._procedure_id,
            observation_error_type=(
                type(self._observation_error).__name__
                if self._observation_error is not None
                else None
            ),
        )

    def _ensure_owner_procedure(self, *, procedure_deadline: float | None) -> None:
        if self._owner_task is not None:
            # Same owner: do not refresh deadline / create a new procedure.
            return
        self._procedure_id = int(secrets.randbits(32)) or 1
        self._procedure_deadline = procedure_deadline
        self._owner_task = self._loop.create_task(
            self._owner_observe(), name="antares-ptb-producer-wait-owner"
        )
        self._owner_task.add_done_callback(self._on_owner_done)

    def _on_owner_done(self, task: asyncio.Task[None]) -> None:
        self._pulse_progress()
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None and self._observation_error is None:
            self._observation_error = exc
            if self._status is not ProducerWaitStatus.COMPLETE:
                self._status = ProducerWaitStatus.FAILED

    async def wait_producers_complete(
        self, *, deadline: float | None = None
    ) -> ProducerWaitOutcome:
        """Wait for attestation or publish incomplete for this waiter.

        ``deadline`` is this **caller's** wait budget. The first non-None deadline
        is recorded as ``procedure_deadline`` (diagnostics / drain alignment) and
        is never refreshed by repeats. Expiry returns incomplete without cancelling
        producers or ending owner observation. A later call with ``deadline=None``
        waits for the same owner procedure to complete.
        """

        self.require_owner_loop()
        if self._complete_outcome is not None:
            return self._complete_outcome
        if self._refused_outcome is not None and self._status is ProducerWaitStatus.REFUSED:
            return self._refused_outcome

        self._ensure_owner_procedure(procedure_deadline=deadline)
        caller_deadline = deadline

        while True:
            if self._complete_outcome is not None:
                return self._complete_outcome
            if self._refused_outcome is not None and self._status is ProducerWaitStatus.REFUSED:
                return self._refused_outcome
            if self._status is ProducerWaitStatus.FAILED:
                err = self._observation_error or PtbProducerWaitError(
                    "owner observation failed"
                )
                raise err

            if (
                caller_deadline is not None
                and self._clock.monotonic() >= caller_deadline
            ):
                return ProducerWaitOutcome(
                    snapshot=self._build_terminalish_snapshot(
                        ProducerWaitStatus.INCOMPLETE
                    ),
                    attestation=None,
                )

            progress = self._progress
            helpers: list[asyncio.Task[Any]] = [
                self._loop.create_task(
                    progress.wait(), name="antares-ptb-waiter-progress"
                )
            ]
            if caller_deadline is not None:
                helpers.append(
                    self._loop.create_task(
                        self._clock.sleep_until(caller_deadline),
                        name="antares-ptb-waiter-deadline",
                    )
                )
            try:
                done, pending = await asyncio.wait(
                    helpers, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                for task in helpers:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*helpers, return_exceptions=True)
                raise
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def wait_and_accept(
        self,
        session: ShutdownSession,
        *,
        deadline: float | None = None,
    ) -> ProducerWaitOutcome:
        self.require_owner_loop()
        outcome = await self.wait_producers_complete(deadline=deadline)
        if outcome.attestation is not None:
            session.accept_producers_complete(outcome.attestation)
        return outcome


def attach_producer_wait_to_shutdown_host(
    shutdown_host: Any,
    producer_host: PtbProducerWaitHost,
) -> None:
    """Bind producer-wait issuer onto ShutdownSessionHost (owner loop required)."""

    shutdown_host.require_owner_loop()
    binder: Callable[[PtbProducerWaitHost], None] | None = getattr(
        shutdown_host, "bind_producer_wait", None
    )
    if binder is None:
        raise PtbProducerWaitError("ShutdownSessionHost.bind_producer_wait missing")
    binder(producer_host)
