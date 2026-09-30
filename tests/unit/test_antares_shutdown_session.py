"""TASK-49.S: owner shutdown-session primitive (Event/barrier, controllable clock)."""

from __future__ import annotations

import asyncio

import pytest

from modules.antares.shutdown_session import (
    ApplicationHttpState,
    CleanupStatus,
    ControllableClock,
    ProducersCompleteAttestation,
    SessionState,
    ShutdownCause,
    ShutdownPath,
    ShutdownSessionError,
    ShutdownSessionHost,
)
from modules.antares.work_admission import (
    WorkAdmission,
    bind_antares_admission,
    reset_antares_admission_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_admission():
    reset_antares_admission_for_tests()
    yield
    reset_antares_admission_for_tests()


def _bound_open_admission() -> WorkAdmission:
    admission = WorkAdmission()
    bind_antares_admission(admission)
    admission.open()
    return admission


def _make_host(
    *,
    drain_timeout: float = 30.0,
    cleanup_observe_timeout: float = 5.0,
    clock: ControllableClock | None = None,
    open_admission: bool = True,
) -> tuple[ShutdownSessionHost, WorkAdmission, asyncio.Event, ControllableClock]:
    loop = asyncio.get_running_loop()
    clk = clock if clock is not None else ControllableClock()
    clk.bind_loop()
    if open_admission:
        admission = _bound_open_admission()
    else:
        admission = WorkAdmission()
        bind_antares_admission(admission)
    stop = asyncio.Event()
    host = ShutdownSessionHost(
        admission=admission,
        stop=stop,
        loop=loop,
        drain_timeout=drain_timeout,
        cleanup_observe_timeout=cleanup_observe_timeout,
        clock=clk,
    )
    return host, admission, stop, clk


def test_first_arm_creates_one_session_and_owner_task() -> None:
    async def _main() -> None:
        host, admission, stop, _clk = _make_host()
        s1 = host.arm_request_stop(had_open=True)
        await s1.wait_arm_effects()
        assert host.session is s1
        assert s1.owner_task is not None
        assert not s1.owner_task.done()
        assert admission.state.value == "sealed"
        assert stop.is_set()
        assert s1.shutdown_deadline is not None
        s1.owner_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await s1.owner_task

    asyncio.run(_main())


def test_repeat_and_concurrent_arm_do_not_duplicate_owner() -> None:
    async def _main() -> None:
        host, _admission, stop, _clk = _make_host()
        barrier = asyncio.Barrier(3)
        results: list = []

        async def arm_one() -> None:
            await barrier.wait()
            results.append(host.arm_request_stop(had_open=True))

        t1 = asyncio.create_task(arm_one())
        t2 = asyncio.create_task(arm_one())
        await barrier.wait()
        await asyncio.gather(t1, t2)
        s = host.arm_request_stop(had_open=True)
        assert len({id(x) for x in results + [s]}) == 1
        assert s.owner_task is not None
        owner = s.owner_task
        assert host.arm_cancel_after_open(asyncio.CancelledError(), had_open=True) is s
        assert s.owner_task is owner
        deadline = s.shutdown_deadline
        host.arm_request_stop(had_open=True)
        assert s.shutdown_deadline == deadline
        assert stop.is_set()
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner

    asyncio.run(_main())


def test_deadline_preserved_on_repeat() -> None:
    async def _main() -> None:
        host, _a, _stop, clk = _make_host(drain_timeout=10.0)
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        d0 = s.shutdown_deadline
        assert d0 == pytest.approx(clk.monotonic() + 10.0)
        clk.advance(3.0)
        host.arm_request_stop(had_open=True)
        assert s.shutdown_deadline == d0
        assert s.owner_task is not None
        s.owner_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await s.owner_task

    asyncio.run(_main())


def test_cancel_waiter_does_not_cancel_owner_or_other_waiters() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        entered = asyncio.Event()
        other_outcome: list[str] = []

        async def other_waiter() -> None:
            entered.set()
            try:
                await s.wait_terminal()
            except asyncio.CancelledError:
                other_outcome.append("cancelled")
                raise
            other_outcome.append("terminal")

        other = asyncio.create_task(other_waiter())
        await entered.wait()

        waiter = asyncio.create_task(s.wait_terminal())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert s.owner_task is not None and not s.owner_task.done()
        assert not other.done()

        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def quick_cleanup() -> str:
            return "ok"

        s.start_cleanup(quick_cleanup)
        await other
        assert other_outcome == ["terminal"]

    asyncio.run(_main())


def test_cancel_after_open_with_stop_unset_seals_and_sets() -> None:
    async def _main() -> None:
        host, admission, stop, _clk = _make_host()
        assert not stop.is_set()
        assert admission.state.value == "open"
        primary = asyncio.CancelledError()
        s = host.arm_cancel_after_open(primary, had_open=True)
        await s.wait_arm_effects()
        assert admission.state.value == "sealed"
        assert stop.is_set()
        assert s.cause is ShutdownCause.CANCEL_AFTER_OPEN
        assert s.path is ShutdownPath.POST_OPEN
        assert s.owner_task is not None
        s.owner_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await s.owner_task

    asyncio.run(_main())


def test_startup_failure_does_not_take_post_open_path() -> None:
    async def _main() -> None:
        host, admission, stop, _clk = _make_host(open_admission=False)
        primary = RuntimeError("init failed")
        s = host.arm_startup_failure(primary)
        assert s.path is ShutdownPath.STARTUP
        assert s.cause is ShutdownCause.STARTUP_FAILURE
        assert s.shutdown_deadline is None
        await s.wait_arm_effects()
        assert not stop.is_set()
        assert admission.state.value == "bound_closed"

        with pytest.raises(ShutdownSessionError):
            s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def staged() -> str:
            return "staged"

        s.start_cleanup(staged)
        term = await s.wait_terminal()
        assert term.snapshot.application_http is ApplicationHttpState.STARTUP_CLEANED
        assert term.primary is primary
        assert term.snapshot.overall_ok is None

    asyncio.run(_main())


def test_post_open_arm_refuses_without_had_open_proof() -> None:
    async def _main() -> None:
        host, admission, _stop, _clk = _make_host()
        admission.seal()
        with pytest.raises(ShutdownSessionError, match="OPEN"):
            host.arm_request_stop(had_open=False)
        assert host.session is None

    asyncio.run(_main())


def test_foreign_loop_rejected_before_side_effects() -> None:
    async def _main() -> None:
        host, admission, stop, _clk = _make_host()
        foreign = asyncio.new_event_loop()
        try:
            foreign_host = ShutdownSessionHost(
                admission=admission,
                stop=stop,
                loop=foreign,
                drain_timeout=1.0,
                cleanup_observe_timeout=1.0,
            )
            with pytest.raises(ShutdownSessionError, match="foreign"):
                foreign_host.arm_request_stop(had_open=True)
            assert not stop.is_set()
            assert admission.state.value == "open"
            assert foreign_host.session is None
        finally:
            foreign.close()

    asyncio.run(_main())


def test_cleanup_invoked_once() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())
        calls = 0

        async def cleanup() -> str:
            nonlocal calls
            calls += 1
            return "once"

        t1 = s.start_cleanup(cleanup)
        t2 = s.start_cleanup(cleanup)
        assert t1 is t2
        await s.wait_terminal()
        assert calls == 1

    asyncio.run(_main())


def test_observe_timeout_snapshot_cleanup_continues() -> None:
    async def _main() -> None:
        host, _a, _stop, clk = _make_host(cleanup_observe_timeout=2.0)
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())
        release = asyncio.Event()

        async def slow_cleanup() -> str:
            await release.wait()
            return "late"

        s.start_cleanup(slow_cleanup)
        await s.wait_until(
            lambda snap: snap.cleanup_status is CleanupStatus.IN_PROGRESS
        )
        clk.advance(2.0)
        snap = await s.wait_until(
            lambda s_: s_.cleanup_status is CleanupStatus.IN_PROGRESS_OBSERVE_EXCEEDED
        )
        assert snap.session_state is SessionState.CLEANUP_IN_PROGRESS
        assert not snap.is_terminal
        assert s.terminal_result() is None
        assert s.cleanup_task is not None and not s.cleanup_task.done()

        release.set()
        term = await s.wait_terminal()
        assert term.snapshot.is_terminal
        assert term.snapshot.cleanup_status is CleanupStatus.DONE
        assert term.cleanup_result == "late"

    asyncio.run(_main())


def test_cleanup_error_reflected_in_terminal() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def boom() -> None:
            raise ValueError("cleanup boom")

        s.start_cleanup(boom)
        term = await s.wait_terminal()
        assert term.cleanup_error is not None
        assert isinstance(term.cleanup_error, ValueError)
        assert term.snapshot.overall_ok is False
        assert "cleanup_failed" in term.snapshot.remainder

    asyncio.run(_main())


def test_repeat_after_terminal_failure_no_recovery() -> None:
    async def _main() -> None:
        host, _a, stop, clk = _make_host(drain_timeout=5.0)
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def boom() -> None:
            raise RuntimeError("fail")

        s.start_cleanup(boom)
        term1 = await s.wait_terminal()
        d0 = s.shutdown_deadline
        assert stop.is_set()

        s2 = host.arm_request_stop(had_open=True)
        assert s2 is s
        assert s2.shutdown_deadline == d0
        term2 = await s2.wait_terminal()
        assert term2 is term1
        clk.advance(100.0)
        assert s.owner_task is not None and s.owner_task.done()
        assert s.cleanup_task is not None and s.cleanup_task.done()

    asyncio.run(_main())


def test_no_false_overall_ok_without_phase_results() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def ok() -> str:
            return "cleaned"

        s.start_cleanup(ok)
        term = await s.wait_terminal()
        assert term.cleanup_error is None
        assert term.snapshot.overall_ok is None
        assert term.snapshot.application_http is ApplicationHttpState.CLEANUP_DONE

    asyncio.run(_main())


def test_post_open_cleanup_refused_without_attestation() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()

        async def cleanup() -> str:
            return "nope"

        with pytest.raises(ShutdownSessionError, match="Attestation"):
            s.start_cleanup(cleanup)
        assert s.snapshot().cleanup_status is CleanupStatus.NOT_STARTED
        assert s.owner_task is not None
        s.owner_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await s.owner_task

    asyncio.run(_main())


def test_drain_expiry_snapshot_no_terminal_without_cleanup() -> None:
    async def _main() -> None:
        host, _a, _stop, clk = _make_host(drain_timeout=2.0)
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        clk.advance(2.0)
        snap = await s.wait_until(
            lambda s_: s_.session_state is SessionState.DRAIN_SNAPSHOT
        )
        assert snap.drain_expired
        assert not snap.may_start_new_destructive_phases
        assert not snap.is_terminal
        assert "producers_incomplete" in snap.remainder
        assert s.terminal_result() is None
        assert s.owner_task is not None
        s.owner_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await s.owner_task

    asyncio.run(_main())


def test_done_tasks_do_not_leave_unretrieved_exceptions() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def boom() -> None:
            raise KeyError("x")

        s.start_cleanup(boom)
        term = await s.wait_terminal()
        assert isinstance(term.cleanup_error, KeyError)
        assert s.cleanup_task is not None
        # Error absorbed into session state; Task itself completed without pending exc.
        assert s.cleanup_task.exception() is None
        assert s.owner_task is not None
        assert s.owner_task.exception() is None

    asyncio.run(_main())


def test_startup_cannot_be_armed_after_open_flag() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        with pytest.raises(ShutdownSessionError, match="OPEN"):
            host.arm_startup_failure(RuntimeError("x"), had_open=True)

    asyncio.run(_main())


def test_waiter_cancel_preserves_cancelled_error_type() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        waiter = asyncio.create_task(s.wait_terminal())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert s.owner_task is not None and not s.owner_task.done()
        s.owner_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await s.owner_task

    asyncio.run(_main())
