from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.access_guard import deny_message
from core.access_rules import CommandRule
from core.job_dispatch import _reset_job_executor_for_tests
from core.job_runner import Actor
from modules.antares import handlers
from modules.antares.application_lifecycle import run_ptb_lifecycle
from modules.antares.work_admission import (
    ADMISSION_CLOSED_REPLY,
    AdmissionAccepted,
    AdmissionRejected,
    AdmissionState,
    AdmissionTransitionError,
    WorkAdmission,
    await_admitted_future,
    bind_antares_admission,
    bound_admission,
    request_antares_stop,
    reset_antares_admission_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_admission() -> None:
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    handlers._rules = None
    handlers._logger = None
    yield
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    handlers._rules = None
    handlers._logger = None


def _allow_wallet() -> SimpleNamespace:
    rules = SimpleNamespace(
        commands_map={
            "run_wallet": CommandRule(
                required_level=1,
                allow_private=True,
                allow_groups=True,
                enabled=True,
            ),
            "run_hourly": CommandRule(
                required_level=1,
                allow_private=True,
                allow_groups=True,
                enabled=True,
            ),
        },
        access_map={("private", 22): 1, (11, 22): 1},
    )

    class _Rules:
        def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
            return rules

    return _Rules()


def _update() -> MagicMock:
    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = 11
    update.effective_user.id = 22
    replies: list[str] = []

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)
    update._replies = replies
    return update


def _open_bound() -> WorkAdmission:
    admission = WorkAdmission()
    bind_antares_admission(admission)
    admission.open()
    return admission


def test_transitions_and_idempotent_seal() -> None:
    admission = WorkAdmission()
    assert admission.state is AdmissionState.UNBOUND
    with pytest.raises(AdmissionTransitionError):
        admission.open()
    with pytest.raises(AdmissionTransitionError):
        admission.seal()
    bind_antares_admission(admission)
    assert admission.state is AdmissionState.BOUND_CLOSED
    with pytest.raises(AdmissionTransitionError):
        bind_antares_admission(WorkAdmission())
    admission.open()
    assert admission.state is AdmissionState.OPEN
    with pytest.raises(AdmissionTransitionError):
        admission.open()
    admission.seal()
    admission.seal()
    assert admission.state is AdmissionState.SEALED
    with pytest.raises(AdmissionTransitionError):
        admission.open()


def test_closed_and_sealed_reject_without_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def _submit(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append((args, kwargs))
        raise AssertionError("submit must not run")

    monkeypatch.setattr(
        "modules.antares.work_admission.get_job_executor",
        lambda: SimpleNamespace(submit=_submit),
    )
    admission = WorkAdmission()
    bind_antares_admission(admission)
    actor = Actor(kind="tg", chat_id=11, user_id=22)
    closed = admission.submit_job_if_open("wallet", actor)
    assert isinstance(closed, AdmissionRejected)
    assert closed.state is AdmissionState.BOUND_CLOSED
    admission.open()
    admission.seal()
    sealed = admission.submit_job_if_open("wallet", actor)
    assert isinstance(sealed, AdmissionRejected)
    assert sealed.state is AdmissionState.SEALED
    assert calls == []


def test_submit_failure_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    def _submit(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("executor down")

    monkeypatch.setattr(
        "modules.antares.work_admission.get_job_executor",
        lambda: SimpleNamespace(submit=_submit),
    )
    admission = _open_bound()
    with pytest.raises(RuntimeError, match="executor down"):
        admission.submit_job_if_open("wallet", Actor(kind="tg", chat_id=11, user_id=22))
    assert admission.state is AdmissionState.OPEN


def test_seal_wins_race_no_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted = threading.Event()
    start_submit = threading.Event()
    result: dict[str, object] = {}

    def _submit(*args, **kwargs):  # noqa: ANN002, ANN003
        submitted.set()
        raise AssertionError("seal won; submit must not run")

    monkeypatch.setattr(
        "modules.antares.work_admission.get_job_executor",
        lambda: SimpleNamespace(submit=_submit),
    )
    admission = _open_bound()
    actor = Actor(kind="tg", chat_id=11, user_id=22)

    def _try_submit() -> None:
        assert start_submit.wait(timeout=5)
        result["outcome"] = admission.submit_job_if_open("wallet", actor)

    thread = threading.Thread(target=_try_submit)
    thread.start()
    admission.seal()
    start_submit.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert submitted.is_set() is False
    assert isinstance(result["outcome"], AdmissionRejected)
    assert admission.state is AdmissionState.SEALED


def test_accept_wins_future_may_run_after_seal(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def _job(job_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
        assert job_type == "wallet"
        assert actor.kind == "tg"
        assert force_rules_sync is False
        started.set()
        assert release.wait(timeout=5)
        return "jid-after-seal"

    monkeypatch.setattr("modules.antares.work_admission.request_job", _job)
    admission = _open_bound()
    outcome = admission.submit_job_if_open(
        "wallet",
        Actor(kind="tg", chat_id=11, user_id=22),
        force_rules_sync=False,
    )
    assert isinstance(outcome, AdmissionAccepted)
    admission.seal()
    assert admission.state is AdmissionState.SEALED
    assert admission.submit_job_if_open(
        "wallet", Actor(kind="tg", chat_id=11, user_id=22)
    ).__class__ is AdmissionRejected
    release.set()
    assert outcome.future.result(timeout=5) == "jid-after-seal"
    assert started.wait(timeout=5)


def test_isolated_wallet_order_acl_and_hourly_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    def _job(job_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
        recorded["job_type"] = job_type
        recorded["actor"] = actor
        recorded["force"] = force_rules_sync
        return "jid-wallet"

    monkeypatch.setattr("modules.antares.work_admission.request_job", _job)
    _open_bound()
    handlers.bind_rules(_allow_wallet())
    handlers.bind_logger(MagicMock())
    update = _update()

    async def _run() -> None:
        await handlers.cmd_run_wallet(update, MagicMock())

    asyncio.run(_run())
    assert recorded["job_type"] == "wallet"
    actor = recorded["actor"]
    assert isinstance(actor, Actor)
    assert actor.kind == "tg"
    assert actor.chat_id == 11
    assert actor.user_id == 22
    assert recorded["force"] is False
    assert update._replies[0] == "🚀 Запускаю: wallet"
    assert update._replies[1] == "✅ Принято: wallet\njob_id=jid-wallet"

    hourly = _update()

    async def _hourly() -> None:
        with patch(
            "core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock
        ) as dispatch:
            dispatch.return_value = "jid-hourly"
            await handlers.cmd_run_hourly(hourly, MagicMock())
            dispatch.assert_awaited_once()
            assert dispatch.await_args.args[0] == "hourly"

    asyncio.run(_hourly())
    assert hourly._replies[0] == "🚀 Запускаю: hourly"


def test_isolated_closed_no_zapuskayu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "modules.antares.work_admission.get_job_executor",
        lambda: SimpleNamespace(submit=lambda *a, **k: (_ for _ in ()).throw(AssertionError())),
    )
    admission = WorkAdmission()
    bind_antares_admission(admission)
    handlers.bind_rules(_allow_wallet())
    handlers.bind_logger(MagicMock())
    update = _update()
    asyncio.run(handlers.cmd_run_wallet(update, MagicMock()))
    assert update._replies == [ADMISSION_CLOSED_REPLY]


def test_isolated_acl_deny_no_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted = []
    monkeypatch.setattr(
        "modules.antares.work_admission.get_job_executor",
        lambda: SimpleNamespace(submit=lambda *a, **k: submitted.append(1)),
    )
    _open_bound()
    class _Empty:
        def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
            return SimpleNamespace(commands_map={}, access_map={})

    handlers.bind_rules(_Empty())
    handlers.bind_logger(MagicMock())
    update = _update()
    asyncio.run(handlers.cmd_run_wallet(update, MagicMock()))
    assert update._replies == [deny_message("unknown_command", {})]
    assert submitted == []
    assert all("Запускаю" not in text for text in update._replies)


def test_unbound_wallet_keeps_legacy_order() -> None:
    update = _update()
    handlers.bind_rules(_allow_wallet())
    handlers.bind_logger(MagicMock())

    async def _dispatch(job_type: str, actor: Actor) -> str:
        assert update._replies == ["🚀 Запускаю: wallet"]
        return "legacy-id"

    async def _run() -> None:
        with patch(
            "core.tg_command_dispatch.dispatch_job_async", side_effect=_dispatch
        ) as dispatch:
            await handlers.cmd_run_wallet(update, MagicMock())
            dispatch.assert_awaited_once()

    asyncio.run(_run())
    assert update._replies == [
        "🚀 Запускаю: wallet",
        "✅ Принято: wallet\njob_id=legacy-id",
    ]


def test_reply_failure_does_not_cancel_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def _job(job_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
        started.set()
        assert release.wait(timeout=5)
        return "jid-live"

    monkeypatch.setattr("modules.antares.work_admission.request_job", _job)
    _open_bound()
    logger = MagicMock()
    handlers.bind_rules(_allow_wallet())
    handlers.bind_logger(logger)
    update = _update()
    boom = {"n": 0}

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("telegram down")
        update._replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)

    async def _run() -> None:
        task = asyncio.create_task(handlers.cmd_run_wallet(update, MagicMock()))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.05)
        assert started.is_set()
        release.set()
        await task

    asyncio.run(_run())
    assert started.is_set()
    assert any("jid-live" in text for text in update._replies)


def test_cancel_waiter_does_not_cancel_future(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()
    finished: dict[str, object] = {}

    def _job(job_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
        started.set()
        assert release.wait(timeout=5)
        finished["ok"] = True
        return "jid-cancel"

    monkeypatch.setattr("modules.antares.work_admission.request_job", _job)
    admission = _open_bound()
    outcome = admission.submit_job_if_open(
        "wallet", Actor(kind="tg", chat_id=1, user_id=2)
    )
    assert isinstance(outcome, AdmissionAccepted)
    logger = MagicMock()

    async def _run() -> None:
        task = asyncio.create_task(await_admitted_future(outcome.future, logger))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.05)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert outcome.future.cancelled() is False
        release.set()
        assert outcome.future.result(timeout=5) == "jid-cancel"

    asyncio.run(_run())
    assert finished.get("ok") is True


def test_helper_bind_open_seal_on_start_error() -> None:
    admission = WorkAdmission()

    class _App:
        async def initialize(self) -> None:
            assert admission.state is AdmissionState.BOUND_CLOSED
            assert bound_admission() is admission
            raise RuntimeError("initialize failed")

        async def start(self) -> None:
            raise AssertionError("start must not run")

    async def _run() -> None:
        with patch(
            "modules.antares.application_lifecycle.unsupported_application_reasons",
            return_value=(),
        ):
            with patch(
                "modules.antares.application_lifecycle._await_cleanup",
                AsyncMock(return_value=([], (), [], None)),
            ) as cleanup:
                stop = asyncio.Event()
                with pytest.raises(RuntimeError, match="initialize failed"):
                    await run_ptb_lifecycle(_App(), stop=stop, admission=admission)
                cleanup.assert_awaited()
                assert admission.state is AdmissionState.SEALED

    asyncio.run(_run())


def test_helper_opens_after_start_and_seals_before_cleanup() -> None:
    admission = WorkAdmission()
    marks: list[str] = []

    class _App:
        async def initialize(self) -> None:
            marks.append(admission.state.value)

        async def start(self) -> None:
            marks.append(admission.state.value)

    async def _cleanup(app):  # noqa: ANN001
        marks.append(admission.state.value)
        return [], (), [], None

    async def _run() -> None:
        stop = asyncio.Event()

        async def _trip() -> None:
            while admission.state is not AdmissionState.OPEN:
                await asyncio.sleep(0)
            stop.set()

        with patch(
            "modules.antares.application_lifecycle.unsupported_application_reasons",
            return_value=(),
        ):
            with patch(
                "modules.antares.application_lifecycle._await_cleanup",
                side_effect=_cleanup,
            ):
                trip = asyncio.create_task(_trip())
                await run_ptb_lifecycle(_App(), stop=stop, admission=admission)
                await trip

    asyncio.run(_run())
    assert marks[0] == AdmissionState.BOUND_CLOSED.value
    assert marks[1] == AdmissionState.BOUND_CLOSED.value
    assert marks[-1] == AdmissionState.SEALED.value


def test_stop_from_other_thread_uses_call_soon() -> None:
    admission = _open_bound()

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        seen: list[str] = []
        real_soon = loop.call_soon_threadsafe

        def _soon(cb, *args):  # noqa: ANN001, ANN002
            seen.append("soon")
            return real_soon(cb, *args)

        loop.call_soon_threadsafe = _soon  # type: ignore[method-assign]
        started = threading.Event()

        def _other() -> None:
            started.set()
            request_antares_stop(stop, admission, loop=loop)

        thread = threading.Thread(target=_other)
        thread.start()
        assert started.wait(timeout=5)
        await asyncio.wait_for(stop.wait(), timeout=5)
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert admission.state is AdmissionState.SEALED
        assert "soon" in seen

    asyncio.run(_run())
