"""TASK-49.B/C: post-OPEN drain orchestration P4–P8 (Q-HLP1 sibling module).

Layout (Q-HLP1): **separate module** under ``modules.antares``, not inlined into
``run_ptb_lifecycle``. ``ShutdownSession`` / ``ShutdownSessionHost`` remain the
single owner-session primitive (49.S); this module binds **one** drain owner
Task to that session. Public callers only join/observe it. Cancelling a waiter
detaches that waiter only — phase transitions continue. Concurrent / repeat
callers never start a second orchestration for the same session.

Deadline snapshot vs owner completion: expiry publishes a **waiter-side** partial
without caching a terminal owner error. The same owner keeps observing late
producer proof and already-accepted work; no new ``shutdown_deadline`` is issued.
After expiry, new destructive phases are not started. Started P6–P8 ops continue
to be observed; waiters may take a partial and later re-join the same procedure.

TASK-49.C adds **P8** after successful P7: ``stop_isolated_sender`` with an
explicit ``sender_ownership_proof`` (from ``AntaresBootPrefix.sender_ownership``
or equivalent claim). Proof identity is checked before side effects and before
returning any cached result. Remaining budget comes only from
``session.shutdown_deadline``. A live TASK-48 partial (already-requested loop
stop / ``LOOP_STOPPING``) is **not** cached as owner-terminal: the same owner
keeps observing until STOPPED, a boundary soft-stop (would need a new
destructive phase after expiry), or a final refuse. Structured
``SenderPhaseSnapshot`` is retained for EX1/P9. P8 success is not
SESSION_TERMINAL / full shutdown; P9 executor remains 49.D. Production
lifecycle wiring that would bypass P9 is **not** enabled; the 49.B integration
blocker remains.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
from dataclasses import dataclass, field
from typing import Any

from automation.worker import (
    IsolatedProfileWorkerStopError,
    snapshot_isolated_profile_workers,
    stop_isolated_profile_workers,
)
from core.antares_sender_ownership import validate_antares_sender_ownership
from integrations.wallet_editor_registry_async import (
    IsolatedRegistryDaemonStopError,
    wait_isolated_registry_daemon_ops,
)
from modules.antares.ptb_producer_wait import PtbProducerWaitHost
from modules.antares.shutdown_session import ShutdownSession
from modules.antares.work_admission import WorkAdmission


class DrainOrchestrationError(RuntimeError):
    """Drain phase refused, failed, or deadline partial snapshot for a waiter."""

    def __init__(self, message: str, *, remainder: Any | None = None) -> None:
        super().__init__(message)
        self.remainder = remainder


class DrainPhase(enum.Enum):
    P4_PRODUCERS = "p4_producers"
    P5_ACCEPTED_ITEMS = "p5_accepted_items"
    P6_WE_STOP = "p6_we_stop"
    P7_REGISTRY = "p7_registry"
    P8_SENDER = "p8_sender"


@dataclass(frozen=True)
class SenderRequestCloseSnapshot:
    """Immutable per-request close diagnostic for EX1/P9."""

    role: str
    target_index: int
    shutdown_attempted: bool
    shutdown_ok: bool
    already_closed: bool
    leftover_open: bool
    timed_out: bool
    error_type: str | None
    error_text: str | None


@dataclass(frozen=True)
class SenderPhaseSnapshot:
    """Immutable structured copy of TASK-48 ``SenderFullStopResult`` for EX1/P9."""

    ok: bool
    reason: str | None
    ownership_passed: bool
    ptb_passed: bool
    structural_passed: bool
    lifecycle_state: str
    intake_sealed: bool
    worker_stopped: bool
    worker_terminal: bool
    bot_shutdown_attempted: bool
    bot_shutdown_ok: bool
    bot_shutdown_error_type: str | None
    bot_shutdown_error_text: str | None
    request_close_results: tuple[SenderRequestCloseSnapshot, ...]
    http_stopped: bool
    loop_stop_requested: bool
    loop_running: bool
    loop_thread_alive: bool
    thread_joined: bool
    full_resource_stopped: bool
    terminal_intake_failure_total: int
    recent_intake_failures: tuple[str, ...]


@dataclass(frozen=True)
class DrainPhasesResult:
    """Partial or full drain outcome — not full shutdown / not SESSION_TERMINAL."""

    producers_attested: bool
    p5_complete: bool
    we_joined: tuple[str, ...]
    registry_joined: tuple[str, ...]
    last_completed_phase: DrainPhase | None
    remainder: tuple[str, ...]
    # P8 / EX1 diagnostics (False/None when P8 not attempted).
    sender_attempted: bool = False
    sender_ok: bool | None = None
    sender_reason: str | None = None
    sender_full_resource_stopped: bool = False
    sender_lifecycle_state: str | None = None
    sender_phase: SenderPhaseSnapshot | None = None


@dataclass(frozen=True)
class DrainOrchestrationSnapshot:
    """Public read-only progress of the session-bound drain owner procedure."""

    last_completed_phase: DrainPhase | None
    producers_attested: bool
    p5_complete: bool
    p6_started: bool
    p7_started: bool
    p8_started: bool
    we_joined: tuple[str, ...]
    registry_joined: tuple[str, ...]
    remainder: tuple[str, ...]
    owner_alive: bool
    deadline_passed: bool
    may_start_new_destructive_phases: bool
    has_terminal_result: bool
    has_terminal_error: bool
    sender_attempted: bool
    sender_ok: bool | None
    sender_reason: str | None
    sender_full_resource_stopped: bool
    sender_observing: bool
    sender_phase: SenderPhaseSnapshot | None
    include_p8: bool


@dataclass
class _DrainOwnerState:
    """Per-ShutdownSession owner procedure state (progress / partial / error)."""

    session: ShutdownSession
    producer_wait: PtbProducerWaitHost
    include_p8: bool = False
    sender_ownership_proof: object | None = None
    owner_task: asyncio.Task[Any] | None = None
    waiters: list[asyncio.Future] = field(default_factory=list)
    last_completed_phase: DrainPhase | None = None
    we_joined: tuple[str, ...] = ()
    registry_joined: tuple[str, ...] = ()
    remainder: list[str] = field(default_factory=list)
    result: DrainPhasesResult | None = None
    # Terminal hard failures only (not deadline waiter partials).
    error: BaseException | None = None
    p6_started: bool = False
    p7_started: bool = False
    p8_started: bool = False
    sender_attempted: bool = False
    sender_ok: bool | None = None
    sender_reason: str | None = None
    sender_full_resource_stopped: bool = False
    sender_lifecycle_state: str | None = None
    sender_phase: SenderPhaseSnapshot | None = None
    sender_observing: bool = False
    # Waiters that already received a deadline partial for this procedure.
    deadline_partial_delivered: set[int] = field(default_factory=set)
    # One deadline snapshot wave for waiters present when expiry is first noticed;
    # later joiners observe the same owner through to soft/hard terminal.
    deadline_partial_wave_done: bool = False
    progress: asyncio.Event = field(default_factory=asyncio.Event)

    def pulse(self) -> None:
        self.progress.set()

    def build_partial_result(self) -> DrainPhasesResult:
        p5_done = self.last_completed_phase in (
            DrainPhase.P5_ACCEPTED_ITEMS,
            DrainPhase.P6_WE_STOP,
            DrainPhase.P7_REGISTRY,
            DrainPhase.P8_SENDER,
        )
        rem = list(self.remainder)
        if self.session._drain_deadline_passed():  # noqa: SLF001
            if "drain_deadline_passed" not in rem:
                rem.insert(0, "drain_deadline_passed")
        return DrainPhasesResult(
            producers_attested=bool(
                self.session.snapshot().producers_complete_attested
            ),
            p5_complete=p5_done,
            we_joined=self.we_joined,
            registry_joined=self.registry_joined,
            last_completed_phase=self.last_completed_phase,
            remainder=tuple(rem),
            sender_attempted=self.sender_attempted,
            sender_ok=self.sender_ok,
            sender_reason=self.sender_reason,
            sender_full_resource_stopped=self.sender_full_resource_stopped,
            sender_lifecycle_state=self.sender_lifecycle_state,
            sender_phase=self.sender_phase,
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


def observe_drain_orchestration(
    session: ShutdownSession,
) -> DrainOrchestrationSnapshot | None:
    """Public read-only progress of the drain owner (None if not armed)."""

    state = _get_owner_state(session)
    if state is None:
        return None
    owner = state.owner_task
    alive = owner is not None and not owner.done()
    snap = state.build_partial_result()
    return DrainOrchestrationSnapshot(
        last_completed_phase=state.last_completed_phase,
        producers_attested=snap.producers_attested,
        p5_complete=snap.p5_complete,
        p6_started=state.p6_started,
        p7_started=state.p7_started,
        p8_started=state.p8_started,
        we_joined=state.we_joined,
        registry_joined=state.registry_joined,
        remainder=snap.remainder,
        owner_alive=alive,
        deadline_passed=bool(session._drain_deadline_passed()),  # noqa: SLF001
        may_start_new_destructive_phases=bool(
            session._may_start_new_destructive_phases()  # noqa: SLF001
        ),
        has_terminal_result=state.result is not None,
        has_terminal_error=state.error is not None
        and (owner is None or owner.done()),
        sender_attempted=state.sender_attempted,
        sender_ok=state.sender_ok,
        sender_reason=state.sender_reason,
        sender_full_resource_stopped=state.sender_full_resource_stopped,
        sender_observing=state.sender_observing,
        sender_phase=state.sender_phase,
        include_p8=state.include_p8,
    )


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
    """Waiter-side snapshot only — must not be cached as owner terminal error."""

    rem = list(state.build_partial_result().remainder)
    if "drain_deadline_passed" not in rem:
        rem.insert(0, "drain_deadline_passed")
    if state.p6_started and "p6_started" not in rem:
        rem.append("p6_started")
    if state.p7_started and "p7_started" not in rem:
        rem.append("p7_started")
    if state.p8_started and "p8_started" not in rem:
        rem.append("p8_started")
    if state.sender_attempted and "sender_attempted" not in rem:
        rem.append("sender_attempted")
    if state.last_completed_phase is not None:
        tag = f"last_{state.last_completed_phase.value}"
        if tag not in rem:
            rem.append(tag)
    snap = state.session.snapshot()
    for item in snap.remainder:
        if item not in rem:
            rem.append(item)
    state.remainder = rem
    state.pulse()
    return DrainOrchestrationError(
        "shutdown deadline published partial drain snapshot; "
        "Accepted/continuation and started owner ops were not cancelled",
        remainder=tuple(rem),
    )


def _remaining_from_session(session: ShutdownSession) -> float:
    """Remaining seconds until ``session.shutdown_deadline`` (no new budget)."""

    deadline = session.shutdown_deadline
    if deadline is None:
        return 0.0
    return max(0.0, float(deadline) - float(session._host.clock.monotonic()))  # noqa: SLF001


def _bind_sender_ownership_proof(
    state: _DrainOwnerState, proof: object | None, *, require: bool
) -> None:
    """Validate and bind exact proof identity before side effects / cached return."""

    if not require:
        return
    if proof is None:
        raise DrainOrchestrationError(
            "sender ownership proof required for P8",
            remainder=("sender_proof_missing",),
        )
    validation = validate_antares_sender_ownership(proof)
    if not validation.ok:
        raise DrainOrchestrationError(
            f"sender ownership refused: {validation.reason}",
            remainder=(f"ownership_{validation.reason or 'refused'}",),
        )
    if state.sender_ownership_proof is None:
        state.sender_ownership_proof = proof
        return
    if state.sender_ownership_proof is not proof:
        raise DrainOrchestrationError(
            "sender ownership proof does not match bound orchestration proof",
            remainder=("ownership_foreign",),
        )


def _freeze_sender_outcome(outcome: Any) -> SenderPhaseSnapshot:
    """Copy TASK-48 structured result into an immutable EX1/P9 record."""

    req_raw = tuple(getattr(outcome, "request_close_results", ()) or ())
    requests: list[SenderRequestCloseSnapshot] = []
    for item in req_raw:
        requests.append(
            SenderRequestCloseSnapshot(
                role=str(getattr(item, "role", "unknown")),
                target_index=int(getattr(item, "target_index", -1)),
                shutdown_attempted=bool(getattr(item, "shutdown_attempted", False)),
                shutdown_ok=bool(getattr(item, "shutdown_ok", False)),
                already_closed=bool(getattr(item, "already_closed", False)),
                leftover_open=bool(getattr(item, "leftover_open", False)),
                timed_out=bool(getattr(item, "timed_out", False)),
                error_type=getattr(item, "error_type", None),
                error_text=getattr(item, "error_text", None),
            )
        )
    return SenderPhaseSnapshot(
        ok=bool(getattr(outcome, "ok", False)),
        reason=getattr(outcome, "reason", None),
        ownership_passed=bool(getattr(outcome, "ownership_passed", False)),
        ptb_passed=bool(getattr(outcome, "ptb_passed", False)),
        structural_passed=bool(getattr(outcome, "structural_passed", False)),
        lifecycle_state=str(getattr(outcome, "lifecycle_state", "")),
        intake_sealed=bool(getattr(outcome, "intake_sealed", False)),
        worker_stopped=bool(getattr(outcome, "worker_stopped", False)),
        worker_terminal=bool(getattr(outcome, "worker_terminal", False)),
        bot_shutdown_attempted=bool(getattr(outcome, "bot_shutdown_attempted", False)),
        bot_shutdown_ok=bool(getattr(outcome, "bot_shutdown_ok", False)),
        bot_shutdown_error_type=getattr(outcome, "bot_shutdown_error_type", None),
        bot_shutdown_error_text=getattr(outcome, "bot_shutdown_error_text", None),
        request_close_results=tuple(requests),
        http_stopped=bool(getattr(outcome, "http_stopped", False)),
        loop_stop_requested=bool(getattr(outcome, "loop_stop_requested", False)),
        loop_running=bool(getattr(outcome, "loop_running", False)),
        loop_thread_alive=bool(getattr(outcome, "loop_thread_alive", False)),
        thread_joined=bool(getattr(outcome, "thread_joined", False)),
        full_resource_stopped=bool(getattr(outcome, "full_resource_stopped", False)),
        terminal_intake_failure_total=int(
            getattr(outcome, "terminal_intake_failure_total", 0) or 0
        ),
        recent_intake_failures=tuple(
            getattr(outcome, "recent_intake_failures", ()) or ()
        ),
    )


def _record_sender_outcome(state: _DrainOwnerState, outcome: Any) -> None:
    snap = _freeze_sender_outcome(outcome)
    state.sender_phase = snap
    state.sender_ok = snap.ok
    state.sender_reason = snap.reason
    state.sender_full_resource_stopped = snap.full_resource_stopped
    state.sender_lifecycle_state = snap.lifecycle_state
    rem = list(state.remainder)
    if snap.full_resource_stopped and snap.ok:
        state.remainder = [r for r in rem if r not in ("sender_partial", snap.reason)]
    else:
        if "sender_partial" not in rem:
            rem.append("sender_partial")
        if snap.reason and snap.reason not in rem:
            rem.append(str(snap.reason))
        state.remainder = rem


def _classify_sender_outcome(outcome: Any) -> str:
    """Classify TASK-48 outcome: success | live_observe | boundary | final_refuse."""

    ok = bool(getattr(outcome, "ok", False))
    full = bool(getattr(outcome, "full_resource_stopped", False))
    if ok and full:
        return "success"

    reason = getattr(outcome, "reason", None) or ""
    state = str(getattr(outcome, "lifecycle_state", "") or "")
    loop_stop_requested = bool(getattr(outcome, "loop_stop_requested", False))
    intake_sealed = bool(getattr(outcome, "intake_sealed", False))
    ownership_passed = bool(getattr(outcome, "ownership_passed", True))
    ptb_passed = bool(getattr(outcome, "ptb_passed", True))

    if (not ownership_passed) or reason.startswith("ownership_"):
        return "final_refuse"
    if (not ptb_passed) or reason.startswith("ptb_"):
        return "final_refuse"
    if reason.startswith("lifecycle_refuses"):
        return "final_refuse"
    if reason.startswith("observe_refused"):
        return "boundary"

    # Already-started stop: observe only with proof the destructive action began.
    if reason in (
        "deadline_loop_stopped",
        "deadline_thread_join",
        "loop_still_running",
    ):
        if loop_stop_requested:
            return "live_observe"
        return "boundary"

    if state == "LOOP_STOPPING":
        return "live_observe" if loop_stop_requested else "boundary"

    if state == "HTTP_STOPPING" or reason in (
        "deadline_http_close",
        "http_close_incomplete",
    ):
        return "live_observe"

    if state == "HTTP_STOPPED":
        return "live_observe" if loop_stop_requested else "boundary"

    if state == "DRAINING" and (
        intake_sealed
        or reason
        in (
            "deadline_s1_pending",
            "deadline_queue_join",
            "deadline_s3_active",
            "deadline_worker_status",
            "deadline_worker_terminal",
            "deadline_sentinel_ack",
        )
    ):
        return "live_observe"

    if state == "WORKER_STOPPED" and bool(getattr(outcome, "worker_stopped", False)):
        # Worker done; continuing would start HTTP (destructive).
        return "boundary"

    if reason in (
        "deadline_before_loop_stop",
        "deadline_before_drain",
        "structural_refused",
        "drain_not_started",
        "http_stopped_boundary",
    ) or reason.startswith("deadline_"):
        if reason in ("deadline_loop_stopped", "deadline_thread_join"):
            return "live_observe" if loop_stop_requested else "boundary"
        if reason in ("deadline_http_close", "http_close_incomplete"):
            return "live_observe"
        return "boundary"

    return "final_refuse"


async def _continue_sender_stop(
    state: _DrainOwnerState,
    *,
    proof: object,
    stop_isolated_sender: Any,
) -> Any:
    """Continue/observe the same TASK-48 stop procedure (no second owner session).

    Always observes the *current* started phase first (no next destructive step
    inside that wait). If the phase settles on a between-phase boundary and the
    session budget still allows new destructive work, resume via
    ``stop_isolated_sender``; after expiry, return the boundary/failure snapshot.
    """

    from integrations.telegram_bot import observe_started_sender_stop

    remaining = _remaining_from_session(state.session)
    state.sender_observing = True
    state.pulse()
    outcome = await observe_started_sender_stop(
        proof,
        timeout=(remaining if remaining > 0 else None),
    )
    kind = _classify_sender_outcome(outcome)
    if kind != "boundary":
        return outcome
    remaining = _remaining_from_session(state.session)
    if remaining <= 0:
        return outcome
    # Budget remains: next destructive phase is allowed under the same session.
    return await stop_isolated_sender(proof, timeout=remaining)


def _publish_deadline_wave(state: _DrainOwnerState) -> bool:
    """Publish one deadline partial wave to current waiters; owner keeps running.

    Returns True if this call performed the wave. Later joiners do not get another
    automatic deadline partial — they observe the same owner to soft/hard terminal.
    """

    if state.deadline_partial_wave_done:
        return False
    state.deadline_partial_wave_done = True
    err = _deadline_partial_error(state)
    for fut in list(state.waiters):
        if fut.done():
            continue
        state.deadline_partial_delivered.add(id(fut))
        fut.set_exception(err)
    return True


async def wait_p5_accepted_continuation_and_we_items(
    admission: WorkAdmission,
    *,
    progress: asyncio.Event | None = None,
) -> None:
    """P5: Accepted Futures done, AE continuations empty, WE unfinished_tasks==0.

    Does **not** cancel Futures/continuations. Empty queue alone is not drain —
    ``unfinished_tasks`` must reach 0. Not a full graceful stop. Deadline does
    not abort this observation — caller gates new destructive phases separately.
    """

    await admission.wait_accepted_executor_work()
    while True:
        cont = _continuation_states(admission)
        unfinished = _we_unfinished_profiles()
        if not cont and not unfinished:
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
    """Single owner sequence; waiter cancel / deadline partial must not stop this."""

    session = state.session
    producer_wait = state.producer_wait
    admission = session._host.admission  # noqa: SLF001

    try:
        # --- P4: keep observing until attested (no owner-side deadline abort) ---
        if not session.snapshot().producers_complete_attested:
            # Owner waits without a fresh budget; session.shutdown_deadline is
            # waiter-side only. Late proof is still accepted on this session.
            outcome = await producer_wait.wait_and_accept(session, deadline=None)
            if outcome.attestation is None:
                # Should not happen with deadline=None unless refused/failed.
                state.remainder = ["producers_incomplete"]
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
        state.remainder = [r for r in state.remainder if r != "producers_incomplete"]
        state.pulse()

        # After expiry: observe only — do not start P5→P6 destructive chain.
        # P5 itself is observation (no cancel); still run it so late Accepted
        # work is joined. P6/P7 remain gated.
        await wait_p5_accepted_continuation_and_we_items(
            admission,
            progress=state.progress,
        )
        state.last_completed_phase = DrainPhase.P5_ACCEPTED_ITEMS
        state.pulse()

        if not session._may_start_new_destructive_phases():  # noqa: SLF001
            # Soft terminal: deadline blocked P6; not a hard owner error.
            state.remainder = ["drain_deadline_passed", "p5_complete"]
            result = state.build_partial_result()
            state.result = result
            return result

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
            state.remainder = ["drain_deadline_passed", "we_stop_complete"]
            result = state.build_partial_result()
            state.result = result
            return result

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
        state.pulse()

        if not state.include_p8:
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

        if not session._may_start_new_destructive_phases():  # noqa: SLF001
            state.remainder = ["drain_deadline_passed", "p7_complete"]
            result = state.build_partial_result()
            state.result = result
            return result

        # --- P8: sender stop (lazy import — avoid arming telegram_bot on P4–P7) ---
        from integrations.telegram_bot import stop_isolated_sender

        proof = state.sender_ownership_proof
        assert proof is not None
        state.p8_started = True
        state.pulse()
        # Truthful mid-await diagnostics: attempted before the TASK-48 call.
        state.sender_attempted = True
        state.pulse()
        remaining = _remaining_from_session(session)
        outcome = await stop_isolated_sender(proof, timeout=remaining)
        _record_sender_outcome(state, outcome)
        state.pulse()

        while True:
            kind = _classify_sender_outcome(outcome)
            if kind == "success":
                state.sender_observing = False
                state.last_completed_phase = DrainPhase.P8_SENDER
                state.remainder = []
                result = DrainPhasesResult(
                    producers_attested=True,
                    p5_complete=True,
                    we_joined=state.we_joined,
                    registry_joined=state.registry_joined,
                    last_completed_phase=DrainPhase.P8_SENDER,
                    remainder=(),
                    sender_attempted=True,
                    sender_ok=True,
                    sender_reason=None,
                    sender_full_resource_stopped=True,
                    sender_lifecycle_state=getattr(
                        outcome, "lifecycle_state", "STOPPED"
                    ),
                    sender_phase=state.sender_phase,
                )
                state.result = result
                return result

            if kind == "live_observe":
                # Keep the same owner/session observing the already-started stop.
                # Do not cache a terminal result; waiters/rejoin see late completion.
                state.sender_observing = True
                state.pulse()
                outcome = await _continue_sender_stop(
                    state,
                    proof=proof,
                    stop_isolated_sender=stop_isolated_sender,
                )
                _record_sender_outcome(state, outcome)
                state.pulse()
                continue

            if kind == "boundary":
                # Would need a new destructive phase after budget expiry — soft stop.
                state.sender_observing = False
                rem = ["sender_partial", "sender_boundary"]
                if getattr(outcome, "reason", None):
                    rem.append(str(outcome.reason))
                state.remainder = rem
                state.last_completed_phase = DrainPhase.P7_REGISTRY
                result = state.build_partial_result()
                state.result = result
                return result

            # final_refuse — structured terminal refuse (not SESSION_TERMINAL).
            state.sender_observing = False
            rem = ["sender_refused"]
            if getattr(outcome, "reason", None):
                rem.append(str(outcome.reason))
            state.remainder = rem
            state.last_completed_phase = DrainPhase.P7_REGISTRY
            result = state.build_partial_result()
            state.result = result
            return result
    except BaseException as exc:
        if not isinstance(exc, asyncio.CancelledError):
            if state.error is None:
                state.error = exc
            state.pulse()
        raise
    finally:
        _publish_drain_waiters(state)


async def _join_owner_drain(
    session: ShutdownSession,
    producer_wait: PtbProducerWaitHost,
    *,
    include_p8: bool,
    sender_ownership_proof: object | None,
) -> DrainPhasesResult:
    """Shared join/observe for the single session-bound drain owner Task."""

    _assert_drain_identity(session, producer_wait)
    loop = asyncio.get_running_loop()

    state = _get_owner_state(session)
    if state is None:
        state = _DrainOwnerState(
            session=session,
            producer_wait=producer_wait,
            include_p8=include_p8,
        )
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
        if state.include_p8 != include_p8:
            raise DrainOrchestrationError(
                "drain owner already armed with a different P8 include mode",
                remainder=("include_p8_mismatch",),
            )

    # Proof identity before side effects and before any cached outcome.
    _bind_sender_ownership_proof(
        state, sender_ownership_proof, require=include_p8
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
        state.owner_task = loop.create_task(
            _owner_drain_p4_to_p7(state),
            name="antares-drain-p4-p8-owner" if include_p8 else "antares-drain-p4-p7-owner",
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

    if state.owner_task.done() and not waiter.done():
        _retrieve_drain_owner_exception(state)
        if state.error is not None:
            waiter.set_exception(state.error)
        elif state.result is not None:
            waiter.set_result(state.result)

    deadline = session.shutdown_deadline
    helper_tasks: list[asyncio.Task[Any]] = []

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
        helper_tasks.extend(helpers)
        try:
            await asyncio.wait(helpers, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in helpers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*helpers, return_exceptions=True)
            for task in helpers:
                with contextlib.suppress(ValueError):
                    helper_tasks.remove(task)
        state.progress.clear()

    async def _cancel_own_helpers() -> None:
        pending = [t for t in list(helper_tasks) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        helper_tasks.clear()

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

            deadline_passed = (
                deadline is not None
                and session._drain_deadline_passed()  # noqa: SLF001
            )
            if deadline_passed and not state.deadline_partial_wave_done:
                _publish_deadline_wave(state)
                break

            wait_tasks: list[asyncio.Future[Any]] = []
            try:
                wait_tasks = [
                    asyncio.ensure_future(asyncio.shield(waiter)),
                    asyncio.ensure_future(_wait_progress_or_deadline()),
                ]
                done, pending = await asyncio.wait(
                    wait_tasks, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                for task in wait_tasks:
                    if not task.done():
                        task.cancel()
                if wait_tasks:
                    await asyncio.gather(*wait_tasks, return_exceptions=True)
                await _cancel_own_helpers()
                raise
            else:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    with contextlib.suppress(
                        asyncio.CancelledError, asyncio.InvalidStateError
                    ):
                        if not task.cancelled():
                            task.exception()

        return await waiter
    except asyncio.CancelledError:
        try:
            state.waiters.remove(waiter)
        except ValueError:
            pass
        if not waiter.done():
            waiter.cancel()
        await _cancel_own_helpers()
        raise
    finally:
        await _cancel_own_helpers()


async def run_owner_drain_p4_to_p7(
    session: ShutdownSession,
    producer_wait: PtbProducerWaitHost,
) -> DrainPhasesResult:
    """Join/observe the single P4→P7 owner Task bound to ``session`` (no P8)."""

    return await _join_owner_drain(
        session,
        producer_wait,
        include_p8=False,
        sender_ownership_proof=None,
    )


async def run_owner_drain_p4_to_p8(
    session: ShutdownSession,
    producer_wait: PtbProducerWaitHost,
    *,
    sender_ownership_proof: object,
) -> DrainPhasesResult:
    """Join/observe P4→P8 under one session-bound owner.

    ``sender_ownership_proof`` must be the exact process attestation (e.g.
    ``AntaresBootPrefix.sender_ownership``). Identity is checked before side
    effects and before cached results. Timeout is only the remaining
    ``session.shutdown_deadline``. P8 success is not SESSION_TERMINAL; P9 is 49.D.
    """

    return await _join_owner_drain(
        session,
        producer_wait,
        include_p8=True,
        sender_ownership_proof=sender_ownership_proof,
    )


def require_producer_host_pre_initialize(application: Any) -> None:
    """Documented check: host must already be installed (pre-initialize)."""

    if getattr(application, "_antares_ptb_producer_wait_host", None) is None:
        raise DrainOrchestrationError(
            "producer-wait host must be installed before Application.initialize()/start()"
        )
