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


async def _running_app(
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
    with _patch_telegram_http():
        await app.initialize()
        await app.start()
    return app, queue


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
        app, queue = await _running_app(concurrent_updates=True)
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
        host = PtbProducerWaitHost(application=app, loop=asyncio.get_running_loop())
        await queue.put(object())
        await queue.put(object())
        await e1.wait()
        await e2.wait()
        host.seal_intake()
        live_before = list(app._Application__create_task_tasks)  # noqa: SLF001
        assert len([t for t in live_before if not t.done()]) >= 2
        r1.set()
        await _await_true(lambda: f1.is_set())
        still = [t for t in live_before if not t.done()]
        assert still, "second producer must still be alive"
        assert not any(t.cancelled() for t in still)
        assert not f2.is_set()
        r2.set()
        out = await host.wait_producers_complete()
        assert f2.is_set()
        assert out.attestation is not None
        assert out.snapshot.producers_complete is True
        assert out.snapshot.status is ProducerWaitStatus.COMPLETE
        assert host.snapshot() == out.snapshot
        await _shutdown_app(app)

    asyncio.run(_main())


def test_deadline_incomplete_does_not_cancel_producer() -> None:
    async def _main() -> None:
        app, queue = await _running_app()
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
        loop = asyncio.get_running_loop()
        clk = ControllableClock(5_000.0)
        clk.bind_loop()
        host = PtbProducerWaitHost(application=app, loop=loop, clock=clk)
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
        app, queue = await _running_app(concurrent_updates=True)
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
        loop = asyncio.get_running_loop()
        clk = ControllableClock(6_000.0)
        clk.bind_loop()
        host = PtbProducerWaitHost(application=app, loop=loop, clock=clk)
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
        app, queue = await _running_app(concurrent_updates=False)
        assert app.concurrent_updates == 1
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(_update, _context) -> None:
            entered.set()
            await release.wait()

        app.add_handler(TypeHandler(object, handler, block=True))
        host = PtbProducerWaitHost(application=app, loop=asyncio.get_running_loop())
        await queue.put(object())
        await entered.wait()
        assert host.snapshot().process_update_inflight >= 1 or host.snapshot().unfinished_tasks >= 1
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
        app, queue = await _running_app()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(_update, _context) -> None:
            entered.set()
            await release.wait()

        app.add_handler(TypeHandler(object, handler, block=True))
        host = PtbProducerWaitHost(application=app, loop=asyncio.get_running_loop())
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
        app, queue = await _running_app()
        entered = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def handler(_update, _context) -> None:
            entered.set()
            await release.wait()
            finished.set()

        app.add_handler(TypeHandler(object, handler, block=True))
        host = PtbProducerWaitHost(application=app, loop=asyncio.get_running_loop())
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
        app, queue = await _running_app()
        loop = asyncio.get_running_loop()
        host = PtbProducerWaitHost(application=app, loop=loop)
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
        app2, _q2 = await _running_app()
        host2 = PtbProducerWaitHost(application=app2, loop=loop)
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
            shut2.bind_producer_wait(host)

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
        app, queue = await _running_app()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(_update, _context) -> None:
            entered.set()
            await release.wait()

        app.add_handler(TypeHandler(object, handler, block=True))
        loop = asyncio.get_running_loop()
        clk = ControllableClock(3_000.0)
        clk.bind_loop()
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
        session = shut.arm_request_stop(had_open=True)
        await session.wait_arm_effects()
        drain_deadline = session.shutdown_deadline
        assert drain_deadline is not None
        await queue.put(object())
        await entered.wait()
        owner_before = None
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
        app, queue = await _running_app()
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
        with _patch_telegram_http():
            await app.initialize()
            await app.start()
        with pytest.raises(PtbProducerWaitError, match="update_queue"):
            PtbProducerWaitHost(application=app, loop=asyncio.get_running_loop())
        await _shutdown_app(app)

    asyncio.run(_main())
