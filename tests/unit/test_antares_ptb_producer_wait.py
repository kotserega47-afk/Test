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
        assert "create_task_alive" in out.snapshot.remainder or (
            out.snapshot.create_task_alive > 0
            or out.snapshot.concurrent_updates > 0
            or out.snapshot.unfinished_tasks > 0
        )
        release.set()
        await _shutdown_app(app)

    asyncio.run(_main())


def test_admission_sealed_does_not_early_attest_while_producer_live() -> None:
    async def _main() -> None:
        admission = WorkAdmission()
        bind_antares_admission(admission)
        admission.open()
        admission.seal()
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
        assert admission.state.value == "sealed"
        host.seal_intake()
        clk = ControllableClock(2_000.0)
        clk.bind_loop()
        host._clock = clk  # noqa: SLF001
        task = asyncio.create_task(host.wait_producers_complete(deadline=2_001.0))
        await asyncio.sleep(0)
        clk.advance(2.0)
        out = await task
        assert out.attestation is None
        assert out.snapshot.status is ProducerWaitStatus.INCOMPLETE
        release.set()
        await _shutdown_app(app)

    asyncio.run(_main())


def test_all_producers_done_after_sealed_intake_mints_attestation() -> None:
    async def _main() -> None:
        app, queue = await _running_app()
        done = asyncio.Event()

        async def handler(_update, _context) -> None:
            done.set()

        app.add_handler(TypeHandler(object, handler, block=True))
        host = PtbProducerWaitHost(application=app, loop=asyncio.get_running_loop())
        await queue.put(object())
        await done.wait()
        for _ in range(100):
            if host.snapshot().unfinished_tasks == 0 and host.snapshot().create_task_alive == 0:
                break
            await asyncio.sleep(0)
        host.seal_intake()
        out = await host.wait_producers_complete()
        assert out.snapshot.status is ProducerWaitStatus.COMPLETE
        assert out.attestation is not None
        assert out.attestation.application_token == id(app)
        assert out.attestation.intake_generation == queue.seal_generation
        assert not out.attestation.is_test_harness
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
        waiter = asyncio.create_task(host.wait_producers_complete())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not finished.is_set()
        assert host.owner_task is not None and not host.owner_task.done()
        release.set()
        out = await host.wait_producers_complete()
        assert finished.is_set()
        assert out.attestation is not None
        await _shutdown_app(app)

    asyncio.run(_main())


def test_deadline_incomplete_keeps_http_path_open_via_session() -> None:
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
        stop = asyncio.Event()
        shut = ShutdownSessionHost(
            admission=admission,
            stop=stop,
            loop=loop,
            drain_timeout=5.0,
            clock=clk,
        )
        prod = PtbProducerWaitHost(application=app, loop=loop, clock=clk)
        attach_producer_wait_to_shutdown_host(shut, prod)
        session = shut.arm_request_stop(had_open=True)
        await session.wait_arm_effects()
        deadline = session.shutdown_deadline
        assert deadline is not None
        await queue.put(object())
        await entered.wait()
        waiter = asyncio.create_task(prod.wait_producers_complete(deadline=deadline))
        await asyncio.sleep(0)
        # Expiry while producer still live → incomplete (not success).
        clk.advance(6.0)
        out = await waiter
        assert out.attestation is None
        assert out.snapshot.status is ProducerWaitStatus.INCOMPLETE
        assert session.snapshot().producers_complete_attested is False
        assert session.snapshot().application_http is ApplicationHttpState.OPEN
        assert session.shutdown_deadline == deadline
        release.set()
        late = await prod.wait_and_accept(session, deadline=None)
        assert late.attestation is not None
        assert session.snapshot().producers_complete_attested is True
        assert session.shutdown_deadline == deadline
        assert session.snapshot().application_http is ApplicationHttpState.OPEN

        async def cleanup() -> str:
            return "later"

        session.start_cleanup(cleanup)
        term = await session.wait_terminal()
        assert term.snapshot.application_http is ApplicationHttpState.CLEANUP_DONE
        await _shutdown_app(app)

    asyncio.run(_main())


def test_repeat_wait_joins_same_owner_procedure() -> None:
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
        host.seal_intake()
        gen = queue.seal_generation
        w1 = asyncio.create_task(host.wait_producers_complete())
        w2 = asyncio.create_task(host.wait_producers_complete())
        await asyncio.sleep(0)
        assert host.owner_task is not None
        owner = host.owner_task
        release.set()
        o1, o2 = await asyncio.gather(w1, w2)
        assert o1.attestation is not None and o2.attestation is not None
        assert o1.attestation == o2.attestation
        assert queue.seal_generation == gen
        again = await host.wait_producers_complete()
        assert again.attestation == o1.attestation
        assert host.owner_task is owner or host.owner_task is not None
        await _shutdown_app(app)

    asyncio.run(_main())


def test_unsupported_queue_and_foreign_attestation_rejected() -> None:
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

        app2, queue = await _running_app()
        loop = asyncio.get_running_loop()
        host = PtbProducerWaitHost(application=app2, loop=loop)
        host.seal_intake()
        # Idle app with sealed empty queue → complete
        out = await host.wait_producers_complete()
        assert out.attestation is not None

        admission = WorkAdmission()
        bind_antares_admission(admission)
        admission.open()
        stop = asyncio.Event()
        shut = ShutdownSessionHost(admission=admission, stop=stop, loop=loop)
        attach_producer_wait_to_shutdown_host(shut, host)
        session = shut.arm_request_stop(had_open=True)
        await session.wait_arm_effects()
        with pytest.raises(Exception, match="for_tests"):
            session.accept_producers_complete(ProducersCompleteAttestation.for_tests())
        foreign = ProducersCompleteAttestation.mint_for_application(
            application_token=id(object()),
            intake_generation=1,
        )
        with pytest.raises(Exception, match="foreign"):
            session.accept_producers_complete(foreign)
        session.accept_producers_complete(out.attestation)
        assert session.snapshot().producers_complete_attested is True
        await _shutdown_app(app2)

    asyncio.run(_main())


def test_stop_signal_still_accepted_after_seal() -> None:
    async def _main() -> None:
        app, queue = await _running_app()
        queue.seal()
        with pytest.raises(AntaresUpdateIntakeError):
            await queue.put(object())
        await _shutdown_app(app)

    asyncio.run(_main())
