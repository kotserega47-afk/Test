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
    TaskLiveness,
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
        # Contract: seal if admission bound (startup path).
        assert admission.state.value == "sealed"

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
        assert term.snapshot.application_http is ApplicationHttpState.OPEN
        assert "cleanup_failed" in term.snapshot.remainder
        assert "application_http_open" in term.snapshot.remainder
        assert "producers_incomplete" not in term.snapshot.remainder
        assert "ptb_cleanup_not_started" not in term.snapshot.remainder

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
        assert "drain_deadline_exceeded" in snap.remainder
        assert "producers_incomplete" in snap.remainder
        assert "ptb_cleanup_not_started" in snap.remainder
        assert "application_http_open" in snap.remainder
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


def test_deadline_permission_false_before_watcher_yields() -> None:
    async def _main() -> None:
        host, _a, _stop, clk = _make_host(drain_timeout=2.0)
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        assert s.snapshot().may_start_new_destructive_phases is True
        # Advance past deadline without awaiting the drain watcher.
        clk.advance(2.0)
        snap = s.snapshot()
        assert snap.drain_expired is True
        assert snap.may_start_new_destructive_phases is False
        assert s.owner_task is not None
        s.owner_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await s.owner_task

    asyncio.run(_main())


def test_terminal_before_deadline_forbids_destructive_phases() -> None:
    async def _main() -> None:
        host, _a, _stop, clk = _make_host(drain_timeout=100.0)
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def ok() -> str:
            return "done"

        s.start_cleanup(ok)
        term = await s.wait_terminal()
        assert clk.monotonic() < s.shutdown_deadline  # type: ignore[operator]
        assert term.snapshot.may_start_new_destructive_phases is False
        assert s.snapshot() is term.snapshot
        assert s.snapshot().may_start_new_destructive_phases is False
        assert s.snapshot().is_terminal is True

    asyncio.run(_main())


def test_foreign_loop_after_arm_rejects_mutating_apis() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        owner = s.owner_task
        attested_before = s.snapshot().producers_complete_attested
        cleanup_before = s.cleanup_task

        foreign = asyncio.new_event_loop()
        errors: list[BaseException] = []

        def run_foreign() -> None:
            asyncio.set_event_loop(foreign)
            try:

                async def bad_accept() -> None:
                    s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

                async def bad_cleanup() -> None:
                    async def cb() -> str:
                        return "x"

                    s.start_cleanup(cb)

                async def bad_wait() -> None:
                    await s.wait_terminal()

                for coro_factory in (bad_accept, bad_cleanup, bad_wait):
                    try:
                        foreign.run_until_complete(coro_factory())
                    except BaseException as exc:
                        errors.append(exc)
            finally:
                foreign.close()

        import threading

        thread = threading.Thread(target=run_foreign)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(errors) == 3
        assert all(isinstance(e, ShutdownSessionError) for e in errors)
        assert s.snapshot().producers_complete_attested is attested_before
        assert s.cleanup_task is cleanup_before
        assert s.owner_task is owner
        assert owner is not None and not owner.done()
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner

    asyncio.run(_main())


def test_cleanup_callback_cancelled_error_reaches_terminal() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def boom_cancel() -> None:
            raise asyncio.CancelledError()

        s.start_cleanup(boom_cancel)
        term = await asyncio.wait_for(s.wait_terminal(), timeout=2)
        assert term.snapshot.cleanup_status is CleanupStatus.DONE
        assert term.snapshot.application_http is ApplicationHttpState.OPEN
        assert isinstance(term.cleanup_error, asyncio.CancelledError)
        assert "cleanup_cancelled" in term.snapshot.remainder
        assert term.snapshot.overall_ok is False
        assert s.owner_task is not None and s.owner_task.done()

    asyncio.run(_main())


def test_cleanup_task_cancelled_before_callback_runs() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())
        started = asyncio.Event()

        async def never() -> str:
            started.set()
            await asyncio.Event().wait()
            return "no"

        task = s.start_cleanup(never)
        # Cancel before the callback body can observe started (best-effort pre-run).
        task.cancel()
        term = await asyncio.wait_for(s.wait_terminal(), timeout=2)
        assert term.snapshot.is_terminal
        assert term.snapshot.cleanup_status is CleanupStatus.DONE
        assert term.snapshot.application_http is ApplicationHttpState.OPEN
        assert term.cleanup_error is not None
        assert "cleanup_task_cancelled" in term.snapshot.remainder or (
            "cleanup_cancelled" in term.snapshot.remainder
        )
        assert s.owner_task is not None and s.owner_task.done()

    asyncio.run(_main())


def test_waiter_cancel_does_not_become_session_primary() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        assert s.primary is None
        waiter = asyncio.create_task(s.wait_terminal())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert s.primary is None
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def ok() -> str:
            return "ok"

        s.start_cleanup(ok)
        term = await s.wait_terminal()
        assert term.primary is None

    asyncio.run(_main())


def test_cancel_after_request_stop_preserves_primary() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        assert s.primary is None
        original = asyncio.CancelledError()
        s2 = host.arm_cancel_after_open(original, had_open=True)
        assert s2 is s
        assert s.primary is original
        assert s.cause is ShutdownCause.CANCEL_AFTER_OPEN
        deadline = s.shutdown_deadline
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def ok() -> str:
            return "ok"

        s.start_cleanup(ok)
        term = await s.wait_terminal()
        assert term.primary is original
        assert s.shutdown_deadline == deadline
        # Read-only after terminal.
        again = host.arm_cancel_after_open(asyncio.CancelledError(), had_open=True)
        assert again is s
        assert again.terminal_result() is term
        assert again.primary is original

    asyncio.run(_main())


def test_incompatible_startup_arm_on_live_post_open_refused() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        with pytest.raises(ShutdownSessionError, match="incompatible"):
            host.arm_startup_failure(RuntimeError("x"), had_open=False)
        assert host.session is s
        assert s.owner_task is not None and not s.owner_task.done()
        s.owner_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await s.owner_task

    asyncio.run(_main())


def test_startup_arm_refused_when_admission_actually_open() -> None:
    async def _main() -> None:
        host, admission, _stop, _clk = _make_host(open_admission=True)
        assert admission.state.value == "open"
        with pytest.raises(ShutdownSessionError, match="OPEN"):
            host.arm_startup_failure(RuntimeError("x"), had_open=False)
        assert host.session is None

    asyncio.run(_main())


def test_wait_until_after_terminal_with_false_predicate() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def ok() -> str:
            return "ok"

        s.start_cleanup(ok)
        term = await s.wait_terminal()
        snap = await asyncio.wait_for(
            s.wait_until(lambda s_: s_.cleanup_status is CleanupStatus.IN_PROGRESS),
            timeout=1,
        )
        assert snap.is_terminal
        assert snap is term.snapshot

    asyncio.run(_main())


def test_cleanup_started_forbids_new_drain_phases() -> None:
    async def _main() -> None:
        host, _a, _stop, clk = _make_host(drain_timeout=50.0)
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())
        release = asyncio.Event()

        async def slow() -> str:
            await release.wait()
            return "x"

        s.start_cleanup(slow)
        assert s.snapshot().may_start_new_destructive_phases is False
        clk.advance(1.0)
        assert s.snapshot().may_start_new_destructive_phases is False
        release.set()
        await s.wait_terminal()

    asyncio.run(_main())


def test_foreign_thread_snapshot_after_deadline_does_not_mutate() -> None:
    async def _main() -> None:
        import threading

        host, _a, _stop, clk = _make_host(drain_timeout=2.0)
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        assert s.snapshot().may_start_new_destructive_phases is True
        clk.advance(2.0)

        # Do not await here: that would let the owner-loop drain watcher mutate.
        state_before = s._state  # noqa: SLF001 — mutation probe
        drain_flag_before = s._drain_expired  # noqa: SLF001
        changed_before = s._changed  # noqa: SLF001
        box: dict[str, object] = {}

        def foreign_snapshot() -> None:
            box["snap"] = s.snapshot()
            box["host_snap"] = host.snapshot()
            box["changed_after"] = s._changed  # noqa: SLF001
            box["state_after"] = s._state  # noqa: SLF001
            box["drain_after"] = s._drain_expired  # noqa: SLF001

        thread = threading.Thread(target=foreign_snapshot)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()

        snap = box["snap"]
        host_snap = box["host_snap"]
        assert snap.drain_expired is True  # type: ignore[union-attr]
        assert snap.may_start_new_destructive_phases is False  # type: ignore[union-attr]
        assert snap.session_state is SessionState.DRAIN_SNAPSHOT  # type: ignore[union-attr]
        assert host_snap is not None
        assert host_snap.session_state is SessionState.DRAIN_SNAPSHOT  # type: ignore[union-attr]
        assert box["state_after"] is state_before
        assert box["drain_after"] is drain_flag_before
        assert box["changed_after"] is changed_before
        assert s._state is state_before  # noqa: SLF001
        assert s._drain_expired is drain_flag_before  # noqa: SLF001
        assert s._changed is changed_before  # noqa: SLF001
        assert s.terminal_result() is None
        assert s.owner_task is not None
        s.owner_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await s.owner_task

    asyncio.run(_main())


def test_late_successful_cleanup_after_drain_snapshot_remainder() -> None:
    async def _main() -> None:
        host, _a, _stop, clk = _make_host(drain_timeout=2.0)
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        clk.advance(2.0)
        snap = await s.wait_until(
            lambda s_: s_.session_state is SessionState.DRAIN_SNAPSHOT
        )
        assert "producers_incomplete" in snap.remainder
        assert "ptb_cleanup_not_started" in snap.remainder
        assert "application_http_open" in snap.remainder

        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())
        mid = s.snapshot()
        assert mid.producers_complete_attested is True
        assert "producers_incomplete" not in mid.remainder
        assert "drain_deadline_exceeded" in mid.remainder
        assert "ptb_cleanup_not_started" in mid.remainder
        assert "application_http_open" in mid.remainder

        async def ok() -> str:
            return "cleaned"

        s.start_cleanup(ok)
        term = await s.wait_terminal()
        rem = term.snapshot.remainder
        assert term.snapshot.producers_complete_attested is True
        assert term.snapshot.cleanup_status is CleanupStatus.DONE
        assert term.snapshot.application_http is ApplicationHttpState.CLEANUP_DONE
        assert "drain_deadline_exceeded" in rem
        assert "producers_incomplete" not in rem
        assert "ptb_cleanup_not_started" not in rem
        assert "application_http_open" not in rem
        assert "ptb_cleanup_in_progress" not in rem
        assert s.snapshot() is term.snapshot
        assert host.snapshot() is term.snapshot

    asyncio.run(_main())


def test_late_failed_cleanup_after_drain_keeps_open_http_remainder() -> None:
    async def _main() -> None:
        host, _a, _stop, clk = _make_host(drain_timeout=2.0)
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        clk.advance(2.0)
        await s.wait_until(lambda s_: s_.session_state is SessionState.DRAIN_SNAPSHOT)
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def boom() -> None:
            raise RuntimeError("late cleanup fail")

        s.start_cleanup(boom)
        term = await s.wait_terminal()
        rem = term.snapshot.remainder
        assert term.snapshot.producers_complete_attested is True
        assert term.snapshot.cleanup_status is CleanupStatus.DONE
        assert term.snapshot.application_http is ApplicationHttpState.OPEN
        assert "drain_deadline_exceeded" in rem
        assert "cleanup_failed" in rem
        assert "application_http_open" in rem
        assert "producers_incomplete" not in rem
        assert "ptb_cleanup_not_started" not in rem
        assert s.snapshot().remainder == rem

    asyncio.run(_main())


def test_frozen_terminal_task_alive_at_publish_vs_current_liveness() -> None:
    async def _main() -> None:
        host, _a, _stop, _clk = _make_host()
        s = host.arm_request_stop(had_open=True)
        await s.wait_arm_effects()
        s.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        async def ok() -> str:
            return "ok"

        s.start_cleanup(ok)
        term = await s.wait_terminal()
        frozen = term.snapshot
        assert frozen is s.snapshot()
        assert frozen.owner_task_alive_at_publish is True
        assert frozen.cleanup_task_alive_at_publish is False
        assert s.owner_task is not None
        await asyncio.wait_for(asyncio.shield(s.owner_task), timeout=2)
        assert s.owner_task.done()
        assert s.snapshot() is frozen
        assert s.snapshot().owner_task_alive_at_publish is True
        live = s.current_task_liveness()
        assert isinstance(live, TaskLiveness)
        assert live.owner_task_alive is False
        assert live.cleanup_task_alive is False

    asyncio.run(_main())
