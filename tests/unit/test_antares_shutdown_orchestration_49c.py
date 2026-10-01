"""TASK-49.C: P8 sender stop + ownership proof plumbing regressions."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from telegram import User
from telegram.ext import Application, TypeHandler

import automation.worker as worker_mod
from core.antares_sender_ownership import (
    AntaresSenderOwnershipAttestation,
    _reset_antares_sender_ownership_for_tests,
    claim_antares_sender_ownership,
)
from core.job_dispatch import _reset_job_executor_for_tests
from integrations.wallet_editor_registry_async import (
    _reset_registry_daemon_ops_for_tests,
)
from modules.antares.ptb_producer_wait import (
    PtbProducerWaitHost,
    attach_producer_wait_to_shutdown_host,
)
from modules.antares.ptb_update_intake import AntaresUpdateIntakeQueue
from modules.antares.shutdown_orchestration import (
    DrainOrchestrationError,
    DrainPhase,
    _ATTR,
    observe_drain_orchestration,
    run_owner_drain_p4_to_p7,
    run_owner_drain_p4_to_p8,
)
from modules.antares.shutdown_session import (
    ControllableClock,
    ShutdownSessionHost,
)
from modules.antares.work_admission import (
    WorkAdmission,
    bind_antares_admission,
    reset_antares_admission_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_all():
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    _reset_registry_daemon_ops_for_tests()
    _reset_antares_sender_ownership_for_tests()
    worker_mod._profile_workers = {}
    worker_mod._profile_workers_frozen = False
    worker_mod._profile_workers_stop_done = False
    worker_mod._reset_we_stop_owner_for_tests()
    yield
    for worker in list(worker_mod._profile_workers.values()):
        thread = worker.thread
        if thread is not None and thread.is_alive() and not worker.sentinel_put:
            worker.queue.put(worker_mod.PROFILE_WORKER_STOP)
            worker.sentinel_put = True
        if thread is not None:
            thread.join(timeout=5)
    worker_mod._profile_workers = {}
    worker_mod._reset_we_stop_owner_for_tests()
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    _reset_registry_daemon_ops_for_tests()
    _reset_antares_sender_ownership_for_tests()


def _patch_telegram_http():
    user = User(id=1, first_name="C", is_bot=True, username="bot")

    async def fake_do_request(self, *args, **kwargs):
        return 200, json.dumps({"ok": True, "result": user.to_dict()}).encode()

    return patch(
        "telegram.request._httpxrequest.HTTPXRequest.do_request",
        new=fake_do_request,
    )


def _build_app():
    queue = AntaresUpdateIntakeQueue()
    app = (
        Application.builder()
        .token("1:test-token")
        .concurrent_updates(True)
        .update_queue(queue)
        .build()
    )
    return app, queue


async def _start_app(app: Application) -> None:
    with _patch_telegram_http():
        await app.initialize()
        await app.start()


async def _shutdown_app(app: Application) -> None:
    if app.running:
        await app.stop()
    if getattr(app, "_initialized", False):
        await app.shutdown()


async def _arm(loop, clk=None, *, drain_timeout: float = 30.0):
    app, queue = _build_app()
    admission = WorkAdmission()
    bind_antares_admission(admission)
    admission.open()
    stop = asyncio.Event()
    kwargs = {
        "admission": admission,
        "stop": stop,
        "loop": loop,
        "drain_timeout": drain_timeout,
    }
    if clk is not None:
        kwargs["clock"] = clk
    shut = ShutdownSessionHost(**kwargs)
    host = PtbProducerWaitHost(
        application=app, loop=loop, clock=clk if clk is not None else None
    )
    attach_producer_wait_to_shutdown_host(shut, host)
    await _start_app(app)
    session = shut.arm_request_stop(had_open=True)
    await session.wait_arm_effects()
    host.seal_intake()
    return app, queue, host, shut, session, admission


def _full_ok(**kwargs):
    base = dict(
        ok=True,
        reason=None,
        full_resource_stopped=True,
        lifecycle_state="STOPPED",
        ownership_passed=True,
        ptb_passed=True,
        structural_passed=True,
        intake_sealed=True,
        worker_stopped=True,
        worker_terminal=True,
        bot_shutdown_attempted=True,
        bot_shutdown_ok=True,
        bot_shutdown_error_type=None,
        bot_shutdown_error_text=None,
        request_close_results=(),
        http_stopped=True,
        loop_stop_requested=True,
        loop_running=False,
        loop_thread_alive=False,
        thread_joined=True,
        terminal_intake_failure_total=0,
        recent_intake_failures=(),
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _partial(**kwargs):
    base = dict(
        ok=False,
        reason="deadline_before_loop_stop",
        full_resource_stopped=False,
        lifecycle_state="HTTP_STOPPED",
        ownership_passed=True,
        ptb_passed=True,
        structural_passed=True,
        intake_sealed=True,
        worker_stopped=True,
        worker_terminal=True,
        bot_shutdown_attempted=True,
        bot_shutdown_ok=True,
        bot_shutdown_error_type=None,
        bot_shutdown_error_text=None,
        request_close_results=(),
        http_stopped=True,
        loop_stop_requested=False,
        loop_running=True,
        loop_thread_alive=True,
        thread_joined=False,
        terminal_intake_failure_total=0,
        recent_intake_failures=(),
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_p8_only_after_successful_p7() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        proof = claim_antares_sender_ownership()
        calls: list[str] = []

        async def _stop(proof_arg, *, timeout):
            calls.append("p8")
            assert proof_arg is proof
            return _full_ok()

        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=AsyncMock(side_effect=_stop),
        ):
            # Fail before P7 via dead WE worker → P8 must not run.
            dead = threading.Thread(target=lambda: None, name="49c-pre-p7")
            dead.start()
            dead.join(timeout=5)
            worker_mod._profile_workers["DEAD"] = worker_mod._ProfileWorker(
                queue=worker_mod.Queue(), thread=dead
            )
            with pytest.raises(DrainOrchestrationError):
                await run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            assert calls == []

        await _shutdown_app(app)
        reset_antares_admission_for_tests()
        worker_mod._reset_we_stop_owner_for_tests()
        worker_mod._profile_workers = {}
        worker_mod._profile_workers_frozen = False
        worker_mod._profile_workers_stop_done = False
        _reset_antares_sender_ownership_for_tests()

        app2, _q2, host2, _s2, session2, _a2 = await _arm(loop)
        proof2 = claim_antares_sender_ownership()
        order: list[str] = []

        async def _tracking_stop(proof_arg, *, timeout):
            order.append("p8")
            assert worker_mod._profile_workers_stop_done is True
            return _full_ok()

        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=AsyncMock(side_effect=_tracking_stop),
        ):
            result = await run_owner_drain_p4_to_p8(
                session2, host2, sender_ownership_proof=proof2
            )
        assert order == ["p8"]
        assert result.last_completed_phase is DrainPhase.P8_SENDER
        assert result.sender_attempted is True
        assert result.sender_full_resource_stopped is True
        assert session2.terminal_result() is None
        await _shutdown_app(app2)

    asyncio.run(_main())


def test_missing_and_foreign_proof_refuse_without_sender_stop() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        claim_antares_sender_ownership()
        stop = AsyncMock(return_value=_full_ok())
        with patch("integrations.telegram_bot.stop_isolated_sender", new=stop):
            with pytest.raises(DrainOrchestrationError) as ei:
                await run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=object()
                )
            assert "ownership" in str(ei.value.remainder)
            stop.assert_not_awaited()

            foreign = AntaresSenderOwnershipAttestation()
            with pytest.raises(DrainOrchestrationError) as ei2:
                await run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=foreign
                )
            assert "ownership" in str(ei2.value.remainder)
            stop.assert_not_awaited()
        await _shutdown_app(app)

    asyncio.run(_main())


def test_p8_waiter_cancel_concurrent_repeat_single_owner() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        proof = claim_antares_sender_ownership()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def _slow(proof_arg, *, timeout):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return _full_ok()

        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=AsyncMock(side_effect=_slow),
        ):
            w1 = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            await started.wait()
            owner = getattr(session, _ATTR).owner_task
            w1.cancel()
            with pytest.raises(asyncio.CancelledError):
                await w1
            assert owner is not None and not owner.done()
            w2 = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            w3 = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            release.set()
            r2, r3 = await asyncio.gather(w2, w3)
            assert r2 == r3
            assert r2.last_completed_phase is DrainPhase.P8_SENDER
            assert calls == 1
            assert getattr(session, _ATTR).owner_task is owner
            # Cached identity check
            again = await run_owner_drain_p4_to_p8(
                session, host, sender_ownership_proof=proof
            )
            assert again.last_completed_phase is DrainPhase.P8_SENDER
            with pytest.raises(DrainOrchestrationError):
                await run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=object()
                )
        await _shutdown_app(app)

    asyncio.run(_main())


def test_deadline_before_p8_does_not_start_sender() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(50_000.0)
        clk.bind_loop()
        app, _q, host, _s, session, _a = await _arm(
            loop, clk, drain_timeout=2.0
        )
        proof = claim_antares_sender_ownership()
        stop = AsyncMock(return_value=_full_ok())
        # Hold registry so deadline can fire after P6, before P8.
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_reg(adm, *, producers_complete, timeout=None):
            started.set()
            await release.wait()
            return ()

        with patch(
            "modules.antares.shutdown_orchestration.wait_isolated_registry_daemon_ops",
            new=_slow_reg,
        ), patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=stop,
        ):
            d1 = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            await started.wait()
            clk.advance(3.0)
            with pytest.raises(DrainOrchestrationError) as ei:
                await d1
            assert "drain_deadline_passed" in str(ei.value.remainder)
            assert observe_drain_orchestration(session).p8_started is False
            release.set()
            result = await run_owner_drain_p4_to_p8(
                session, host, sender_ownership_proof=proof
            )
            assert result.last_completed_phase is DrainPhase.P7_REGISTRY
            assert result.sender_attempted is False
            stop.assert_not_awaited()
            assert session.terminal_result() is None
        await _shutdown_app(app)

    asyncio.run(_main())


def test_deadline_during_p8_publishes_partial_while_observing() -> None:
    """Deadline wave during started P8; owner keeps observing same stop."""

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(60_000.0)
        clk.bind_loop()
        app, _q, host, _s, session, _a = await _arm(
            loop, clk, drain_timeout=2.0
        )
        proof = claim_antares_sender_ownership()
        started = asyncio.Event()
        release = asyncio.Event()
        timeouts: list[float] = []

        async def _slow(proof_arg, *, timeout):
            timeouts.append(float(timeout))
            started.set()
            await release.wait()
            # Live partial: loop stop already requested, wait for late observe path.
            return _partial(
                reason="deadline_loop_stopped",
                lifecycle_state="LOOP_STOPPING",
                loop_stop_requested=True,
                loop_running=True,
                loop_thread_alive=True,
                http_stopped=True,
                worker_stopped=True,
            )

        observe_calls = 0

        async def _continue(proof_arg, *, timeout):
            nonlocal observe_calls
            observe_calls += 1
            if observe_calls == 1:
                # Still partial while held.
                return _partial(
                    reason="deadline_loop_stopped",
                    lifecycle_state="LOOP_STOPPING",
                    loop_stop_requested=True,
                    http_stopped=True,
                    worker_stopped=True,
                )
            return _full_ok(lifecycle_state="STOPPED")

        async def _dispatch(proof_arg, *, timeout):
            if not started.is_set() or not release.is_set():
                return await _slow(proof_arg, timeout=timeout)
            return await _continue(proof_arg, timeout=timeout)

        stop_mock = AsyncMock(side_effect=_dispatch)

        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=stop_mock,
        ), patch(
            # ControllableClock past session deadline → phase-aware observe path.
            "integrations.telegram_bot.observe_started_sender_stop",
            new=AsyncMock(side_effect=_dispatch),
        ):
            d1 = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            await started.wait()
            assert observe_drain_orchestration(session).sender_attempted is True
            assert observe_drain_orchestration(session).p8_started is True
            clk.advance(3.0)
            with pytest.raises(DrainOrchestrationError) as ei:
                await d1
            assert "p8_started" in str(ei.value.remainder)
            assert not release.is_set()
            # Owner still observing — no terminal cache yet.
            snap = observe_drain_orchestration(session)
            assert snap is not None
            assert snap.owner_alive is True
            assert snap.has_terminal_result is False
            release.set()
            # Allow observe loop to proceed past first continue partial.
            await asyncio.sleep(0.05)
            result = await run_owner_drain_p4_to_p8(
                session, host, sender_ownership_proof=proof
            )
            assert result.last_completed_phase is DrainPhase.P8_SENDER
            assert result.sender_full_resource_stopped is True
            assert len(timeouts) >= 1
            assert session.terminal_result() is None
        await _shutdown_app(app)

    asyncio.run(_main())


def test_partial_sender_boundary_not_false_full_success() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        proof = claim_antares_sender_ownership()
        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=AsyncMock(
                return_value=_partial(
                    reason="deadline_before_loop_stop",
                    lifecycle_state="HTTP_STOPPED",
                    loop_stop_requested=False,
                    http_stopped=True,
                    worker_stopped=True,
                )
            ),
        ):
            result = await run_owner_drain_p4_to_p8(
                session, host, sender_ownership_proof=proof
            )
        assert result.sender_attempted is True
        assert result.sender_ok is False
        assert result.sender_full_resource_stopped is False
        assert result.last_completed_phase is DrainPhase.P7_REGISTRY
        assert "sender_partial" in result.remainder
        assert "sender_boundary" in result.remainder
        assert result.sender_phase is not None
        assert result.sender_phase.http_stopped is True
        assert result.sender_phase.loop_stop_requested is False
        assert session.terminal_result() is None
        await _shutdown_app(app)

    asyncio.run(_main())


def test_p4_to_p7_unchanged_without_p8() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        stop = AsyncMock(return_value=_full_ok())
        with patch("integrations.telegram_bot.stop_isolated_sender", new=stop):
            result = await run_owner_drain_p4_to_p7(session, host)
        assert result.last_completed_phase is DrainPhase.P7_REGISTRY
        assert result.sender_attempted is False
        stop.assert_not_awaited()
        await _shutdown_app(app)

    asyncio.run(_main())


def test_p8_real_task48_partial_then_late_loop_stop(monkeypatch) -> None:
    """Real stop path: live LOOP_STOPPING partial, expire session, late STOPPED."""

    import integrations.telegram_bot as tg
    from tests.unit.test_telegram_sender_full_stop import (
        LoopStopHarness,
        _force_worker_stopped,
        _install_fake_bot,
        _ensure_worker_alive,
        without_killing_sender_loop,
    )

    _ensure_worker_alive()
    _install_fake_bot(monkeypatch)
    proof = claim_antares_sender_ownership()
    _force_worker_stopped(proof)

    harness = LoopStopHarness()
    harness.auto_signal_stopped = False

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(70_000.0)
        clk.bind_loop()
        app, _q, host, _s, session, _admission = await _arm(
            loop, clk, drain_timeout=5.0
        )
        deadline_before = session.shutdown_deadline
        reg_started = asyncio.Event()
        reg_release = asyncio.Event()

        async def _hold_reg(adm, *, producers_complete, timeout=None):
            reg_started.set()
            await reg_release.wait()
            return ()

        with without_killing_sender_loop(monkeypatch, harness), patch(
            "modules.antares.shutdown_orchestration.wait_isolated_registry_daemon_ops",
            new=_hold_reg,
        ):
            drain = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            await reg_started.wait()
            clk.advance(4.6)
            reg_release.set()

            attempted = False
            partial_seen = False
            for _ in range(500):
                snap = observe_drain_orchestration(session)
                if snap is not None and snap.sender_attempted:
                    attempted = True
                if (
                    snap is not None
                    and snap.sender_reason == "deadline_loop_stopped"
                    and snap.sender_phase is not None
                    and snap.sender_phase.lifecycle_state == "LOOP_STOPPING"
                    and snap.sender_phase.loop_stop_requested is True
                    and not snap.sender_phase.full_resource_stopped
                    and not snap.has_terminal_result
                    and snap.owner_alive
                ):
                    partial_seen = True
                    break
                await asyncio.sleep(0.02)
            assert attempted is True
            assert partial_seen is True
            assert tg._loop_stop_requested is True
            assert tg._lifecycle_state == "LOOP_STOPPING"
            assert not drain.done()
            owner = getattr(session, _ATTR).owner_task
            assert owner is not None and not owner.done()
            assert harness.stop_schedule_count == 1

            # Expire the shared session budget while the started loop-stop lives.
            clk.advance(1.0)
            snap = observe_drain_orchestration(session)
            assert snap is not None
            assert snap.may_start_new_destructive_phases is False
            assert snap.deadline_passed is True
            assert snap.owner_alive is True
            assert snap.has_terminal_result is False

            with pytest.raises(DrainOrchestrationError) as ei:
                await asyncio.wait_for(drain, timeout=5.0)
            assert "drain_deadline_passed" in str(ei.value.remainder)
            assert getattr(session, _ATTR).owner_task is owner
            assert not owner.done()

            harness._stop_faked = True
            harness.thread_alive_after_stop = False
            harness.loop_running_after_stop = False
            tg._loop_stopped.set()

            result = await asyncio.wait_for(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                ),
                timeout=5.0,
            )
            assert result.last_completed_phase is DrainPhase.P8_SENDER
            assert result.sender_ok is True
            assert result.sender_full_resource_stopped is True
            assert result.sender_phase is not None
            assert result.sender_phase.lifecycle_state == "STOPPED"
            assert result.sender_phase.loop_stop_requested is True
            assert getattr(session, _ATTR).owner_task is owner
            assert session.shutdown_deadline == deadline_before
            assert harness.stop_schedule_count == 1

            again = await asyncio.wait_for(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                ),
                timeout=2.0,
            )
            assert again.last_completed_phase is DrainPhase.P8_SENDER
            assert again.sender_full_resource_stopped is True
            assert harness.stop_schedule_count == 1
            assert session.shutdown_deadline == deadline_before
            assert session.terminal_result() is None

        await _shutdown_app(app)

    try:
        asyncio.run(_main())
    finally:
        _ensure_worker_alive()


def test_p8_real_task48_draining_late_worker_after_deadline(monkeypatch) -> None:
    """Already-enqueued sentinel: late WORKER_STOPPED observed without re-send."""

    import time as time_mod

    import integrations.telegram_bot as tg
    from tests.unit.test_telegram_sender_full_stop import (
        LoopStopHarness,
        _install_fake_bot,
        _ensure_worker_alive,
        without_killing_sender_loop,
    )

    _ensure_worker_alive()
    _install_fake_bot(monkeypatch)
    proof = claim_antares_sender_ownership()

    harness = LoopStopHarness()
    hold_terminal = asyncio.Event()
    entered_terminal = asyncio.Event()
    real_obs = tg._observe_worker_terminal_after_sentinel
    real_enqueue = tg._enqueue_sentinel_on_loop
    enqueue_calls = {"n": 0}

    def _counting_enqueue():
        enqueue_calls["n"] += 1
        return real_enqueue()

    async def _held_terminal(deadline, **kwargs):
        entered_terminal.set()
        remaining = max(0.01, float(deadline) - time_mod.monotonic())
        try:
            await asyncio.wait_for(hold_terminal.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            return tg._snapshot_drain_fields(
                ok=False,
                reason="deadline_worker_terminal",
                ownership_passed=True,
                ptb_passed=True,
                structural_passed=True,
                queue_drained=True,
                sentinel_submitted=True,
                worker_terminal=False,
            )
        return await real_obs(time_mod.monotonic() + 5.0, **kwargs)

    monkeypatch.setattr(tg, "_observe_worker_terminal_after_sentinel", _held_terminal)
    monkeypatch.setattr(tg, "_enqueue_sentinel_on_loop", _counting_enqueue)

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(80_000.0)
        clk.bind_loop()
        app, _q, host, _s, session, _admission = await _arm(
            loop, clk, drain_timeout=5.0
        )
        deadline_before = session.shutdown_deadline
        reg_started = asyncio.Event()
        reg_release = asyncio.Event()

        async def _hold_reg(adm, *, producers_complete, timeout=None):
            reg_started.set()
            await reg_release.wait()
            return ()

        with without_killing_sender_loop(monkeypatch, harness), patch(
            "modules.antares.shutdown_orchestration.wait_isolated_registry_daemon_ops",
            new=_hold_reg,
        ):
            drain = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            await reg_started.wait()
            clk.advance(4.7)
            reg_release.set()

            for _ in range(500):
                if entered_terminal.is_set():
                    break
                await asyncio.sleep(0.02)
            assert entered_terminal.is_set()
            assert enqueue_calls["n"] == 1
            assert tg._sentinel_state == tg._SENTINEL_ENQUEUED

            partial_seen = False
            for _ in range(500):
                snap = observe_drain_orchestration(session)
                if (
                    snap is not None
                    and snap.sender_attempted
                    and snap.sender_phase is not None
                    and snap.sender_phase.lifecycle_state == "DRAINING"
                    and snap.sender_phase.intake_sealed is True
                    and not snap.has_terminal_result
                    and snap.owner_alive
                ):
                    partial_seen = True
                    break
                await asyncio.sleep(0.02)
            assert partial_seen is True
            owner = getattr(session, _ATTR).owner_task
            assert owner is not None and not owner.done()

            clk.advance(1.0)
            snap = observe_drain_orchestration(session)
            assert snap is not None
            assert snap.may_start_new_destructive_phases is False

            with pytest.raises(DrainOrchestrationError):
                await asyncio.wait_for(drain, timeout=5.0)
            assert not owner.done()
            assert harness.stop_schedule_count == 0
            assert tg._loop_stop_requested is False
            assert enqueue_calls["n"] == 1

            hold_terminal.set()
            result = await asyncio.wait_for(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                ),
                timeout=5.0,
            )
            assert result.sender_attempted is True
            assert result.sender_full_resource_stopped is False
            assert result.last_completed_phase is DrainPhase.P7_REGISTRY
            assert "sender_boundary" in result.remainder
            assert result.sender_phase is not None
            assert result.sender_phase.lifecycle_state == "WORKER_STOPPED"
            assert result.sender_phase.worker_stopped is True
            assert result.sender_phase.http_stopped is False
            assert enqueue_calls["n"] == 1
            assert harness.stop_schedule_count == 0
            assert tg._loop_stop_requested is False
            assert session.shutdown_deadline == deadline_before
            assert session.terminal_result() is None

        await _shutdown_app(app)

    try:
        asyncio.run(_main())
    finally:
        hold_terminal.set()
        _ensure_worker_alive()


def test_p8_draining_deadline_before_sentinel_no_first_send(monkeypatch) -> None:
    """After deadline, accepted-work idle without sentinel → boundary, worker alive."""

    import integrations.telegram_bot as tg
    from tests.unit.test_telegram_sender_full_stop import (
        LoopStopHarness,
        _install_fake_bot,
        _ensure_worker_alive,
        without_killing_sender_loop,
    )

    _ensure_worker_alive()
    _install_fake_bot(monkeypatch)
    proof = claim_antares_sender_ownership()

    harness = LoopStopHarness()
    real_enqueue = tg._enqueue_sentinel_on_loop
    enqueue_calls = {"n": 0}

    def _counting_enqueue():
        enqueue_calls["n"] += 1
        return real_enqueue()

    monkeypatch.setattr(tg, "_enqueue_sentinel_on_loop", _counting_enqueue)

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(81_000.0)
        clk.bind_loop()
        app, _q, host, _s, session, _admission = await _arm(
            loop, clk, drain_timeout=5.0
        )
        reg_started = asyncio.Event()
        reg_release = asyncio.Event()

        async def _hold_reg(adm, *, producers_complete, timeout=None):
            reg_started.set()
            await reg_release.wait()
            return ()

        with without_killing_sender_loop(monkeypatch, harness), patch(
            "modules.antares.shutdown_orchestration.wait_isolated_registry_daemon_ops",
            new=_hold_reg,
        ):
            with tg._lifecycle_lock:
                tg._pending_loop_handoffs = 1
                tg._s1_idle.clear()

            drain = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            await reg_started.wait()
            clk.advance(4.7)
            reg_release.set()

            partial_seen = False
            for _ in range(500):
                snap = observe_drain_orchestration(session)
                if (
                    snap is not None
                    and snap.sender_attempted
                    and snap.sender_phase is not None
                    and snap.sender_reason == "deadline_s1_pending"
                    and snap.sender_phase.lifecycle_state == "DRAINING"
                    and not snap.has_terminal_result
                    and snap.owner_alive
                ):
                    partial_seen = True
                    break
                await asyncio.sleep(0.02)
            assert partial_seen is True
            assert enqueue_calls["n"] == 0
            assert tg._sentinel_state == tg._SENTINEL_NOT_SUBMITTED
            owner = getattr(session, _ATTR).owner_task
            assert owner is not None and not owner.done()

            clk.advance(1.0)
            assert observe_drain_orchestration(session).may_start_new_destructive_phases is False
            with pytest.raises(DrainOrchestrationError):
                await asyncio.wait_for(drain, timeout=5.0)
            assert not owner.done()

            with tg._lifecycle_lock:
                tg._pending_loop_handoffs = 0
                tg._s1_idle.set()

            result = await asyncio.wait_for(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                ),
                timeout=5.0,
            )
            assert result.last_completed_phase is DrainPhase.P7_REGISTRY
            assert "sender_boundary" in result.remainder
            assert result.sender_reason == "drain_boundary_sentinel_not_requested"
            assert result.sender_full_resource_stopped is False
            assert result.sender_phase is not None
            assert result.sender_phase.lifecycle_state == "DRAINING"
            assert result.sender_phase.worker_stopped is False
            assert result.sender_phase.http_stopped is False
            assert enqueue_calls["n"] == 0
            assert tg._sentinel_state == tg._SENTINEL_NOT_SUBMITTED
            assert tg._worker_task is not None and not tg._worker_task.done()
            assert harness.stop_schedule_count == 0
            assert tg._loop_stop_requested is False
            assert session.terminal_result() is None

        await _shutdown_app(app)

    try:
        asyncio.run(_main())
    finally:
        with tg._lifecycle_lock:
            tg._pending_loop_handoffs = 0
            tg._s1_idle.set()
        _ensure_worker_alive()


def test_p8_draining_unexpected_dead_worker_terminates(monkeypatch) -> None:
    """unexpected_dead_worker during DRAINING observe returns final remainder bounded."""

    import integrations.telegram_bot as tg
    from tests.unit.test_telegram_sender_full_stop import (
        LoopStopHarness,
        _install_fake_bot,
        _ensure_worker_alive,
        without_killing_sender_loop,
    )

    _ensure_worker_alive()
    _install_fake_bot(monkeypatch)
    proof = claim_antares_sender_ownership()

    harness = LoopStopHarness()
    real_enqueue = tg._enqueue_sentinel_on_loop
    enqueue_calls = {"n": 0}

    def _counting_enqueue():
        enqueue_calls["n"] += 1
        return real_enqueue()

    monkeypatch.setattr(tg, "_enqueue_sentinel_on_loop", _counting_enqueue)

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(82_000.0)
        clk.bind_loop()
        app, _q, host, _s, session, _admission = await _arm(
            loop, clk, drain_timeout=5.0
        )
        reg_started = asyncio.Event()
        reg_release = asyncio.Event()

        async def _hold_reg(adm, *, producers_complete, timeout=None):
            reg_started.set()
            await reg_release.wait()
            return ()

        with without_killing_sender_loop(monkeypatch, harness), patch(
            "modules.antares.shutdown_orchestration.wait_isolated_registry_daemon_ops",
            new=_hold_reg,
        ):
            with tg._lifecycle_lock:
                tg._pending_loop_handoffs = 1
                tg._s1_idle.clear()

            drain = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            await reg_started.wait()
            clk.advance(4.7)
            reg_release.set()

            for _ in range(500):
                snap = observe_drain_orchestration(session)
                if (
                    snap is not None
                    and snap.sender_reason == "deadline_s1_pending"
                    and snap.owner_alive
                    and not snap.has_terminal_result
                ):
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("missing deadline_s1_pending partial")

            clk.advance(1.0)
            with pytest.raises(DrainOrchestrationError):
                await asyncio.wait_for(drain, timeout=5.0)

            async def _dead_join():
                raise RuntimeError("unexpected_dead_worker")

            monkeypatch.setattr(tg, "_queue_join_watching_worker", _dead_join)
            with tg._lifecycle_lock:
                tg._pending_loop_handoffs = 0
                tg._s1_idle.set()

            result = await asyncio.wait_for(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                ),
                timeout=5.0,
            )
            assert result.last_completed_phase is DrainPhase.P7_REGISTRY
            assert "sender_refused" in result.remainder
            assert "unexpected_dead_worker" in result.remainder
            assert result.sender_full_resource_stopped is False
            assert result.sender_phase is not None
            assert result.sender_phase.reason == "unexpected_dead_worker"
            assert enqueue_calls["n"] == 0
            assert harness.stop_schedule_count == 0
            assert tg._loop_stop_requested is False
            assert session.terminal_result() is None

        await _shutdown_app(app)

    try:
        asyncio.run(_main())
    finally:
        with tg._lifecycle_lock:
            tg._pending_loop_handoffs = 0
            tg._s1_idle.set()
        _ensure_worker_alive()


def test_p8_real_task48_http_stopping_late_http_after_deadline(monkeypatch) -> None:
    """HTTP_STOPPING after deadline observes HTTP only — no loop.stop."""

    import time as time_mod

    import integrations.telegram_bot as tg
    from tests.unit.test_telegram_sender_full_stop import (
        LoopStopHarness,
        _force_worker_stopped,
        _install_fake_bot,
        _ensure_worker_alive,
        without_killing_sender_loop,
    )

    _ensure_worker_alive()
    bot, _gu, _gen = _install_fake_bot(monkeypatch)
    proof = claim_antares_sender_ownership()
    _force_worker_stopped(proof)

    hold = threading.Event()
    owner_started = threading.Event()
    harness = LoopStopHarness()
    real_await = tg._await_http_phase_terminal
    first_claimer_partial = {"done": False}

    async def _owner_until_released() -> bool:
        # Keep HTTP_STOPPING alive past the caller waiter partial.
        owner_started.set()
        while not hold.is_set():
            await asyncio.sleep(0.02)
        success = bool(
            await tg._http_close_on_sender_loop(time_mod.monotonic() + 30.0)
        )
        with tg._lifecycle_lock:
            tg._publish_http_phase_locked(success=success)
        return success

    async def _spy_await(deadline, *, cancel_owner_on_deadline):
        if cancel_owner_on_deadline and not first_claimer_partial["done"]:
            assert await asyncio.to_thread(owner_started.wait, 3.0)
            first_claimer_partial["done"] = True
            return tg._snapshot_full_stop_fields(
                ok=False,
                reason="deadline_http_close",
                ownership_passed=True,
                ptb_passed=True,
                structural_passed=True,
                worker_terminal=True,
            )
        return await real_await(
            deadline, cancel_owner_on_deadline=cancel_owner_on_deadline
        )

    monkeypatch.setattr(tg, "_owner_http_close_session", _owner_until_released)
    monkeypatch.setattr(tg, "_await_http_phase_terminal", _spy_await)

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(90_000.0)
        clk.bind_loop()
        app, _q, host, _s, session, _admission = await _arm(
            loop, clk, drain_timeout=5.0
        )
        deadline_before = session.shutdown_deadline
        reg_started = asyncio.Event()
        reg_release = asyncio.Event()

        async def _hold_reg(adm, *, producers_complete, timeout=None):
            reg_started.set()
            await reg_release.wait()
            return ()

        with without_killing_sender_loop(monkeypatch, harness), patch(
            "modules.antares.shutdown_orchestration.wait_isolated_registry_daemon_ops",
            new=_hold_reg,
        ):
            drain = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            await reg_started.wait()
            clk.advance(4.0)
            reg_release.set()

            partial_seen = False
            for _ in range(500):
                snap = observe_drain_orchestration(session)
                if (
                    snap is not None
                    and snap.sender_attempted
                    and snap.sender_phase is not None
                    and snap.sender_phase.lifecycle_state == "HTTP_STOPPING"
                    and snap.sender_reason == "deadline_http_close"
                    and not snap.has_terminal_result
                    and snap.owner_alive
                ):
                    partial_seen = True
                    break
                await asyncio.sleep(0.02)
            assert partial_seen is True
            owner = getattr(session, _ATTR).owner_task
            assert owner is not None and not owner.done()
            assert tg._lifecycle_state == "HTTP_STOPPING"

            clk.advance(2.0)
            snap = observe_drain_orchestration(session)
            assert snap is not None
            assert snap.may_start_new_destructive_phases is False

            with pytest.raises(DrainOrchestrationError):
                await asyncio.wait_for(drain, timeout=5.0)
            assert not owner.done()
            assert harness.stop_schedule_count == 0
            assert tg._loop_stop_requested is False

            hold.set()
            result = await asyncio.wait_for(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                ),
                timeout=5.0,
            )
            assert result.sender_attempted is True
            assert result.sender_full_resource_stopped is False
            assert result.last_completed_phase is DrainPhase.P7_REGISTRY
            assert "sender_boundary" in result.remainder
            assert result.sender_phase is not None
            assert result.sender_phase.lifecycle_state == "HTTP_STOPPED"
            assert result.sender_phase.http_stopped is True
            assert result.sender_phase.loop_stop_requested is False
            assert harness.stop_schedule_count == 0
            assert tg._loop_stop_requested is False
            assert session.shutdown_deadline == deadline_before
            assert session.terminal_result() is None

        await _shutdown_app(app)

    try:
        asyncio.run(_main())
    finally:
        hold.set()
        _ensure_worker_alive()
