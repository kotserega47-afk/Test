"""TASK-49.A: Q-PTB1 producer-wait primitive (real PTB 22.8 Application)."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from telegram import User
from telegram.ext import Application, TypeHandler

from modules.antares.ptb_producer_wait import (
    ProducerWaitStatus,
    PtbProducerWaitError,
    PtbProducerWaitHost,
    attach_producer_wait_to_shutdown_host,
)
from modules.antares.ptb_update_intake import (
    AntaresUpdateIntakeError,
    AntaresUpdateIntakeQueue,
)
from modules.antares.shutdown_session import (
    ApplicationHttpState,
    ControllableClock,
    ProducersCompleteAttestation,
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


def _build_app(
    *, concurrent_updates: bool | int = True
) -> tuple[Application, AntaresUpdateIntakeQueue]:
    queue = AntaresUpdateIntakeQueue()
    app = (
        Application.builder()
        .token("1:test-token")
        .concurrent_updates(concurrent_updates)
        .update_queue(queue)
        .build()
    )
    return app, queue


async def _start_app(app: Application) -> None:
    with _patch_telegram_http():
        await app.initialize()
        await app.start()


async def _app_with_host(
    *,
    concurrent_updates: bool | int = True,
    clock: ControllableClock | None = None,
) -> tuple[Application, AntaresUpdateIntakeQueue, PtbProducerWaitHost]:
    """Install host on a built Application, then initialize+start (PTB 22.8)."""

    app, queue = _build_app(concurrent_updates=concurrent_updates)
    loop = asyncio.get_running_loop()
    kwargs: dict = {"application": app, "loop": loop}
    if clock is not None:
        kwargs["clock"] = clock
    host = PtbProducerWaitHost(**kwargs)
    await _start_app(app)
    return app, queue, host


async def _shutdown_app(app: Application) -> None:
    if app.running:
        await app.stop()
    if getattr(app, "_initialized", False):
        await app.shutdown()


async def _await_true(predicate, *, turns: int = 200) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("predicate not satisfied")


def test_two_live_producers_first_finish_does_not_cancel_second() -> None:
    async def _main() -> None:
        clk = ControllableClock(8_000.0)
        clk.bind_loop()
        app, queue, host = await _app_with_host(concurrent_updates=True, clock=clk)
        e1, e2 = asyncio.Event(), asyncio.Event()
        r1, r2 = asyncio.Event(), asyncio.Event()
        f1, f2 = asyncio.Event(), asyncio.Event()
        n = {"i": 0}

        async def handler(_update, _context) -> None:
            idx = n["i"]
            n["i"] += 1
            if idx == 0:
                e1.set()
                await r1.wait()
                f1.set()
            else:
                e2.set()
                await r2.wait()
                f2.set()

        app.add_handler(TypeHandler(object, handler, block=True))
        await queue.put(object())
        await queue.put(object())
        await e1.wait()
        await e2.wait()
        host.seal_intake()
        live_before = [t for t in app._Application__create_task_tasks if not t.done()]  # noqa: SLF001
        assert len(live_before) >= 2
        waiter = asyncio.create_task(host.wait_producers_complete(deadline=8_030.0))
        await asyncio.sleep(0)
        assert host.owner_task is not None and not host.owner_task.done()
        r1.set()
        await _await_true(lambda: f1.is_set())
        still = [t for t in live_before if not t.done()]
        assert still, "second producer must still be alive"
        assert not any(t.cancelled() for t in still)
        assert not f2.is_set()
        assert host.snapshot().producers_complete is False
        assert host._issued is None  # noqa: SLF001
        assert waiter.done() is False
        r2.set()
        out = await waiter
        assert f2.is_set()
        assert out.attestation is not None
        assert out.snapshot.producers_complete is True
        assert out.snapshot.status is ProducerWaitStatus.COMPLETE
        assert host.snapshot() == out.snapshot
        await _shutdown_app(app)

    asyncio.run(_main())


def test_deadline_incomplete_does_not_cancel_producer() -> None:
    async def _main() -> None:
        clk = ControllableClock(5_000.0)
        clk.bind_loop()
        app, queue, host = await _app_with_host(clock=clk)
        entered = asyncio.Event()
        release = asyncio.Event()
        saw_cancel = {"v": False}

        async def handler(_update, _context) -> None:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                saw_cancel["v"] = True
                raise

        app.add_handler(TypeHandler(object, handler, block=True))
        await queue.put(object())
        await entered.wait()
        host.seal_intake()
        waiter = asyncio.create_task(host.wait_producers_complete(deadline=5_001.0))
        await asyncio.sleep(0)
        clk.advance(2.0)
        out = await waiter
        assert out.attestation is None
        assert out.snapshot.status is ProducerWaitStatus.INCOMPLETE
        assert out.snapshot.producers_complete is False
        assert not saw_cancel["v"]
        assert host.owner_task is not None and not host.owner_task.done()
        release.set()
        late = await host.wait_producers_complete(deadline=None)
        assert late.attestation is not None
        assert late.snapshot.producers_complete is True
        assert host.procedure_deadline == 5_001.0
        assert host.owner_task is not None
        again = await host.wait_producers_complete(deadline=9_999.0)
        assert again.attestation is late.attestation
        assert again.snapshot == late.snapshot == host.snapshot()
        await _shutdown_app(app)

    asyncio.run(_main())


def test_block_false_and_application_create_task_tracked() -> None:
    async def _main() -> None:
        clk = ControllableClock(6_000.0)
        clk.bind_loop()
        app, queue, host = await _app_with_host(concurrent_updates=True, clock=clk)
        entered = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def handler(_update, context) -> None:
            entered.set()

            async def nested() -> None:
                await release.wait()
                finished.set()

            context.application.create_task(nested())

        app.add_handler(TypeHandler(object, handler, block=False))
        await queue.put(object())
        await entered.wait()
        host.seal_intake()
        waiter = asyncio.create_task(host.wait_producers_complete(deadline=6_001.0))
        await asyncio.sleep(0)
        clk.advance(2.0)
        out = await waiter
        assert out.attestation is None
        assert not finished.is_set()
        assert host.owner_task is not None and not host.owner_task.done()
        release.set()
        done = await host.wait_producers_complete(deadline=None)
        assert finished.is_set()
        assert done.attestation is not None
        assert done.snapshot.producers_complete is True
        await _shutdown_app(app)

    asyncio.run(_main())


def test_sequential_concurrent_updates_false() -> None:
    async def _main() -> None:
        app, queue, host = await _app_with_host(concurrent_updates=False)
        assert app.concurrent_updates == 1
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(_update, _context) -> None:
            entered.set()
            await release.wait()

        app.add_handler(TypeHandler(object, handler, block=True))
        await queue.put(object())
        await entered.wait()
        assert (
            host.snapshot().process_update_inflight >= 1
            or host.snapshot().unfinished_tasks >= 1
        )
        host.seal_intake()
        clk = ControllableClock(7_000.0)
        clk.bind_loop()
        host._clock = clk  # noqa: SLF001
        waiter = asyncio.create_task(host.wait_producers_complete(deadline=7_001.0))
        await asyncio.sleep(0)
        clk.advance(2.0)
        out = await waiter
        assert out.attestation is None
        release.set()
        done = await host.wait_producers_complete(deadline=None)
        assert done.attestation is not None
        assert done.snapshot.producers_complete is True
        await _shutdown_app(app)

    asyncio.run(_main())


def test_empty_queue_while_handler_running_no_proof() -> None:
    async def _main() -> None:
        app, queue, host = await _app_with_host()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(_update, _context) -> None:
            entered.set()
            await release.wait()

        app.add_handler(TypeHandler(object, handler, block=True))
        await queue.put(object())
        await entered.wait()
        assert queue.empty()
        host.seal_intake()
        with pytest.raises(AntaresUpdateIntakeError):
            queue.put_nowait(object())
        clk = ControllableClock(1_000.0)
        clk.bind_loop()
        host._clock = clk  # noqa: SLF001
        task = asyncio.create_task(host.wait_producers_complete(deadline=1_001.0))
        await asyncio.sleep(0)
        clk.advance(2.0)
        out = await task
        assert out.attestation is None
        assert out.snapshot.status is ProducerWaitStatus.INCOMPLETE
        release.set()
        await host.wait_producers_complete(deadline=None)
        await _shutdown_app(app)

    asyncio.run(_main())


def test_waiter_cancel_does_not_cancel_producers() -> None:
    async def _main() -> None:
        app, queue, host = await _app_with_host()
        entered = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def handler(_update, _context) -> None:
            entered.set()
            await release.wait()
            finished.set()

        app.add_handler(TypeHandler(object, handler, block=True))
        await queue.put(object())
        await entered.wait()
        host.seal_intake()
        waiter = asyncio.create_task(host.wait_producers_complete(deadline=None))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not finished.is_set()
        assert host.owner_task is not None and not host.owner_task.done()
        release.set()
        out = await host.wait_producers_complete(deadline=None)
        assert finished.is_set()
        assert out.attestation is not None
        await _shutdown_app(app)

    asyncio.run(_main())


def test_foreign_forged_stale_and_bind_rules() -> None:
    async def _main() -> None:
        app, queue, host = await _app_with_host()
        loop = asyncio.get_running_loop()
        host.seal_intake()
        out = await host.wait_producers_complete(deadline=None)
        assert out.attestation is not None

        admission = WorkAdmission()
        bind_antares_admission(admission)
        admission.open()
        stop = asyncio.Event()
        shut = ShutdownSessionHost(admission=admission, stop=stop, loop=loop)
        attach_producer_wait_to_shutdown_host(shut, host)
        session = shut.arm_request_stop(had_open=True)
        await session.wait_arm_effects()

        with pytest.raises(ShutdownSessionError, match="for_tests"):
            session.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        forged = ProducersCompleteAttestation(
            _mark="ptb_producer_wait",
            _issuer_id=host.issuer_id,
            _procedure_id=host.procedure_id,
            _application_token=id(app),
            _intake_generation=queue.seal_generation,
            _secret=b"not-the-real-secret",
        )
        with pytest.raises(ShutdownSessionError, match="foreign|forged|stale"):
            session.accept_producers_complete(forged)

        stale = ProducersCompleteAttestation._issue_from_producer_wait(  # noqa: SLF001
            issuer_id=host.issuer_id,
            procedure_id=999999,
            application_token=id(app),
            intake_generation=queue.seal_generation,
            secret=b"0123456789abcdef",
        )
        with pytest.raises(ShutdownSessionError, match="foreign|forged|stale"):
            session.accept_producers_complete(stale)

        session.accept_producers_complete(out.attestation)
        assert session.snapshot().producers_complete_attested is True

        # Cannot bind a different issuer after attestation.
        app2, _q2, host2 = await _app_with_host()
        with pytest.raises(ShutdownSessionError, match="after producers were attested"):
            attach_producer_wait_to_shutdown_host(shut, host2)
        await _shutdown_app(app2)

        # for_tests then bind refused
        reset_antares_admission_for_tests()
        admission2 = WorkAdmission()
        bind_antares_admission(admission2)
        admission2.open()
        shut2 = ShutdownSessionHost(
            admission=admission2, stop=asyncio.Event(), loop=loop
        )
        s2 = shut2.arm_request_stop(had_open=True)
        await s2.wait_arm_effects()
        s2.accept_producers_complete(ProducersCompleteAttestation.for_tests())
        with pytest.raises(ShutdownSessionError, match="for_tests attestation"):
            shut2.bind_application(app)
        with pytest.raises(ShutdownSessionError, match="for_tests attestation"):
            shut2.bind_producer_wait(host)

        # bind_application (without producer-wait) → for_tests refused
        reset_antares_admission_for_tests()
        admission3 = WorkAdmission()
        bind_antares_admission(admission3)
        admission3.open()
        shut3 = ShutdownSessionHost(
            admission=admission3, stop=asyncio.Event(), loop=loop
        )
        shut3.bind_application(app)
        s3 = shut3.arm_request_stop(had_open=True)
        await s3.wait_arm_effects()
        with pytest.raises(ShutdownSessionError, match="for_tests"):
            s3.accept_producers_complete(ProducersCompleteAttestation.for_tests())

        # foreign loop bind
        foreign = asyncio.new_event_loop()
        try:

            def bad_bind() -> None:
                asyncio.set_event_loop(foreign)

                async def go() -> None:
                    attach_producer_wait_to_shutdown_host(shut, host)

                foreign.run_until_complete(go())

            import threading

            err: list[BaseException] = []

            def run() -> None:
                try:
                    bad_bind()
                except BaseException as exc:  # noqa: BLE001
                    err.append(exc)

            t = threading.Thread(target=run)
            t.start()
            t.join(5)
            assert err and isinstance(err[0], (ShutdownSessionError, PtbProducerWaitError))
        finally:
            foreign.close()

        await _shutdown_app(app)

    asyncio.run(_main())


def test_late_complete_same_owner_no_new_budget() -> None:
    async def _main() -> None:
        loop = asyncio.get_running_loop()
        clk = ControllableClock(3_000.0)
        clk.bind_loop()
        app, queue = _build_app()
        admission = WorkAdmission()
        bind_antares_admission(admission)
        admission.open()
        shut = ShutdownSessionHost(
            admission=admission,
            stop=asyncio.Event(),
            loop=loop,
            drain_timeout=5.0,
            clock=clk,
        )
        prod = PtbProducerWaitHost(application=app, loop=loop, clock=clk)
        attach_producer_wait_to_shutdown_host(shut, prod)
        await _start_app(app)

        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(_update, _context) -> None:
            entered.set()
            await release.wait()

        app.add_handler(TypeHandler(object, handler, block=True))
        session = shut.arm_request_stop(had_open=True)
        await session.wait_arm_effects()
        drain_deadline = session.shutdown_deadline
        assert drain_deadline is not None
        await queue.put(object())
        await entered.wait()
        waiter = asyncio.create_task(prod.wait_producers_complete(deadline=drain_deadline))
        await asyncio.sleep(0)
        owner_before = prod.owner_task
        assert owner_before is not None
        clk.advance(6.0)
        out = await waiter
        assert out.attestation is None
        assert session.snapshot().application_http is ApplicationHttpState.OPEN
        assert prod.procedure_deadline == drain_deadline
        release.set()
        late = await prod.wait_and_accept(session, deadline=None)
        assert late.attestation is not None
        assert prod.owner_task is owner_before
        assert prod.procedure_deadline == drain_deadline
        assert session.shutdown_deadline == drain_deadline
        assert late.snapshot.producers_complete is True
        assert late.snapshot == prod.snapshot()
        await _shutdown_app(app)

    asyncio.run(_main())


def test_stop_signal_still_accepted_after_seal() -> None:
    async def _main() -> None:
        app, queue, _host = await _app_with_host()
        queue.seal()
        with pytest.raises(AntaresUpdateIntakeError):
            await queue.put(object())
        await _shutdown_app(app)

    asyncio.run(_main())


def test_unsupported_plain_queue_rejected() -> None:
    async def _main() -> None:
        plain = asyncio.Queue()
        app = (
            Application.builder()
            .token("1:test-token")
            .concurrent_updates(True)
            .update_queue(plain)
            .build()
        )
        with pytest.raises(PtbProducerWaitError, match="update_queue"):
            PtbProducerWaitHost(application=app, loop=asyncio.get_running_loop())

    asyncio.run(_main())


def test_post_complete_process_update_and_create_task_refused() -> None:
    async def _main() -> None:
        app, _queue, host = await _app_with_host()
        ran = {"process": False, "task": False}

        async def handler(_update, _context) -> None:
            return None

        app.add_handler(TypeHandler(object, handler, block=True))
        host.seal_intake()
        out = await host.wait_producers_complete(deadline=None)
        assert out.attestation is not None
        assert out.snapshot.producers_complete is True

        async def late_handler(_update, _context) -> None:
            ran["process"] = True

        app.add_handler(TypeHandler(object, late_handler, block=True), group=1)
        with pytest.raises(PtbProducerWaitError, match="process_update refused"):
            await app.process_update(object())
        assert ran["process"] is False

        async def late_task() -> None:
            ran["task"] = True

        with pytest.raises(PtbProducerWaitError, match="create_task refused"):
            app.create_task(late_task())
        assert ran["task"] is False

        # PTB stop/shutdown needs only sealed _STOP_SIGNAL — entries stay closed.
        await _shutdown_app(app)

    asyncio.run(_main())


def test_entries_stay_closed_during_cleanup_and_after_terminal() -> None:
    async def _main() -> None:
        app, _queue, host = await _app_with_host()
        loop = asyncio.get_running_loop()
        host.seal_intake()
        out = await host.wait_producers_complete(deadline=None)
        assert out.attestation is not None

        admission = WorkAdmission()
        bind_antares_admission(admission)
        admission.open()
        shut = ShutdownSessionHost(admission=admission, stop=asyncio.Event(), loop=loop)
        attach_producer_wait_to_shutdown_host(shut, host)
        session = shut.arm_request_stop(had_open=True)
        await session.wait_arm_effects()
        session.accept_producers_complete(out.attestation)

        block = asyncio.Event()
        cleanup_entered = asyncio.Event()

        async def cleanup() -> str:
            cleanup_entered.set()
            await block.wait()
            return "ok"

        session.start_cleanup(cleanup)
        await cleanup_entered.wait()
        ran = {"process": False, "task": False}

        async def late_handler(_update, _context) -> None:
            ran["process"] = True

        app.add_handler(TypeHandler(object, late_handler, block=True))
        with pytest.raises(PtbProducerWaitError, match="process_update refused"):
            await app.process_update(object())
        assert ran["process"] is False

        async def late_task() -> None:
            ran["task"] = True

        with pytest.raises(PtbProducerWaitError, match="create_task refused"):
            app.create_task(late_task())
        assert ran["task"] is False

        # Staff PTB stop/shutdown completes while cleanup is still blocked.
        await _shutdown_app(app)
        assert not app.running
        assert not getattr(app, "_initialized", False)

        block.set()
        await session.wait_terminal()

        # After terminal, entries must not reopen.
        with pytest.raises(PtbProducerWaitError, match="process_update refused"):
            await app.process_update(object())
        assert ran["process"] is False
        with pytest.raises(PtbProducerWaitError, match="create_task refused"):

            async def again() -> None:
                ran["task"] = True

            app.create_task(again())
        assert ran["task"] is False

    asyncio.run(_main())


def test_host_install_before_initialize_then_start() -> None:
    async def _main() -> None:
        app, queue = _build_app()
        host = PtbProducerWaitHost(application=app, loop=asyncio.get_running_loop())
        with pytest.raises(PtbProducerWaitError, match="already installed"):
            PtbProducerWaitHost(application=app, loop=asyncio.get_running_loop())
        await _start_app(app)
        host.seal_intake()
        out = await host.wait_producers_complete(deadline=None)
        assert out.attestation is not None
        await _shutdown_app(app)
        assert queue.sealed

    asyncio.run(_main())


def test_host_install_refuses_direct_process_update_before_wrapper() -> None:
    """Counters alone miss a direct process_update; pre-initialize install refuses."""

    async def _main() -> None:
        app, _queue = _build_app(concurrent_updates=False)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(_update, _context) -> None:
            entered.set()
            await release.wait()

        app.add_handler(TypeHandler(object, handler, block=True))
        with _patch_telegram_http():
            await app.initialize()
        # Direct call: queue/create_task/processor can stay zero while handler runs.
        pu_task = asyncio.create_task(app.process_update(object()))
        await entered.wait()
        # Prove the old counter-only check would have been blind to this call.
        assert app.update_processor.current_concurrent_updates == 0
        assert _queue_unfinished_safe(app) == 0
        assert not [t for t in getattr(app, "_Application__create_task_tasks") if not t.done()]  # noqa: SLF001
        with pytest.raises(
            PtbProducerWaitError,
            match="before Application.initialize",
        ):
            PtbProducerWaitHost(application=app, loop=asyncio.get_running_loop())
        # Without a host there is no COMPLETE while the handler is alive.
        release.set()
        await pu_task
        # Still initialized → install remains refused (no post-hoc wrap).
        with pytest.raises(PtbProducerWaitError, match="before Application.initialize"):
            PtbProducerWaitHost(application=app, loop=asyncio.get_running_loop())
        await _shutdown_app(app)

    asyncio.run(_main())


def _queue_unfinished_safe(app: Application) -> int:
    return int(getattr(app.update_queue, "_unfinished_tasks", 0))
