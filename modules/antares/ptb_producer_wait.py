"""Q-PTB1 PTB producer-wait primitive (TASK-49.A).

Surveyed stack: **python-telegram-bot 22.8** (see installed
``telegram.ext._application``).

Producers in the supported isolated Antares lifecycle graph
(``run_ptb_lifecycle`` / ``SimpleUpdateProcessor``):

- Updates entering ``Application.update_queue`` (fetcher ``__update_fetcher``).
- In-flight ``__process_update_wrapper`` work (queue ``task_done`` / unfinished).
- ``Application.create_task`` work tracked in ``__create_task_tasks`` (concurrent
  update wrappers + ``block=False`` handlers + error-handler tasks).
- Processor concurrency snapshot
  ``SimpleUpdateProcessor.current_concurrent_updates``.

How new producers stop:

- Caller seals :class:`~modules.antares.ptb_update_intake.AntaresUpdateIntakeQueue`
  (refuses further ``put`` / ``put_nowait``).
- Updater must **not** be running (polling/webhook feed refused for this primitive).
- This module does **not** call ``Application.stop`` / ``shutdown`` and does not
  close Bot HTTP clients.

Completion proof (only after seal):

- intake sealed; updater not running; Application running (fetcher alive);
- ``update_queue`` empty and ``_unfinished_tasks == 0``;
- ``__create_task_tasks`` empty;
- ``current_concurrent_updates == 0``;
- double-checked after a scheduling yield (seal prevents late ``put`` races).

Not proof alone: stop Event, admission SEALED, empty queue without seal,
absent polling, caller bool, or drain deadline expiry.

Unsupported configurations raise :class:`PtbProducerWaitError` (explicit refuse):
non-:class:`AntaresUpdateIntakeQueue`, lifecycle
``unsupported_application_reasons``, updater running, Application not running,
foreign Application attestation targets.

Production attestations are minted here only — never via
:meth:`ProducersCompleteAttestation.for_tests`.
"""

from __future__ import annotations

import asyncio
import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from telegram.ext._baseupdateprocessor import SimpleUpdateProcessor

from modules.antares.application_lifecycle import unsupported_application_reasons
from modules.antares.ptb_update_intake import AntaresUpdateIntakeQueue
from modules.antares.shutdown_session import (
    Clock,
    ControllableClock,
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


@dataclass(frozen=True)
class ProducerWaitSnapshot:
    status: ProducerWaitStatus
    intake_sealed: bool
    seal_generation: int | None
    queue_empty: bool
    unfinished_tasks: int
    create_task_alive: int
    concurrent_updates: int
    application_running: bool
    updater_running: bool
    producers_complete: bool
    remainder: tuple[str, ...]


@dataclass(frozen=True)
class ProducerWaitOutcome:
    """Result of one wait attempt (success carries a minted attestation)."""

    snapshot: ProducerWaitSnapshot
    attestation: ProducersCompleteAttestation | None


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
    processor = app.update_processor
    if not isinstance(processor, SimpleUpdateProcessor):
        raise PtbProducerWaitError("SimpleUpdateProcessor required")
    return queue


class PtbProducerWaitHost:
    """Lifecycle-owned host: at most one producer-wait procedure per Application."""

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
        self._status = ProducerWaitStatus.IDLE
        self._owner_task: asyncio.Task[ProducerWaitOutcome] | None = None
        self._outcome: ProducerWaitOutcome | None = None
        self._seal_generation: int | None = None
        self._waiters_changed = asyncio.Event()

    @property
    def application(self) -> Any:
        return self._app

    @property
    def application_token(self) -> int:
        return self._app_token

    @property
    def intake(self) -> AntaresUpdateIntakeQueue:
        return self._queue

    @property
    def owner_task(self) -> asyncio.Task[ProducerWaitOutcome] | None:
        return self._owner_task

    @property
    def outcome(self) -> ProducerWaitOutcome | None:
        return self._outcome

    def require_owner_loop(self) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise PtbProducerWaitError(
                "producer wait requires the owner running loop"
            ) from exc
        if running is not self._loop:
            raise PtbProducerWaitError("foreign event loop rejected")

    def snapshot(self) -> ProducerWaitSnapshot:
        updater = getattr(self._app, "updater", None)
        updater_running = bool(updater is not None and getattr(updater, "running", False))
        try:
            tasks = _create_task_tasks(self._app)
            create_alive = sum(1 for t in tasks if not t.done())
        except PtbProducerWaitError:
            create_alive = -1
        unfinished = _queue_unfinished(self._queue)
        concurrent = int(self._app.update_processor.current_concurrent_updates)
        queue_empty = self._queue.empty() and unfinished == 0
        remainder: list[str] = []
        if not self._queue.sealed:
            remainder.append("intake_not_sealed")
        if updater_running:
            remainder.append("updater_running")
        if not getattr(self._app, "running", False):
            remainder.append("application_not_running")
        if not queue_empty:
            remainder.append("queue_or_unfinished")
        if create_alive:
            remainder.append("create_task_alive")
        if concurrent:
            remainder.append("concurrent_updates_alive")
        complete = (
            self._status is ProducerWaitStatus.COMPLETE
            and self._outcome is not None
            and self._outcome.attestation is not None
        )
        return ProducerWaitSnapshot(
            status=self._status,
            intake_sealed=self._queue.sealed,
            seal_generation=self._seal_generation,
            queue_empty=queue_empty,
            unfinished_tasks=unfinished,
            create_task_alive=max(create_alive, 0),
            concurrent_updates=concurrent,
            application_running=bool(getattr(self._app, "running", False)),
            updater_running=updater_running,
            producers_complete=complete,
            remainder=tuple(remainder),
        )

    def seal_intake(self) -> int:
        """Seal update intake on the owner loop. Does not stop Application HTTP."""

        self.require_owner_loop()
        if id(self._app) != self._app_token:
            raise PtbProducerWaitError("Application identity changed")
        gen = self._queue.seal()
        self._seal_generation = gen
        return gen

    def _producers_idle_locked(self) -> bool:
        if not self._queue.sealed:
            return False
        updater = getattr(self._app, "updater", None)
        if updater is not None and getattr(updater, "running", False):
            return False
        if not getattr(self._app, "running", False):
            return False
        if _queue_unfinished(self._queue) != 0 or not self._queue.empty():
            return False
        tasks = _create_task_tasks(self._app)
        if any(not t.done() for t in tasks):
            return False
        if self._app.update_processor.current_concurrent_updates != 0:
            return False
        return True

    async def _wait_until_idle_or_deadline(
        self, deadline: float | None
    ) -> ProducerWaitStatus:
        while True:
            if self._producers_idle_locked():
                # Yield once then re-check: seal blocks new puts; catch in-loop races.
                await asyncio.sleep(0)
                if self._producers_idle_locked():
                    return ProducerWaitStatus.COMPLETE
            if deadline is not None and self._clock.monotonic() >= deadline:
                return ProducerWaitStatus.INCOMPLETE
            tasks = [t for t in _create_task_tasks(self._app) if not t.done()]
            if deadline is not None:
                remaining = deadline - self._clock.monotonic()
                if remaining <= 0:
                    return ProducerWaitStatus.INCOMPLETE
                if tasks:
                    wait_tasks: set[asyncio.Future[Any]] = set(tasks)
                    pulse = self._loop.create_task(self._clock.sleep_until(deadline))
                    wait_tasks.add(pulse)
                    done, pending = await asyncio.wait(
                        wait_tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for item in pending:
                        item.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for item in done:
                        if item is pulse and not item.cancelled():
                            # Deadline wake — loop will observe incomplete/complete.
                            pass
                else:
                    await self._clock.sleep_until(
                        min(deadline, self._clock.monotonic() + 0.05)
                        if not isinstance(self._clock, ControllableClock)
                        else deadline
                    )
            elif tasks:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            else:
                # Sequential handler may be inside fetcher with unfinished_tasks>0.
                await asyncio.sleep(0)

    async def _owner_wait(self, deadline: float | None) -> ProducerWaitOutcome:
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
                snap = self.snapshot()
                outcome = ProducerWaitOutcome(snapshot=snap, attestation=None)
                self._outcome = outcome
                return outcome
            if not getattr(self._app, "running", False):
                self._status = ProducerWaitStatus.REFUSED
                snap = self.snapshot()
                outcome = ProducerWaitOutcome(snapshot=snap, attestation=None)
                self._outcome = outcome
                return outcome

            self._status = ProducerWaitStatus.WAITING
            status = await self._wait_until_idle_or_deadline(deadline)
            if status is ProducerWaitStatus.COMPLETE:
                assert self._seal_generation is not None
                attestation = ProducersCompleteAttestation.mint_for_application(
                    application_token=self._app_token,
                    intake_generation=self._seal_generation,
                )
                self._status = ProducerWaitStatus.COMPLETE
                outcome = ProducerWaitOutcome(
                    snapshot=self.snapshot(), attestation=attestation
                )
                self._outcome = outcome
                return outcome

            self._status = ProducerWaitStatus.INCOMPLETE
            outcome = ProducerWaitOutcome(snapshot=self.snapshot(), attestation=None)
            self._outcome = outcome
            return outcome
        finally:
            self._waiters_changed.set()
            self._waiters_changed = asyncio.Event()

    async def wait_producers_complete(
        self, *, deadline: float | None = None
    ) -> ProducerWaitOutcome:
        """Wait for truthful producers_complete or incomplete/refuse.

        Cancelling this await detaches **this waiter only**; the owner observation
        Task and in-flight PTB producers continue. Repeat calls join the same
        owner procedure (no second seal/stop). Deadline expiry yields incomplete
        without minting an attestation (HTTP remains caller's responsibility).
        """

        self.require_owner_loop()
        if self._outcome is not None and self._outcome.attestation is not None:
            return self._outcome
        if self._owner_task is not None and not self._owner_task.done():
            return await self._await_owner_result()
        if self._outcome is not None and self._status is ProducerWaitStatus.INCOMPLETE:
            # Late completion in the same host: restart observation without new seal.
            self._outcome = None
            self._status = ProducerWaitStatus.IDLE
        self._owner_task = self._loop.create_task(
            self._owner_wait(deadline), name="antares-ptb-producer-wait"
        )
        return await self._await_owner_result()

    async def _await_owner_result(self) -> ProducerWaitOutcome:
        assert self._owner_task is not None
        owner = self._owner_task
        try:
            return await asyncio.shield(owner)
        except asyncio.CancelledError:
            # Detach waiter only; owner keeps running.
            raise

    async def wait_and_accept(
        self,
        session: ShutdownSession,
        *,
        deadline: float | None = None,
    ) -> ProducerWaitOutcome:
        """Wait then deliver minted attestation into a bound shutdown session."""

        self.require_owner_loop()
        outcome = await self.wait_producers_complete(deadline=deadline)
        if outcome.attestation is not None:
            session.accept_producers_complete(outcome.attestation)
        return outcome


def attach_producer_wait_to_shutdown_host(
    shutdown_host: Any,
    producer_host: PtbProducerWaitHost,
) -> None:
    """Bind Application token expectations onto a ShutdownSessionHost.

    Connection point: shutdown_session must reject foreign / ``for_tests``
    attestations once an Application is bound for post-OPEN cleanup.
    """

    binder: Callable[[Any], None] | None = getattr(
        shutdown_host, "bind_application", None
    )
    if binder is None:
        raise PtbProducerWaitError("ShutdownSessionHost.bind_application missing")
    binder(producer_host.application)
