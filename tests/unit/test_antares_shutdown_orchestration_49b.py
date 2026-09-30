"""TASK-49.B: P4–P7 drain orchestration + WE owner-session regressions."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
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
    wait_isolated_registry_daemon_ops,
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
    _DrainOwnerState,
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


async def _arm_session_with_host(loop, clk=None, *, drain_timeout: float = 30.0):
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
    return app, queue, host, shut, session, admission


def test_live_ptb_producer_blocks_p5_p6_p7() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(10_000.0)
        clk.bind_loop()
        app, queue, host, _shut, session, admission = await _arm_session_with_host(
            loop, clk, drain_timeout=5.0
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
        drain = asyncio.create_task(run_owner_drain_p4_to_p7(session, host))
        await asyncio.sleep(0)
        # Align with session.shutdown_deadline only (no separate producers budget).
        clk.advance(6.0)
        with pytest.raises(DrainOrchestrationError) as ei:
            await drain
        assert "deadline" in str(ei.value).lower() or "incomplete" in str(
            ei.value.remainder
        ).lower()
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
        result = await run_owner_drain_p4_to_p7(session, host)
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
        for _ in range(200):
            if worker.sentinel_put:
                break
            await asyncio.sleep(0)
        assert worker.sentinel_put
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
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


def test_we_join_while_sentinel_unfinished_after_waiter_cancel() -> None:
    """Hold worker before sentinel processing; cancel first; join second with unfinished>0."""

    async def _main() -> None:
        admission = WorkAdmission()
        bind_antares_admission(admission)
        admission.open()
        admission.seal()
        q = worker_mod.ensure_profile_queue("DENIS")
        worker = worker_mod._profile_workers["DENIS"]
        held = threading.Event()
        release = threading.Event()
        puts: list[object] = []
        orig_put = q.put
        orig_get = q.get

        def _put(item):
            puts.append(item)
            return orig_put(item)

        def _get(*args, **kwargs):
            item = orig_get(*args, **kwargs)
            if isinstance(item, worker_mod.ProfileWorkerStopSentinel):
                held.set()
                release.wait(timeout=10)
            return item

        q.put = _put  # type: ignore[method-assign]
        q.get = _get  # type: ignore[method-assign]

        # Recycle the worker onto the patched get (in-flight native get bypasses wrap).
        q.put(object())
        for _ in range(400):
            if q.unfinished_tasks == 0 and q.empty():
                break
            await asyncio.sleep(0.01)
        assert q.unfinished_tasks == 0

        first = asyncio.create_task(
            worker_mod.stop_isolated_profile_workers(
                admission, producers_complete=True
            )
        )
        for _ in range(400):
            if held.is_set():
                break
            await asyncio.sleep(0.01)
        assert held.is_set()
        assert worker.sentinel_put
        assert q.unfinished_tasks >= 1
        owner = worker_mod._we_stop_owner_task
        assert owner is not None and not owner.done()

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(
            worker_mod.stop_isolated_profile_workers(
                admission, producers_complete=True
            )
        )
        await asyncio.sleep(0)
        assert worker_mod._we_stop_owner_task is owner
        assert q.unfinished_tasks >= 1
        release.set()
        result = await second
        assert result == ("DENIS",)
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
            waiter = asyncio.create_task(run_owner_drain_p4_to_p7(session, host))
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not fut.done()
            state = getattr(session, _ATTR)
            assert isinstance(state, _DrainOwnerState)
            assert state.owner_task is not None and not state.owner_task.done()
            release.set()
            assert fut.result(timeout=5) == "ok"
            result = await run_owner_drain_p4_to_p7(session, host)
            assert result.last_completed_phase is DrainPhase.P7_REGISTRY
            assert session.terminal_result() is None
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


def test_orchestration_waiter_cancel_p5_owner_continues_no_second_call() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _queue = _build_app()
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
            return "p5"

        ex = ThreadPoolExecutor(max_workers=1)
        try:
            fut = admission.submit_if_open(ex, body).future
            for _ in range(200):
                if hold.is_set():
                    break
                await asyncio.sleep(0)
            stop = asyncio.Event()
            shut = ShutdownSessionHost(
                admission=admission, stop=stop, loop=loop, drain_timeout=30.0
            )
            attach_producer_wait_to_shutdown_host(shut, host)
            session = shut.arm_request_stop(had_open=True)
            await session.wait_arm_effects()
            host.seal_intake()
            w1 = asyncio.create_task(run_owner_drain_p4_to_p7(session, host))
            await asyncio.sleep(0)
            state = getattr(session, _ATTR)
            owner = state.owner_task
            assert owner is not None
            w1.cancel()
            with pytest.raises(asyncio.CancelledError):
                await w1
            assert not owner.done()
            release.set()
            assert fut.result(timeout=5) == "p5"
            result = await run_owner_drain_p4_to_p7(session, host)
            assert result.last_completed_phase is DrainPhase.P7_REGISTRY
            assert getattr(session, _ATTR).owner_task is owner
        finally:
            ex.shutdown(wait=False, cancel_futures=False)
            await _shutdown_app(app)

    asyncio.run(_main())


def test_two_orchestration_waiters_single_phase_sequence() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _queue, host, _shut, session, _admission = await _arm_session_with_host(
            loop
        )
        host.seal_intake()
        w1 = asyncio.create_task(run_owner_drain_p4_to_p7(session, host))
        w2 = asyncio.create_task(run_owner_drain_p4_to_p7(session, host))
        r1, r2 = await asyncio.gather(w1, w2)
        assert r1 == r2
        assert r1.last_completed_phase is DrainPhase.P7_REGISTRY
        state = getattr(session, _ATTR)
        assert state.owner_task is not None
        assert session.terminal_result() is None
        await _shutdown_app(app)

    asyncio.run(_main())


def test_deadline_during_incomplete_p5_and_started_p6() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(20_000.0)
        clk.bind_loop()
        app, _queue, host, _shut, session, admission = await _arm_session_with_host(
            loop, clk, drain_timeout=2.0
        )
        host.seal_intake()
        # Attest P4 first so owner can enter P5, then hold WE unfinished.
        out = await host.wait_and_accept(session, deadline=None)
        assert out.attestation is not None
        q = worker_mod.ensure_profile_queue("DENIS")
        q.put(object())
        assert q.unfinished_tasks >= 1

        drain = asyncio.create_task(run_owner_drain_p4_to_p7(session, host))
        await asyncio.sleep(0)
        clk.advance(3.0)
        with pytest.raises(DrainOrchestrationError) as ei:
            await drain
        assert "deadline" in str(ei.value).lower() or "drain_deadline_passed" in str(
            ei.value.remainder
        )
        # Accepted/continuation not cancelled by deadline publish; unfinished remains.
        assert q.unfinished_tasks >= 1
        assert session.terminal_result() is None

        # Started P6 observe path: clear unfinished, allow P6, expire before P7.
        try:
            q.get_nowait()
            q.task_done()
        except Exception:
            pass

        clk2 = ControllableClock(30_000.0)
        clk2.bind_loop()
        await _shutdown_app(app)
        reset_antares_admission_for_tests()
        worker_mod._reset_we_stop_owner_for_tests()
        worker_mod._profile_workers = {}
        worker_mod._profile_workers_frozen = False
        worker_mod._profile_workers_stop_done = False

        app2, _q2, host2, _s2, session2, admission2 = await _arm_session_with_host(
            loop, clk2, drain_timeout=2.0
        )
        host2.seal_intake()
        # Patch WE stop to hold after "start" so deadline can fire mid-P6.
        started = asyncio.Event()
        release_we = asyncio.Event()

        async def _slow_we(adm, *, producers_complete, timeout=None):
            started.set()
            await release_we.wait()
            return await worker_mod.stop_isolated_profile_workers(
                adm, producers_complete=producers_complete, timeout=timeout
            )

        with patch(
            "modules.antares.shutdown_orchestration.stop_isolated_profile_workers",
            new=_slow_we,
        ):
            d2 = asyncio.create_task(run_owner_drain_p4_to_p7(session2, host2))
            await started.wait()
            state = getattr(session2, _ATTR)
            assert state.p6_started is True
            clk2.advance(3.0)
            # Waiter keeps observing started P6; finish WE then owner hits P7 gate.
            worker_mod.ensure_profile_queue("DENIS")
            release_we.set()
            with pytest.raises(DrainOrchestrationError) as ei2:
                await d2
            rem = ei2.value.remainder
            assert rem is not None
            assert "drain_deadline_passed" in rem or "we_stop_complete" in rem
            assert session2.terminal_result() is None
        await _shutdown_app(app2)

    asyncio.run(_main())


def test_late_continuation_and_we_before_freeze_counted() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _queue, host, _shut, session, admission = await _arm_session_with_host(
            loop
        )
        host.seal_intake()
        await host.wait_and_accept(session, deadline=None)

        # Inject unfinished WE work after P4; P5 must observe it before P6 freeze.
        q = worker_mod.ensure_profile_queue("DENIS")
        q.put(object())
        assert worker_mod._profile_workers_frozen is False

        drain = asyncio.create_task(run_owner_drain_p4_to_p7(session, host))
        await asyncio.sleep(0.05)
        assert worker_mod._profile_workers_frozen is False
        assert not drain.done()
        try:
            q.get_nowait()
            q.task_done()
        except Exception:
            pass
        result = await drain
        assert result.last_completed_phase is DrainPhase.P7_REGISTRY
        await _shutdown_app(app)

    asyncio.run(_main())


def test_registry_not_frozen_before_successful_we_join() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _queue, host, _shut, session, admission = await _arm_session_with_host(
            loop
        )
        host.seal_intake()
        registry_calls: list[str] = []

        async def _tracking_registry(adm, *, producers_complete, timeout=None):
            registry_calls.append("called")
            assert worker_mod._profile_workers_stop_done is True
            return await wait_isolated_registry_daemon_ops(
                adm, producers_complete=producers_complete, timeout=timeout
            )

        dead = threading.Thread(target=lambda: None, name="49b-reg-order")
        dead.start()
        dead.join(timeout=5)
        worker_mod._profile_workers["DEAD"] = worker_mod._ProfileWorker(
            queue=worker_mod.Queue(), thread=dead
        )
        with patch(
            "modules.antares.shutdown_orchestration.wait_isolated_registry_daemon_ops",
            new=_tracking_registry,
        ):
            with pytest.raises(DrainOrchestrationError):
                await run_owner_drain_p4_to_p7(session, host)
            assert registry_calls == []
            assert worker_mod._profile_workers_frozen is False

        await _shutdown_app(app)
        reset_antares_admission_for_tests()
        worker_mod._reset_we_stop_owner_for_tests()
        worker_mod._profile_workers = {}
        worker_mod._profile_workers_frozen = False
        worker_mod._profile_workers_stop_done = False

        app2, _q2, host2, _s2, session2, _a2 = await _arm_session_with_host(loop)
        host2.seal_intake()
        registry_calls.clear()
        with patch(
            "modules.antares.shutdown_orchestration.wait_isolated_registry_daemon_ops",
            new=_tracking_registry,
        ):
            result = await run_owner_drain_p4_to_p7(session2, host2)
        assert registry_calls == ["called"]
        assert result.last_completed_phase is DrainPhase.P7_REGISTRY
        await _shutdown_app(app2)

    asyncio.run(_main())


def test_we_owner_error_retrieved_after_all_waiters_detach() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        bind_antares_admission(admission)
        admission.open()
        admission.seal()
        dead = threading.Thread(target=lambda: None, name="49b-dead2")
        dead.start()
        dead.join(timeout=5)
        worker_mod._profile_workers["DEAD"] = worker_mod._ProfileWorker(
            queue=worker_mod.Queue(), thread=dead
        )
        first = asyncio.create_task(
            worker_mod.stop_isolated_profile_workers(
                admission, producers_complete=True
            )
        )
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        # Allow owner to finish with error while no waiters attached.
        for _ in range(50):
            task = worker_mod._we_stop_owner_task
            if task is not None and task.done():
                break
            await asyncio.sleep(0.01)
        with pytest.raises(IsolatedProfileWorkerStopError) as ei:
            await worker_mod.stop_isolated_profile_workers(
                admission, producers_complete=True
            )
        assert "dead" in ei.value.remainder.reason

    asyncio.run(_main())


def test_orchestration_owner_error_after_all_waiters_detach() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        app, _queue, host, _shut, session, admission = await _arm_session_with_host(
            loop
        )
        host.seal_intake()
        dead = threading.Thread(target=lambda: None, name="49b-orch-dead")
        dead.start()
        dead.join(timeout=5)
        worker_mod._profile_workers["DEAD"] = worker_mod._ProfileWorker(
            queue=worker_mod.Queue(), thread=dead
        )
        w1 = asyncio.create_task(run_owner_drain_p4_to_p7(session, host))
        await asyncio.sleep(0)
        state = getattr(session, _ATTR)
        owner = state.owner_task
        assert owner is not None
        w1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await w1
        for _ in range(100):
            if owner.done():
                break
            await asyncio.sleep(0.01)
        assert owner.done()
        with pytest.raises(DrainOrchestrationError) as ei:
            await run_owner_drain_p4_to_p7(session, host)
        assert "P6" in str(ei.value) or "dead" in str(ei.value).lower()
        # No second orchestration for this session.
        assert getattr(session, _ATTR).owner_task is owner
        await _shutdown_app(app)

    asyncio.run(_main())
