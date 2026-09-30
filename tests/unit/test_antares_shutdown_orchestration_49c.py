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
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _partial(**kwargs):
    base = dict(
        ok=False,
        reason="deadline_before_loop_stop",
        full_resource_stopped=False,
        lifecycle_state="HTTP_STOPPED",
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


def test_deadline_during_p8_partial_then_late_completion() -> None:
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
            return _full_ok()

        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=AsyncMock(side_effect=_slow),
        ):
            d1 = asyncio.create_task(
                run_owner_drain_p4_to_p8(
                    session, host, sender_ownership_proof=proof
                )
            )
            await started.wait()
            assert observe_drain_orchestration(session).p8_started is True
            clk.advance(3.0)
            with pytest.raises(DrainOrchestrationError) as ei:
                await d1
            assert "p8_started" in str(ei.value.remainder)
            assert not release.is_set()
            release.set()
            result = await run_owner_drain_p4_to_p8(
                session, host, sender_ownership_proof=proof
            )
            assert result.last_completed_phase is DrainPhase.P8_SENDER
            assert result.sender_full_resource_stopped is True
            assert len(timeouts) == 1
            assert timeouts[0] > 0
            assert session.terminal_result() is None
        await _shutdown_app(app)

    asyncio.run(_main())


def test_partial_sender_outcome_not_false_full_success() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _q, host, _s, session, _a = await _arm(loop)
        proof = claim_antares_sender_ownership()
        with patch(
            "integrations.telegram_bot.stop_isolated_sender",
            new=AsyncMock(return_value=_partial()),
        ):
            result = await run_owner_drain_p4_to_p8(
                session, host, sender_ownership_proof=proof
            )
        assert result.sender_attempted is True
        assert result.sender_ok is False
        assert result.sender_full_resource_stopped is False
        assert result.last_completed_phase is DrainPhase.P7_REGISTRY
        assert "sender_partial" in result.remainder
        assert "deadline_before_loop_stop" in result.remainder
        assert session.terminal_result() is None
        snap = observe_drain_orchestration(session)
        assert snap is not None
        assert snap.sender_attempted is True
        assert snap.sender_full_resource_stopped is False
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
