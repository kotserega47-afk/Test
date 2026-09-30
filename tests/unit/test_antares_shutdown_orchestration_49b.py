"""TASK-49.B: P4–P7 drain orchestration + WE owner-session regressions."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from telegram import User
from telegram.ext import Application, TypeHandler

import automation.worker as worker_mod
from automation.worker import IsolatedProfileWorkerStopError
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
    run_owner_drain_p4_to_p7,
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


@contextmanager
def _patch_telegram_http():
    user = User(id=1, first_name="B", is_bot=True, username="bot")

    async def fake_do_request(self, *args, **kwargs):
        return 200, json.dumps({"ok": True, "result": user.to_dict()}).encode()

    with patch(
        "telegram.request._httpxrequest.HTTPXRequest.do_request",
        new=fake_do_request,
    ):
        yield


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


async def _arm_session_with_host(loop, clk=None):
    app, queue = _build_app()
    admission = WorkAdmission()
    bind_antares_admission(admission)
    admission.open()
    stop = asyncio.Event()
    kwargs = {"admission": admission, "stop": stop, "loop": loop, "drain_timeout": 30.0}
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
    return app, queue, host, shut, session, admission


def test_live_ptb_producer_blocks_p5_p6_p7() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(10_000.0)
        clk.bind_loop()
        app, queue, host, _shut, session, admission = await _arm_session_with_host(
            loop, clk
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(_u, _c) -> None:
            entered.set()
            await release.wait()

        app.add_handler(TypeHandler(object, handler, block=True))
        await queue.put(object())
        await entered.wait()
        host.seal_intake()
        drain = asyncio.create_task(
            run_owner_drain_p4_to_p7(session, host, producers_deadline=10_001.0)
        )
        await asyncio.sleep(0)
        clk.advance(2.0)
        with pytest.raises(DrainOrchestrationError, match="producers incomplete"):
            await drain
        assert admission.accepted_executor_futures() == ()
        release.set()
        await _shutdown_app(app)

    asyncio.run(_main())


def test_happy_path_p4_to_p7_idle() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _queue, host, _shut, session, _admission = await _arm_session_with_host(
            loop
        )
        host.seal_intake()
        result = await run_owner_drain_p4_to_p7(session, host, producers_deadline=None)
        assert result.producers_attested is True
        assert result.p5_complete is True
        assert result.last_completed_phase is DrainPhase.P7_REGISTRY
        assert session.snapshot().producers_complete_attested is True
        # Partial boundary: not SESSION_TERMINAL from P7 alone.
        assert session.terminal_result() is None
        await _shutdown_app(app)

    asyncio.run(_main())


def test_we_unfinished_blocks_p6() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _queue, host, _shut, session, admission = await _arm_session_with_host(
            loop
        )
        host.seal_intake()
        out = await host.wait_and_accept(session, deadline=None)
        assert out.attestation is not None
        # P5 complete (no Accepted/continuation); unfinished item blocks P6 stop.
        q = worker_mod.ensure_profile_queue("DENIS")
        q.put(object())
        assert q.unfinished_tasks >= 1
        with pytest.raises(IsolatedProfileWorkerStopError) as ei:
            await worker_mod.stop_isolated_profile_workers(
                admission, producers_complete=True
            )
        assert "unfinished" in ei.value.remainder.reason
        try:
            q.get_nowait()
            q.task_done()
        except Exception:
            pass
        await _shutdown_app(app)

    asyncio.run(_main())


def test_we_waiter_cancel_repeat_same_session_no_second_sentinel() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        admission = WorkAdmission()
        bind_antares_admission(admission)
        admission.open()
        admission.seal()
        q = worker_mod.ensure_profile_queue("DENIS")
        worker = worker_mod._profile_workers["DENIS"]
        puts: list[object] = []
        orig_put = q.put

        def _put(item):
            puts.append(item)
            return orig_put(item)

        q.put = _put  # type: ignore[method-assign]
        first = asyncio.create_task(
            worker_mod.stop_isolated_profile_workers(
                admission, producers_complete=True
            )
        )
        await asyncio.sleep(0)
        # Wait until sentinel placed, then cancel waiter while owner still joining.
        for _ in range(200):
            if worker.sentinel_put:
                break
            await asyncio.sleep(0)
        assert worker.sentinel_put
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        # Worker exits after sentinel; owner continues.
        for _ in range(200):
            if worker.thread is not None and not worker.thread.is_alive():
                break
            await asyncio.sleep(0.01)
        again = await worker_mod.stop_isolated_profile_workers(
            admission, producers_complete=True
        )
        assert again == ("DENIS",)
        sentinels = sum(
            1 for item in puts if isinstance(item, worker_mod.ProfileWorkerStopSentinel)
        )
        assert sentinels == 1

    asyncio.run(_main())


def test_dead_worker_before_stop_is_failure_not_success() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        bind_antares_admission(admission)
        admission.open()
        admission.seal()
        dead = threading.Thread(target=lambda: None, name="49b-dead")
        dead.start()
        dead.join(timeout=5)
        worker_mod._profile_workers["DEAD"] = worker_mod._ProfileWorker(
            queue=worker_mod.Queue(), thread=dead
        )
        with pytest.raises(IsolatedProfileWorkerStopError) as ei:
            await worker_mod.stop_isolated_profile_workers(
                admission, producers_complete=True
            )
        assert "dead" in ei.value.remainder.reason
        assert worker_mod._profile_workers_frozen is False

    asyncio.run(_main())


def test_accepted_future_survives_seal_and_waiter_cancel() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, queue = _build_app()
        admission = WorkAdmission()
        bind_antares_admission(admission)
        admission.open()
        host = PtbProducerWaitHost(application=app, loop=loop)
        await _start_app(app)
        hold = threading.Event()
        release = threading.Event()

        def body():
            hold.set()
            release.wait(timeout=5)
            return "ok"

        ex = ThreadPoolExecutor(max_workers=1)
        try:
            accepted = admission.submit_if_open(ex, body)
            fut = accepted.future
            for _ in range(200):
                if hold.is_set():
                    break
                await asyncio.sleep(0)
            assert hold.is_set()
            stop = asyncio.Event()
            shut = ShutdownSessionHost(
                admission=admission, stop=stop, loop=loop, drain_timeout=30.0
            )
            attach_producer_wait_to_shutdown_host(shut, host)
            session = shut.arm_request_stop(had_open=True)
            await session.wait_arm_effects()
            host.seal_intake()
            waiter = asyncio.create_task(
                run_owner_drain_p4_to_p7(session, host, producers_deadline=None)
            )
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not fut.done()
            release.set()
            assert fut.result(timeout=5) == "ok"
            result = await run_owner_drain_p4_to_p7(
                session, host, producers_deadline=None
            )
            assert result.last_completed_phase is DrainPhase.P7_REGISTRY
        finally:
            ex.shutdown(wait=False, cancel_futures=False)
            await _shutdown_app(app)

    asyncio.run(_main())


def test_concurrent_waiters_single_we_owner() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        bind_antares_admission(admission)
        admission.open()
        admission.seal()
        worker_mod.ensure_profile_queue("DENIS")
        w1 = asyncio.create_task(
            worker_mod.stop_isolated_profile_workers(
                admission, producers_complete=True
            )
        )
        w2 = asyncio.create_task(
            worker_mod.stop_isolated_profile_workers(
                admission, producers_complete=True
            )
        )
        r1, r2 = await asyncio.gather(w1, w2)
        assert r1 == r2 == ("DENIS",)
        assert worker_mod._we_stop_owner_task is not None

    asyncio.run(_main())
