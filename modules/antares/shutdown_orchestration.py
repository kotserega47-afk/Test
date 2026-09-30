"""TASK-49.B: post-OPEN drain orchestration P4–P7 (Q-HLP1 sibling module).

Layout (Q-HLP1): **separate module** under ``modules.antares``, not inlined into
``run_ptb_lifecycle``. ``ShutdownSession`` / ``ShutdownSessionHost`` remain the
single owner-session primitive (49.S); this module sequences accepted drain
phases against that session. ``run_ptb_lifecycle`` stays the initialize/start/
stop/cleanup shell — full P8–P10 / SESSION_TERMINAL wiring is **out of scope**.

Supported path requires TASK-49.A conditions: producer host installed once
before ``Application.initialize()``, Antares intake queue, Application/issuer
binding, no untracked producers, permanent entry refuse after COMPLETE.

Partial boundary: successful P7 does **not** mean full Antares shutdown,
production readiness, or SESSION_TERMINAL. P8 sender / P9 executor remain
future slices. Direct PTB cleanup must not bypass these phases.
"""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass
from typing import Any

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


async def wait_p5_accepted_continuation_and_we_items(
    admission: WorkAdmission,
    *,
    progress: asyncio.Event | None = None,
) -> None:
    """P5: Accepted Futures done, AE continuations empty, WE unfinished_tasks==0.

    Does **not** cancel Futures/continuations. Empty queue alone is not drain —
    ``unfinished_tasks`` must reach 0. Not a full graceful stop.
    """

    await admission.wait_accepted_executor_work()
    while True:
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


async def run_owner_drain_p4_to_p7(
    session: ShutdownSession,
    producer_wait: PtbProducerWaitHost,
    *,
    we_timeout: float | None = None,
    registry_timeout: float | None = None,
    producers_deadline: float | None = None,
) -> DrainPhasesResult:
    """Run P4→P5→P6→P7 under one owner shutdown-session.

    Preconditions:
    - ``session`` is the live post-OPEN session on the owner loop;
    - ``producer_wait`` is the bound issuer for this Application;
    - producers proof is accepted via Q-PTB1 (never a naked bool invent).

    After P7 returns successfully, caller must **not** treat this as full
    shutdown: P8/P9/P10 and SESSION_TERMINAL remain separate.
    """

    session._require_owner_loop()  # noqa: SLF001
    admission = session._host.admission  # noqa: SLF001
    remainder: list[str] = []
    last: DrainPhase | None = None
    we_joined: tuple[str, ...] = ()
    registry_joined: tuple[str, ...] = ()

    if not session._may_start_new_destructive_phases():  # noqa: SLF001
        raise DrainOrchestrationError(
            "cannot start drain phases after shutdown deadline expiry",
            remainder=("drain_deadline_passed",),
        )

    # --- P4: truthful producers_complete ---
    if not session.snapshot().producers_complete_attested:
        outcome = await producer_wait.wait_and_accept(
            session, deadline=producers_deadline
        )
        if outcome.attestation is None:
            raise DrainOrchestrationError(
                "PTB producers incomplete; P5–P7 refused",
                remainder=("producers_incomplete",),
            )
    if not session.snapshot().producers_complete_attested:
        raise DrainOrchestrationError(
            "producers attestation missing after P4",
            remainder=("producers_not_attested",),
        )
    last = DrainPhase.P4_PRODUCERS

    if not session._may_start_new_destructive_phases():  # noqa: SLF001
        raise DrainOrchestrationError(
            "drain deadline passed after P4; P5–P7 not started",
            remainder=("drain_deadline_passed", "producers_complete"),
        )

    # --- P5 ---
    try:
        await wait_p5_accepted_continuation_and_we_items(admission)
    except asyncio.CancelledError:
        raise
    last = DrainPhase.P5_ACCEPTED_ITEMS

    if not session._may_start_new_destructive_phases():  # noqa: SLF001
        raise DrainOrchestrationError(
            "drain deadline passed after P5; P6–P7 not started",
            remainder=("drain_deadline_passed", "p5_complete"),
        )

    # --- P6: WE owner-session stop (requires attested producers) ---
    try:
        we_joined = await stop_isolated_profile_workers(
            admission,
            producers_complete=True,
            timeout=we_timeout,
        )
    except IsolatedProfileWorkerStopError as exc:
        rem = getattr(exc, "remainder", None)
        raise DrainOrchestrationError(
            f"P6 WE stop failed: {exc}",
            remainder=rem,
        ) from exc
    last = DrainPhase.P6_WE_STOP

    if not session._may_start_new_destructive_phases():  # noqa: SLF001
        # P6 already started and completed; do not start P7 after expiry.
        raise DrainOrchestrationError(
            "drain deadline passed after P6; P7 registry freeze not started",
            remainder=("drain_deadline_passed", "we_stop_complete"),
        )

    # --- P7: registry only after successful P6 ---
    try:
        registry_joined = await wait_isolated_registry_daemon_ops(
            admission,
            producers_complete=True,
            timeout=registry_timeout,
        )
    except IsolatedRegistryDaemonStopError as exc:
        rem = getattr(exc, "remainder", None)
        raise DrainOrchestrationError(
            f"P7 registry wait failed: {exc}",
            remainder=rem,
        ) from exc
    last = DrainPhase.P7_REGISTRY

    return DrainPhasesResult(
        producers_attested=True,
        p5_complete=True,
        we_joined=we_joined,
        registry_joined=registry_joined,
        last_completed_phase=last,
        remainder=tuple(remainder),
    )


def require_producer_host_pre_initialize(application: Any) -> None:
    """Documented check: host must already be installed (pre-initialize)."""

    if getattr(application, "_antares_ptb_producer_wait_host", None) is None:
        raise DrainOrchestrationError(
            "producer-wait host must be installed before Application.initialize()/start()"
        )
