"""TASK-49.D: P9 executor stop + EX1 gate regressions."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from telegram import User
from telegram.ext import Application

import automation.worker as worker_mod
import core.job_dispatch as job_dispatch
from core.antares_sender_ownership import (
    _reset_antares_sender_ownership_for_tests,
    claim_antares_sender_ownership,
)
from core.job_dispatch import (
    JobExecutorStopResult,
    _reset_job_executor_for_tests,
    get_job_executor,
    stop_isolated_job_executor,
)
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
    _evaluate_ex1,
    observe_drain_orchestration,
    run_owner_drain_p4_to_p8,
    run_owner_drain_p4_to_p9,
)
from modules.antares.shutdown_session import (
    ControllableClock,
    ShutdownSessionHost,
)
from modules.antares.work_admission import (
    AdmissionState,
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
    user = User(id=1, first_name="D", is_bot=True, username="bot")

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


def _ownership_refuse(**kwargs):
    return _partial(
        reason="ownership_foreign",
        ownership_passed=False,
        lifecycle_state="RUNNING",
        intake_sealed=False,
        worker_stopped=False,
        http_stopped=False,
        **kwargs,
    )


def test_ex1_full_path_stops_executor_threads() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        proof = claim_antares_sender_ownership()
        ex = get_job_executor()
        release = threading.Event()
        started = threading.Event()

        def _hold() -> None:
            started.set()
            release.wait(timeout=5)

        ex.submit(_hold)
        assert started.wait(timeout=2)

        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=AsyncMock(return_value=_full_ok()),
        ):
            drain_task = asyncio.create_task(
                run_owner_drain_p4_to_p9(
                    session, host, sender_ownership_proof=proof
                )
            )
            # Allow P8→P9 to begin observing threads, then release worker.
            for _ in range(50):
                snap = observe_drain_orchestration(session)
                if snap is not None and snap.p9_started:
                    break
                await asyncio.sleep(0.02)
            release.set()
            result = await drain_task

        assert result.sender_attempted is True
        assert result.sender_full_resource_stopped is True
        assert result.executor_attempted is True
        assert result.executor_ok is True
        assert result.executor_stopped is True
        assert result.p9_skipped is False
        assert result.last_completed_phase is DrainPhase.P9_EXECUTOR
        assert session.terminal_result() is None
        with pytest.raises(job_dispatch.JobExecutorStoppedError):
            get_job_executor()
        await _shutdown_app(app)

    asyncio.run(_main())


def test_partial_sender_allows_p9_under_ex1() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        proof = claim_antares_sender_ownership()
        get_job_executor()  # ensure present

        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=AsyncMock(return_value=_partial()),
        ):
            result = await run_owner_drain_p4_to_p9(
                session, host, sender_ownership_proof=proof
            )

        assert result.sender_ok is False
        assert result.sender_full_resource_stopped is False
        assert "sender_partial" in result.remainder
        assert result.p9_skipped is False
        assert result.executor_attempted is True
        assert result.executor_stopped is True
        # Sender remainder preserved independently of executor success.
        assert "sender_partial" in result.remainder
        assert result.sender_phase is not None
        assert session.terminal_result() is None
        await _shutdown_app(app)

    asyncio.run(_main())


def test_foreign_sender_ownership_skips_p9() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        proof = claim_antares_sender_ownership()
        get_job_executor()
        stop_exec = AsyncMock(
            return_value=JobExecutorStopResult(
                ok=True,
                reason=None,
                attempted=True,
                shutdown_called=True,
                stopping=False,
                stopped=True,
                executor_was_absent=False,
                live_thread_names=(),
                live_thread_count=0,
                error_type=None,
                error_text=None,
            )
        )

        with (
            patch(
                "integrations.telegram_bot.stop_isolated_sender",
                new=AsyncMock(return_value=_ownership_refuse()),
            ),
            patch(
                "modules.antares.shutdown_orchestration.stop_isolated_job_executor",
                new=stop_exec,
            ),
        ):
            result = await run_owner_drain_p4_to_p9(
                session, host, sender_ownership_proof=proof
            )

        assert result.sender_attempted is True
        assert result.p9_skipped is True
        assert "ex1_sender_ownership_refused" in result.remainder
        assert result.executor_attempted is False
        stop_exec.assert_not_awaited()
        # Executor must not have been production-stopped as a side effect.
        assert job_dispatch._SHUTDOWN_CALLED is False
        await _shutdown_app(app)

    asyncio.run(_main())


def test_each_missing_ex1_gate_refuses() -> None:
    """Direct EX1 evaluation: every missing gate produces a refusal reason."""

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, shut, session, admission = await _arm(loop)
        proof = claim_antares_sender_ownership()
        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=AsyncMock(return_value=_full_ok()),
        ):
            await run_owner_drain_p4_to_p8(
                session, host, sender_ownership_proof=proof
            )

        from modules.antares.shutdown_orchestration import (
            SenderPhaseSnapshot,
            _get_owner_state,
        )
        import integrations.wallet_editor_registry_async as registry_mod

        state = _get_owner_state(session)
        assert state is not None
        allow, reasons = _evaluate_ex1(state)
        assert allow is True
        assert reasons == ()

        def _restore() -> None:
            admission._state = AdmissionState.SEALED
            session._producers_attested = True  # noqa: SLF001
            worker_mod._profile_workers_stop_done = True
            if worker_mod._we_stop_result is None:
                worker_mod._we_stop_result = state.we_joined
            registry_mod._daemon_ops_wait_done = True
            registry_mod._daemon_ops_frozen = True
            if registry_mod._daemon_ops_joined != state.registry_joined:
                registry_mod._daemon_ops_joined = state.registry_joined
            state.sender_attempted = True
            if state.sender_phase is None and state.result is not None:
                state.sender_phase = state.result.sender_phase
            if state.sender_phase is not None and (
                not state.sender_phase.ownership_passed
            ):
                state.sender_phase = SenderPhaseSnapshot(
                    ok=True,
                    reason=None,
                    ownership_passed=True,
                    ptb_passed=True,
                    structural_passed=True,
                    lifecycle_state="STOPPED",
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
                    full_resource_stopped=True,
                    terminal_intake_failure_total=0,
                    recent_intake_failures=(),
                )
                state.sender_reason = None

        def _ownership_case() -> None:
            assert state.sender_phase is not None
            sp = state.sender_phase
            state.sender_phase = SenderPhaseSnapshot(
                ok=False,
                reason="ownership_foreign",
                ownership_passed=False,
                ptb_passed=sp.ptb_passed,
                structural_passed=sp.structural_passed,
                lifecycle_state=sp.lifecycle_state,
                intake_sealed=sp.intake_sealed,
                worker_stopped=sp.worker_stopped,
                worker_terminal=sp.worker_terminal,
                bot_shutdown_attempted=sp.bot_shutdown_attempted,
                bot_shutdown_ok=sp.bot_shutdown_ok,
                bot_shutdown_error_type=sp.bot_shutdown_error_type,
                bot_shutdown_error_text=sp.bot_shutdown_error_text,
                request_close_results=sp.request_close_results,
                http_stopped=sp.http_stopped,
                loop_stop_requested=sp.loop_stop_requested,
                loop_running=sp.loop_running,
                loop_thread_alive=sp.loop_thread_alive,
                thread_joined=sp.thread_joined,
                full_resource_stopped=False,
                terminal_intake_failure_total=sp.terminal_intake_failure_total,
                recent_intake_failures=sp.recent_intake_failures,
            )
            state.sender_reason = "ownership_foreign"

        gate_checks = [
            (
                "ex1_admission_not_sealed",
                lambda: setattr(admission, "_state", AdmissionState.OPEN),
            ),
            (
                "ex1_producers_incomplete",
                lambda: setattr(session, "_producers_attested", False),
            ),
            (
                "ex1_we_not_stop_done",
                lambda: setattr(worker_mod, "_profile_workers_stop_done", False),
            ),
            (
                "ex1_registry_not_wait_done",
                lambda: setattr(registry_mod, "_daemon_ops_wait_done", False),
            ),
            (
                "ex1_sender_not_attempted",
                lambda: setattr(state, "sender_attempted", False),
            ),
            (
                "ex1_sender_phase_missing",
                lambda: setattr(state, "sender_phase", None),
            ),
            ("ex1_sender_ownership_refused", _ownership_case),
        ]

        for token, mutate in gate_checks:
            _restore()
            allow0, _ = _evaluate_ex1(state)
            assert allow0 is True, token
            mutate()
            allow1, reasons1 = _evaluate_ex1(state)
            assert allow1 is False, token
            assert token in reasons1, (token, reasons1)

        _restore()
        fut = asyncio.get_running_loop().create_future()
        admission._accepted_executor_futures.add(fut)
        allow_f, reasons_f = _evaluate_ex1(state)
        assert allow_f is False
        assert "ex1_accepted_futures_remain" in reasons_f
        admission._accepted_executor_futures.discard(fut)
        fut.cancel()

        await _shutdown_app(app)

    asyncio.run(_main())


def test_missing_ex1_gate_no_executor_side_effects() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, admission = await _arm(loop)
        proof = claim_antares_sender_ownership()
        get_job_executor()
        stop_exec = AsyncMock()

        async def _sender(proof_arg, *, timeout):
            # Break WE identity after P8 would have been allowed — force skip.
            worker_mod._profile_workers_stop_done = False
            return _full_ok()

        with (
            patch(
                "integrations.telegram_bot.stop_isolated_sender",
                new=AsyncMock(side_effect=_sender),
            ),
            patch(
                "modules.antares.shutdown_orchestration.stop_isolated_job_executor",
                new=stop_exec,
            ),
        ):
            result = await run_owner_drain_p4_to_p9(
                session, host, sender_ownership_proof=proof
            )

        assert result.p9_skipped is True
        assert "ex1_we_not_stop_done" in result.remainder
        stop_exec.assert_not_awaited()
        assert job_dispatch._SHUTDOWN_CALLED is False
        await _shutdown_app(app)

    asyncio.run(_main())


def test_deadline_before_p9_does_not_start_executor_shutdown() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(50_000.0)
        clk.bind_loop()
        app, _q, host, shut, session, _a = await _arm(
            loop, clk, drain_timeout=5.0
        )
        proof = claim_antares_sender_ownership()
        get_job_executor()
        stop_exec = AsyncMock()

        reg_started = asyncio.Event()
        reg_release = asyncio.Event()

        async def _hold_reg(adm, *, producers_complete, timeout=None):
            reg_started.set()
            await reg_release.wait()
            import integrations.wallet_editor_registry_async as registry_mod

            registry_mod._daemon_ops_frozen = True
            registry_mod._daemon_ops_wait_done = True
            registry_mod._daemon_ops_joined = ()
            return ()

        with (
            patch(
                "modules.antares.shutdown_orchestration.wait_isolated_registry_daemon_ops",
                new=AsyncMock(side_effect=_hold_reg),
            ),
            patch(
                "integrations.telegram_bot.stop_isolated_sender",
                new=AsyncMock(return_value=_full_ok()),
            ),
            patch(
                "modules.antares.shutdown_orchestration.stop_isolated_job_executor",
                new=stop_exec,
            ),
        ):
            drain_task = asyncio.create_task(
                run_owner_drain_p4_to_p9(
                    session, host, sender_ownership_proof=proof
                )
            )
            await reg_started.wait()
            # Expire budget during P7 so P8/P9 cannot start new destructive work.
            # Advance past drain_timeout; then release registry to complete P7.
            clk.advance(10.0)
            reg_release.set()
            # Owner may soft-stop before P8 if deadline blocks after P7.
            result = await drain_task

        # Either P8 never started, or P9 skipped for deadline — executor must
        # not have been invoked.
        stop_exec.assert_not_awaited()
        assert job_dispatch._SHUTDOWN_CALLED is False
        assert result.executor_attempted is False
        await _shutdown_app(app)

    asyncio.run(_main())


def test_deadline_during_p9_partial_same_owner_rejoin() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(60_000.0)
        clk.bind_loop()
        app, _q, host, shut, session, _a = await _arm(
            loop, clk, drain_timeout=30.0
        )
        proof = claim_antares_sender_ownership()
        ex = get_job_executor()
        release = threading.Event()
        started = threading.Event()

        def _hold() -> None:
            started.set()
            release.wait(timeout=10)

        ex.submit(_hold)
        assert started.wait(timeout=2)

        p9_entered = asyncio.Event()
        real_stop = stop_isolated_job_executor

        async def _gated_stop(*, timeout=30.0):
            p9_entered.set()
            # Shrink remaining budget to force thread-observe partial.
            return await real_stop(timeout=0.05)

        with (
            patch(
                "integrations.telegram_bot.stop_isolated_sender",
                new=AsyncMock(return_value=_full_ok()),
            ),
            patch(
                "modules.antares.shutdown_orchestration.stop_isolated_job_executor",
                new=_gated_stop,
            ),
        ):
            first = await run_owner_drain_p4_to_p9(
                session, host, sender_ownership_proof=proof
            )

        assert await asyncio.wait_for(p9_entered.wait(), timeout=5)
        assert first.executor_attempted is True
        assert first.executor_stopped is False
        assert first.p9_skipped is False
        assert job_dispatch._SHUTDOWN_CALLED is True

        release.set()
        # Same session owner already terminal with partial — rejoin via API
        # observes the same permanent executor stop without a new drain owner.
        final_exec = await stop_isolated_job_executor(timeout=5.0)
        assert final_exec.stopped is True

        # Repeat drain join returns the same cached owner result (one procedure).
        again = await run_owner_drain_p4_to_p9(
            session, host, sender_ownership_proof=proof
        )
        assert again.executor_attempted is True
        assert again is first or again.executor_phase == first.executor_phase
        await _shutdown_app(app)

    asyncio.run(_main())


def test_concurrent_repeat_single_p9_owner() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        proof = claim_antares_sender_ownership()
        get_job_executor()
        calls = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_stop(*, timeout=30.0):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return await stop_isolated_job_executor(timeout=timeout)

        with (
            patch(
                "integrations.telegram_bot.stop_isolated_sender",
                new=AsyncMock(return_value=_full_ok()),
            ),
            patch(
                "modules.antares.shutdown_orchestration.stop_isolated_job_executor",
                new=_slow_stop,
            ),
        ):
            t1 = asyncio.create_task(
                run_owner_drain_p4_to_p9(
                    session, host, sender_ownership_proof=proof
                )
            )
            await started.wait()
            t2 = asyncio.create_task(
                run_owner_drain_p4_to_p9(
                    session, host, sender_ownership_proof=proof
                )
            )
            # Cancel second waiter — must detach only.
            t2.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t2
            release.set()
            r1 = await t1
            r3 = await run_owner_drain_p4_to_p9(
                session, host, sender_ownership_proof=proof
            )

        assert calls == 1
        assert r1.executor_stopped is True
        assert r3.executor_stopped is True
        await _shutdown_app(app)

    asyncio.run(_main())


def test_absent_executor_not_created_on_p9() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        proof = claim_antares_sender_ownership()
        assert job_dispatch._JOB_EXECUTOR is None

        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=AsyncMock(return_value=_full_ok()),
        ):
            result = await run_owner_drain_p4_to_p9(
                session, host, sender_ownership_proof=proof
            )

        assert result.executor_attempted is True
        assert result.executor_stopped is True
        assert result.executor_phase is not None
        assert result.executor_phase.executor_was_absent is True
        assert job_dispatch._JOB_EXECUTOR is None
        with pytest.raises(job_dispatch.JobExecutorStoppedError):
            get_job_executor()
        await _shutdown_app(app)

    asyncio.run(_main())


def test_p8_only_api_does_not_run_p9() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        proof = claim_antares_sender_ownership()
        get_job_executor()
        stop_exec = AsyncMock()
        with (
            patch(
                "integrations.telegram_bot.stop_isolated_sender",
                new=AsyncMock(return_value=_full_ok()),
            ),
            patch(
                "modules.antares.shutdown_orchestration.stop_isolated_job_executor",
                new=stop_exec,
            ),
        ):
            result = await run_owner_drain_p4_to_p8(
                session, host, sender_ownership_proof=proof
            )
        assert result.last_completed_phase is DrainPhase.P8_SENDER
        assert result.executor_attempted is False
        stop_exec.assert_not_awaited()
        assert job_dispatch._SHUTDOWN_CALLED is False
        await _shutdown_app(app)

    asyncio.run(_main())


def test_include_mode_mismatch_between_p8_and_p9() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        proof = claim_antares_sender_ownership()
        hold = asyncio.Event()
        started = asyncio.Event()

        async def _slow(proof_arg, *, timeout):
            started.set()
            await hold.wait()
            return _full_ok()

        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=AsyncMock(side_effect=_slow),
        ):
            t8 = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            await started.wait()
            with pytest.raises(DrainOrchestrationError) as ei:
                await run_owner_drain_p4_to_p9(
                    session, host, sender_ownership_proof=proof
                )
            assert "include_p9_mismatch" in ei.value.remainder
            hold.set()
            await t8
        await _shutdown_app(app)

    asyncio.run(_main())
