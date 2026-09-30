"""TASK-49.B: post-OPEN drain orchestration P4–P7 (Q-HLP1 sibling module).

Layout (Q-HLP1): **separate module** under ``modules.antares``, not inlined into
``run_ptb_lifecycle``. ``ShutdownSession`` / ``ShutdownSessionHost`` remain the
single owner-session primitive (49.S); this module binds **one** P4–P7 owner
Task to that session. Public callers only join/observe it. Cancelling a waiter
detaches that waiter only — phase transitions continue. Concurrent / repeat
callers never start a second orchestration for the same session.

Supported path requires TASK-49.A conditions: producer host installed once
before ``Application.initialize()``, Antares intake queue, Application/issuer
binding, no untracked producers, permanent entry refuse after COMPLETE.

Partial boundary: successful P7 does **not** mean full Antares shutdown,
production readiness, or SESSION_TERMINAL. P8 sender / P9 executor remain
future slices. Direct PTB cleanup must not bypass these phases.

Integration boundary (49.B): the real ``apps/antares`` / default sandbox entry
still uses a plain ``asyncio.Queue`` and ``run_ptb_lifecycle`` still seals →
cleanup without calling this module. Safe partial wire of P4–P7 without
bypassing P8/P9 via direct cleanup is a **design blocker** in this scope —
lifecycle wiring is **not** marked done and the unsafe path is **not** enabled.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
from dataclasses import dataclass, field
from typing import Any, Callable

from automation.worker import (
    IsolatedProfileWorkerStopError,
    snapshot_isolated_profile_workers,
    stop_isolated_profile_workers,
)
from integrations.wallet_editor_registry_async import (
    IsolatedRegistryDaemonStopError,
    wait_isolated_registry_daemon_ops,
)
from modules.antares.ptb_producer_wait import PtbProducerWaitHost
from modules.antares.shutdown_session import ShutdownSession
from modules.antares.work_admission import WorkAdmission


class DrainOrchestrationError(RuntimeError):
    """Drain phase refused or failed (remainder may be attached)."""

    def __init__(self, message: str, *, remainder: Any | None = None) -> None:
        super().__init__(message)
        self.remainder = remainder


class DrainPhase(enum.Enum):
    P4_PRODUCERS = "p4_producers"
    P5_ACCEPTED_ITEMS = "p5_accepted_items"
    P6_WE_STOP = "p6_we_stop"
    P7_REGISTRY = "p7_registry"


@dataclass(frozen=True)
class DrainPhasesResult:
    """Partial drain outcome — not full shutdown / not SESSION_TERMINAL."""

    producers_attested: bool
    p5_complete: bool
    we_joined: tuple[str, ...]
    registry_joined: tuple[str, ...]
    last_completed_phase: DrainPhase | None
    remainder: tuple[str, ...]


@dataclass
class _DrainOwnerState:
    """Per-ShutdownSession owner procedure state (progress / partial / error)."""

    session: ShutdownSession
    producer_wait: PtbProducerWaitHost
    owner_task: asyncio.Task[Any] | None = None
    waiters: list[asyncio.Future] = field(default_factory=list)
    last_completed_phase: DrainPhase | None = None
    we_joined: tuple[str, ...] = ()
    registry_joined: tuple[str, ...] = ()
    remainder: list[str] = field(default_factory=list)
    result: DrainPhasesResult | None = None
    error: BaseException | None = None
    p6_started: bool = False
    p7_started: bool = False
    progress: asyncio.Event = field(default_factory=asyncio.Event)

    def pulse(self) -> None:
        self.progress.set()

    def build_partial_result(self) -> DrainPhasesResult:
        p5_done = self.last_completed_phase in (
            DrainPhase.P5_ACCEPTED_ITEMS,
            DrainPhase.P6_WE_STOP,
            DrainPhase.P7_REGISTRY,
        )
        return DrainPhasesResult(
            producers_attested=bool(
                self.session.snapshot().producers_complete_attested
            ),
            p5_complete=p5_done,
            we_joined=self.we_joined,
            registry_joined=self.registry_joined,
            last_completed_phase=self.last_completed_phase,
            remainder=tuple(self.remainder),
        )


_ATTR = "_antares_drain_owner_49b"


def _we_unfinished_profiles() -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for key, worker in snapshot_isolated_profile_workers().items():
        n = int(worker.queue.unfinished_tasks)
        if n:
            out.append((key, n))
    return out


def _continuation_states(admission: WorkAdmission) -> tuple[str, ...]:
    with admission._lock:  # noqa: SLF001
        return tuple(record.state for record in admission._ae_continuations.values())


def _assert_drain_identity(
    session: ShutdownSession, producer_wait: PtbProducerWaitHost
) -> None:
    """Issuer / Application / admission / owner-loop identity (even if proof accepted)."""

    session._require_owner_loop()  # noqa: SLF001
    host = session._host  # noqa: SLF001
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        raise DrainOrchestrationError(
            "drain orchestration requires the owner running loop",
            remainder=("foreign_loop",),
        ) from exc
    if loop is not host.loop:
        raise DrainOrchestrationError(
            "drain orchestration bound to another event loop",
            remainder=("foreign_loop",),
        )
    if host.admission is not session._host.admission:  # noqa: SLF001
        raise DrainOrchestrationError(
            "drain orchestration admission mismatch",
            remainder=("admission_mismatch",),
        )
    bound = host.producer_wait
    if bound is None:
        raise DrainOrchestrationError(
            "producer-wait issuer not bound on ShutdownSessionHost",
            remainder=("issuer_unbound",),
        )
    if bound is not producer_wait:
        raise DrainOrchestrationError(
            "producer-wait issuer does not match session binding",
            remainder=("issuer_mismatch",),
        )
    app = getattr(producer_wait, "application", None)
    if app is None:
        raise DrainOrchestrationError(
            "producer-wait host missing application",
            remainder=("application_missing",),
        )
    token = id(app)
    if host.application_token is not None and host.application_token != token:
        raise DrainOrchestrationError(
            "Application identity does not match session binding",
            remainder=("application_mismatch",),
        )


def _get_owner_state(session: ShutdownSession) -> _DrainOwnerState | None:
    state = getattr(session, _ATTR, None)
    return state if isinstance(state, _DrainOwnerState) else None


def _publish_drain_waiters(state: _DrainOwnerState) -> None:
    waiters = list(state.waiters)
    state.waiters.clear()
    for fut in waiters:
        if fut.done():
            continue
        if state.error is not None:
            fut.set_exception(state.error)
        elif state.result is not None:
            fut.set_result(state.result)
        else:
            fut.set_exception(
                DrainOrchestrationError(
                    "drain owner finished without result",
                    remainder=tuple(state.remainder) or ("owner_empty",),
                )
            )
    state.pulse()


def _retrieve_drain_owner_exception(state: _DrainOwnerState) -> None:
    task = state.owner_task
    if task is None or not task.done() or task.cancelled():
        return
    with contextlib.suppress(BaseException):
        exc = task.exception()
        if exc is not None and state.error is None:
            state.error = exc


def _deadline_partial_error(state: _DrainOwnerState) -> DrainOrchestrationError:
    rem = list(state.remainder)
    if "drain_deadline_passed" not in rem:
        rem.insert(0, "drain_deadline_passed")
    if state.last_completed_phase is not None:
        tag = f"last_{state.last_completed_phase.value}"
        if tag not in rem:
            rem.append(tag)
    snap = state.session.snapshot()
    for item in snap.remainder:
        if item not in rem:
            rem.append(item)
    state.remainder = rem
    return DrainOrchestrationError(
        "shutdown deadline published partial drain snapshot; "
        "Accepted/continuation and started owner ops were not cancelled",
        remainder=tuple(rem),
    )


async def wait_p5_accepted_continuation_and_we_items(
    admission: WorkAdmission,
    *,
    progress: asyncio.Event | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """P5: Accepted Futures done, AE continuations empty, WE unfinished_tasks==0.

    Does **not** cancel Futures/continuations. Empty queue alone is not drain —
    ``unfinished_tasks`` must reach 0. Not a full graceful stop.
    """

    await admission.wait_accepted_executor_work()
    while True:
        if should_stop is not None and should_stop():
            return
        cont = _continuation_states(admission)
        unfinished = _we_unfinished_profiles()
        if not cont and not unfinished:
            # call_soon barrier: confirm idle after one scheduling turn
            ready = asyncio.Event()
            asyncio.get_running_loop().call_soon(ready.set)
            await ready.wait()
            if not _continuation_states(admission) and not _we_unfinished_profiles():
                return
        if progress is not None:
            progress.clear()
            try:
                await asyncio.wait_for(progress.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(0)


async def _owner_drain_p4_to_p7(state: _DrainOwnerState) -> DrainPhasesResult:
    """Single owner sequence; waiter cancel must not interrupt this Task."""

    session = state.session
    producer_wait = state.producer_wait
    admission = session._host.admission  # noqa: SLF001

    try:
        # --- P4: truthful producers_complete (session.shutdown_deadline only) ---
        if not session.snapshot().producers_complete_attested:
            if not session._may_start_new_destructive_phases():  # noqa: SLF001
                state.remainder = ["drain_deadline_passed"]
                raise _deadline_partial_error(state)
            outcome = await producer_wait.wait_and_accept(
                session, deadline=session.shutdown_deadline
            )
            if outcome.attestation is None:
                state.remainder = ["producers_incomplete"]
                if session._drain_deadline_passed():  # noqa: SLF001
                    raise _deadline_partial_error(state)
                raise DrainOrchestrationError(
                    "PTB producers incomplete; P5–P7 refused",
                    remainder=("producers_incomplete",),
                )
        if not session.snapshot().producers_complete_attested:
            state.remainder = ["producers_not_attested"]
            raise DrainOrchestrationError(
                "producers attestation missing after P4",
                remainder=("producers_not_attested",),
            )
        state.last_completed_phase = DrainPhase.P4_PRODUCERS
        state.pulse()

        if not session._may_start_new_destructive_phases():  # noqa: SLF001
            state.remainder = ["drain_deadline_passed", "producers_complete"]
            raise _deadline_partial_error(state)

        # --- P5 (observe until idle; deadline forbids starting P6, not cancel) ---
        await wait_p5_accepted_continuation_and_we_items(
            admission,
            progress=state.progress,
            should_stop=lambda: (
                session._drain_deadline_passed()  # noqa: SLF001
                and not session._may_start_new_destructive_phases()  # noqa: SLF001
            ),
        )
        if session._drain_deadline_passed() and (  # noqa: SLF001
            _continuation_states(admission) or _we_unfinished_profiles()
        ):
            state.remainder = ["drain_deadline_passed", "p5_incomplete"]
            raise _deadline_partial_error(state)
        if _continuation_states(admission) or _we_unfinished_profiles():
            # Stopped early without idle — treat as deadline/partial refuse.
            state.remainder = ["drain_deadline_passed", "p5_incomplete"]
            raise _deadline_partial_error(state)
        state.last_completed_phase = DrainPhase.P5_ACCEPTED_ITEMS
        state.pulse()

        if not session._may_start_new_destructive_phases():  # noqa: SLF001
            state.remainder = ["drain_deadline_passed", "p5_complete"]
            raise _deadline_partial_error(state)

        # --- P6: WE owner-session stop (no separate drain budget) ---
        state.p6_started = True
        state.pulse()
        try:
            state.we_joined = await stop_isolated_profile_workers(
                admission,
                producers_complete=True,
                timeout=None,
            )
        except IsolatedProfileWorkerStopError as exc:
            rem = getattr(exc, "remainder", None)
            reason = getattr(rem, "reason", None) if rem is not None else None
            state.remainder = [str(reason or rem or "we_stop_failed")]
            raise DrainOrchestrationError(
                f"P6 WE stop failed: {exc}",
                remainder=rem,
            ) from exc
        state.last_completed_phase = DrainPhase.P6_WE_STOP
        state.pulse()

        if not session._may_start_new_destructive_phases():  # noqa: SLF001
            # P6 started+finished; do not start new destructive P7 after expiry.
            state.remainder = ["drain_deadline_passed", "we_stop_complete"]
            raise _deadline_partial_error(state)

        # --- P7: registry only after successful P6 ---
        state.p7_started = True
        state.pulse()
        try:
            state.registry_joined = await wait_isolated_registry_daemon_ops(
                admission,
                producers_complete=True,
                timeout=None,
            )
        except IsolatedRegistryDaemonStopError as exc:
            rem = getattr(exc, "remainder", None)
            state.remainder = ["registry_wait_failed"]
            raise DrainOrchestrationError(
                f"P7 registry wait failed: {exc}",
                remainder=rem,
            ) from exc
        state.last_completed_phase = DrainPhase.P7_REGISTRY
        state.remainder = []
        result = DrainPhasesResult(
            producers_attested=True,
            p5_complete=True,
            we_joined=state.we_joined,
            registry_joined=state.registry_joined,
            last_completed_phase=DrainPhase.P7_REGISTRY,
            remainder=(),
        )
        state.result = result
        return result
    except BaseException as exc:
        if state.error is None:
            state.error = exc
        if not isinstance(exc, asyncio.CancelledError):
            # Preserve partial snapshot on failure/deadline.
            if state.result is None and state.last_completed_phase is not None:
                state.pulse()
        raise
    finally:
        _publish_drain_waiters(state)


async def run_owner_drain_p4_to_p7(
    session: ShutdownSession,
    producer_wait: PtbProducerWaitHost,
) -> DrainPhasesResult:
    """Join/observe the single P4→P7 owner Task bound to ``session``.

    Uses only ``session.shutdown_deadline`` (no separate per-phase drain budget).
    Waiter cancellation does not interrupt owner phases. Repeat/concurrent
    callers share the same procedure. P7 success does not publish SESSION_TERMINAL.
    """

    _assert_drain_identity(session, producer_wait)
    loop = asyncio.get_running_loop()

    state = _get_owner_state(session)
    if state is None:
        state = _DrainOwnerState(session=session, producer_wait=producer_wait)
        setattr(session, _ATTR, state)
    else:
        if state.producer_wait is not producer_wait:
            raise DrainOrchestrationError(
                "drain owner already bound to another producer-wait issuer",
                remainder=("issuer_mismatch",),
            )
        if state.session is not session:
            raise DrainOrchestrationError(
                "drain owner bound to another ShutdownSession",
                remainder=("session_mismatch",),
            )

    _retrieve_drain_owner_exception(state)
    if state.result is not None:
        return state.result
    if state.error is not None and (
        state.owner_task is None or state.owner_task.done()
    ):
        raise state.error

    if state.owner_task is None or state.owner_task.done():
        if state.owner_task is not None and state.owner_task.done():
            _retrieve_drain_owner_exception(state)
            if state.result is not None:
                return state.result
            if state.error is not None:
                raise state.error
        # Create the sole owner for this session.
        state.owner_task = loop.create_task(
            _owner_drain_p4_to_p7(state),
            name="antares-drain-p4-p7-owner",
        )

        def _on_owner_done(task: asyncio.Task) -> None:
            with contextlib.suppress(BaseException):
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None and state.error is None:
                    state.error = exc
            state.pulse()

        state.owner_task.add_done_callback(_on_owner_done)

    waiter: asyncio.Future = loop.create_future()
    state.waiters.append(waiter)

    # If owner already finished between create and append, publish immediately.
    if state.owner_task.done() and not waiter.done():
        _retrieve_drain_owner_exception(state)
        if state.error is not None:
            waiter.set_exception(state.error)
        elif state.result is not None:
            waiter.set_result(state.result)

    deadline = session.shutdown_deadline

    async def _wait_progress_or_deadline() -> None:
        helpers: list[asyncio.Task[Any]] = [
            loop.create_task(state.progress.wait(), name="antares-drain-progress")
        ]
        if deadline is not None and not session._drain_deadline_passed():  # noqa: SLF001
            helpers.append(
                loop.create_task(
                    session._host.clock.sleep_until(deadline),  # noqa: SLF001
                    name="antares-drain-deadline",
                )
            )
        else:
            helpers.append(
                loop.create_task(asyncio.sleep(0.05), name="antares-drain-poll")
            )
        try:
            await asyncio.wait(helpers, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in helpers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*helpers, return_exceptions=True)
        state.progress.clear()

    try:
        while not waiter.done():
            if state.owner_task.done():
                _retrieve_drain_owner_exception(state)
                if not waiter.done():
                    if state.error is not None:
                        waiter.set_exception(state.error)
                    elif state.result is not None:
                        waiter.set_result(state.result)
                break

            # Deadline publishes partial without cancelling owner / Accepted work.
            if (
                deadline is not None
                and session._drain_deadline_passed()  # noqa: SLF001
                and not session._may_start_new_destructive_phases()  # noqa: SLF001
                and not (state.p6_started or state.p7_started)
            ):
                err = _deadline_partial_error(state)
                if not waiter.done():
                    waiter.set_exception(err)
                break

            try:
                wait_tasks = [
                    asyncio.ensure_future(asyncio.shield(waiter)),
                    asyncio.ensure_future(_wait_progress_or_deadline()),
                ]
                done, pending = await asyncio.wait(
                    wait_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    with contextlib.suppress(asyncio.CancelledError, asyncio.InvalidStateError):
                        if not task.cancelled():
                            task.exception()
            except asyncio.CancelledError:
                raise

        return await waiter
    except asyncio.CancelledError:
        try:
            state.waiters.remove(waiter)
        except ValueError:
            pass
        if not waiter.done():
            waiter.cancel()
        # Owner continues; error retained for later joiners.
        raise


def require_producer_host_pre_initialize(application: Any) -> None:
    """Documented check: host must already be installed (pre-initialize)."""

    if getattr(application, "_antares_ptb_producer_wait_host", None) is None:
        raise DrainOrchestrationError(
            "producer-wait host must be installed before Application.initialize()/start()"
        )
