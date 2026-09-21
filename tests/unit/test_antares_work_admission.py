from __future__ import annotations

import asyncio
import gc
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.access_guard import deny_message
from core.access_rules import CommandRule
from core.job_dispatch import _reset_job_executor_for_tests, get_job_executor
from core.job_runner import Actor
from modules.antares import handlers
from modules.antares.application_lifecycle import run_ptb_lifecycle
from modules.antares.work_admission import (
    ADMISSION_CLOSED_REPLY,
    AdmissionAccepted,
    AdmissionRejected,
    AdmissionState,
    AdmissionStopError,
    AdmissionTransitionError,
    WorkAdmission,
    bind_antares_admission,
    bound_admission,
    request_antares_stop,
    reset_antares_admission_for_tests,
    watch_admitted_future,
)


@pytest.fixture(autouse=True)
def _reset_admission() -> None:
    prev_rules = handlers._rules
    prev_logger = handlers._logger
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    handlers._rules = None
    handlers._logger = None
    yield
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    handlers._rules = prev_rules
    handlers._logger = prev_logger


_DISPATCH_CMDS = (
    (handlers.cmd_run_wallet, "run_wallet", "wallet", "jid-wallet"),
    (handlers.cmd_run_hourly, "run_hourly", "hourly", "jid-hourly"),
    (handlers.cmd_run_download, "run_download", "download", "jid-download"),
    (handlers.cmd_run_rate, "run_rate", "rate", "jid-rate"),
    (
        handlers.cmd_operator_wallets_ready,
        "operator_wallets_ready",
        "script_job:operator_wallets_ready",
        "jid-operator-wallets-ready",
    ),
    (
        handlers.cmd_wallet_editor_refresh,
        "wallet_editor_refresh",
        "wallet_editor_registry_refresh",
        "jid-wallet-editor-refresh",
    ),
)


def _allow_dispatch() -> SimpleNamespace:
    commands = {
        command: CommandRule(
            required_level=1,
            allow_private=True,
            allow_groups=True,
            enabled=True,
        )
        for _cb, command, _job, _jid in _DISPATCH_CMDS
    }
    rules = SimpleNamespace(
        commands_map=commands,
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


def _job_fail_logs(logger: MagicMock) -> list[object]:
    return [
        call
        for call in logger.exception.call_args_list
        if call.args and call.args[0] == "admitted %s job failed"
    ]


def _capture_asyncio_errors(loop: asyncio.AbstractEventLoop) -> list:
    bucket: list = []

    def _handler(_loop, context):  # noqa: ANN001
        bucket.append(context)

    loop.set_exception_handler(_handler)
    return bucket


async def _drain_scheduled(loop: asyncio.AbstractEventLoop) -> None:
    ready = loop.create_future()
    loop.call_soon(ready.set_result, None)
    await ready


async def _assert_no_unhandled_asyncio(
    loop: asyncio.AbstractEventLoop,
    bucket: list,
    holders: list,
) -> None:
    await _drain_scheduled(loop)
    holders.clear()
    gc.collect()
    await _drain_scheduled(loop)
    assert bucket == [], bucket


def _capture_submit(captured: dict[str, object]):
    orig_submit = WorkAdmission.submit_job_if_open

    def _submit(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        out = orig_submit(self, *args, **kwargs)
        if isinstance(out, AdmissionAccepted):
            captured["future"] = out.future
        return out

    return _submit


def _capture_watch(captured: dict[str, object], orig_watch):
    def _watch(future, logger_obj, *, job_type):  # noqa: ANN001
        admitted = orig_watch(future, logger_obj, job_type=job_type)
        captured["admitted"] = admitted
        return admitted

    return _watch


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


def test_occupied_single_worker_starts_request_job_after_seal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOB_EXECUTOR_MAX_WORKERS", "1")
    _reset_job_executor_for_tests()
    occupied = threading.Event()
    release_worker = threading.Event()
    job_started = threading.Event()

    def _blocker() -> None:
        occupied.set()
        assert release_worker.wait(timeout=5)

    def _job(job_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
        job_started.set()
        return "jid-after-worker"

    monkeypatch.setattr("modules.antares.work_admission.request_job", _job)
    executor = get_job_executor()
    assert isinstance(executor, ThreadPoolExecutor)
    assert executor._max_workers == 1
    executor.submit(_blocker)
    assert occupied.wait(timeout=5)

    admission = _open_bound()
    outcome = admission.submit_job_if_open(
        "wallet", Actor(kind="tg", chat_id=11, user_id=22)
    )
    assert isinstance(outcome, AdmissionAccepted)
    admission.seal()
    assert admission.state is AdmissionState.SEALED
    assert job_started.is_set() is False
    release_worker.set()
    assert job_started.wait(timeout=5)
    assert outcome.future.result(timeout=5) == "jid-after-worker"


@pytest.mark.parametrize("callback,command,job_type,job_id", _DISPATCH_CMDS)
def test_isolated_open_submit_before_reply_and_actor(
    monkeypatch: pytest.MonkeyPatch,
    callback,
    command,
    job_type,
    job_id,
) -> None:
    recorded: dict[str, object] = {}
    order: list[str] = []

    def _job(submitted_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
        recorded["job_type"] = submitted_type
        recorded["actor"] = actor
        recorded["force"] = force_rules_sync
        return job_id

    orig_submit = WorkAdmission.submit_job_if_open

    def _submit(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        order.append("submit")
        return orig_submit(self, *args, **kwargs)

    monkeypatch.setattr("modules.antares.work_admission.request_job", _job)
    monkeypatch.setattr(WorkAdmission, "submit_job_if_open", _submit)
    _open_bound()
    handlers.bind_rules(_allow_dispatch())
    handlers.bind_logger(MagicMock())
    update = _update()

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        order.append("reply")
        update._replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)
    asyncio.run(callback(update, MagicMock()))
    assert order[0] == "submit"
    assert order.index("submit") < order.index("reply")
    assert recorded["job_type"] == job_type
    actor = recorded["actor"]
    assert isinstance(actor, Actor)
    assert actor.kind == "tg"
    assert actor.chat_id == 11
    assert actor.user_id == 22
    assert recorded["force"] is False
    assert update._replies[0] == f"🚀 Запускаю: {job_type}"
    assert update._replies[1] == f"✅ Принято: {job_type}\njob_id={job_id}"


@pytest.mark.parametrize("callback,command,job_type,_jid", _DISPATCH_CMDS)
def test_isolated_closed_and_sealed_no_zapuskayu(
    monkeypatch: pytest.MonkeyPatch,
    callback,
    command,
    job_type,
    _jid,
) -> None:
    monkeypatch.setattr(
        "modules.antares.work_admission.get_job_executor",
        lambda: SimpleNamespace(submit=lambda *a, **k: (_ for _ in ()).throw(AssertionError())),
    )
    handlers.bind_rules(_allow_dispatch())
    handlers.bind_logger(MagicMock())

    closed = WorkAdmission()
    bind_antares_admission(closed)
    update = _update()
    asyncio.run(callback(update, MagicMock()))
    assert update._replies == [ADMISSION_CLOSED_REPLY]

    reset_antares_admission_for_tests()
    sealed = WorkAdmission()
    bind_antares_admission(sealed)
    sealed.open()
    sealed.seal()
    update2 = _update()
    asyncio.run(callback(update2, MagicMock()))
    assert update2._replies == [ADMISSION_CLOSED_REPLY]


@pytest.mark.parametrize("callback,command,job_type,_jid", _DISPATCH_CMDS)
def test_isolated_acl_deny_no_submit(
    monkeypatch: pytest.MonkeyPatch,
    callback,
    command,
    job_type,
    _jid,
) -> None:
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
    asyncio.run(callback(update, MagicMock()))
    assert update._replies == [deny_message("unknown_command", {})]
    assert submitted == []
    assert all("Запускаю" not in text for text in update._replies)


@pytest.mark.parametrize("callback,command,job_type,job_id", _DISPATCH_CMDS)
def test_unbound_keeps_legacy_order(callback, command, job_type, job_id) -> None:
    update = _update()
    handlers.bind_rules(_allow_dispatch())
    handlers.bind_logger(MagicMock())

    async def _dispatch(submitted_type: str, actor: Actor) -> str:
        assert update._replies == [f"🚀 Запускаю: {submitted_type}"]
        assert submitted_type == job_type
        assert actor.kind == "tg"
        return job_id

    async def _run() -> None:
        with patch(
            "core.tg_command_dispatch.dispatch_job_async", side_effect=_dispatch
        ) as dispatch:
            await callback(update, MagicMock())
            dispatch.assert_awaited_once()
            assert dispatch.await_args.args[0] == job_type

    asyncio.run(_run())
    assert update._replies == [
        f"🚀 Запускаю: {job_type}",
        f"✅ Принято: {job_type}\njob_id={job_id}",
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
    handlers.bind_rules(_allow_dispatch())
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
        await asyncio.to_thread(started.wait, 5)
        assert started.is_set()
        release.set()
        await task

    asyncio.run(_run())
    assert started.is_set()
    assert any("jid-live" in text for text in update._replies)
    logger.exception.assert_any_call("isolated %s start reply failed", "wallet")
    assert _job_fail_logs(logger) == []


def test_isolated_submit_error_logs_job_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "modules.antares.work_admission.get_job_executor",
        lambda: SimpleNamespace(
            submit=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("executor down"))
        ),
    )
    _open_bound()
    logger = MagicMock()
    handlers.bind_rules(_allow_dispatch())
    handlers.bind_logger(logger)
    update = _update()
    asyncio.run(handlers.cmd_run_hourly(update, MagicMock()))
    logger.exception.assert_any_call("isolated %s submit failed", "hourly")
    assert any("Ошибка при постановке" in text for text in update._replies)
    assert all("Запускаю" not in text for text in update._replies)


def test_watch_after_future_already_failed() -> None:
    future: Future = Future()
    future.set_exception(RuntimeError("already failed"))
    logger = MagicMock()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        bucket = _capture_asyncio_errors(loop)
        admitted = watch_admitted_future(future, logger, job_type="wallet")
        with pytest.raises(RuntimeError, match="already failed"):
            await admitted.wait()
        holders = [admitted]
        del admitted
        await _assert_no_unhandled_asyncio(loop, bucket, holders)

    asyncio.run(_run())
    assert future.cancelled() is False
    assert len(_job_fail_logs(logger)) == 1


def test_cancel_during_first_reply_observes_later_job_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    release = threading.Event()

    def _job(job_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
        assert release.wait(timeout=5)
        raise RuntimeError("accepted job boom")

    monkeypatch.setattr(WorkAdmission, "submit_job_if_open", _capture_submit(captured))
    monkeypatch.setattr("modules.antares.work_admission.request_job", _job)
    monkeypatch.setattr(
        "modules.antares.handlers.watch_admitted_future",
        _capture_watch(captured, watch_admitted_future),
    )
    _open_bound()
    logger = MagicMock()
    handlers.bind_rules(_allow_dispatch())
    handlers.bind_logger(logger)
    update = _update()
    entered_reply = asyncio.Event()

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        if "Запускаю" in text:
            entered_reply.set()
            await asyncio.Event().wait()
        update._replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        bucket = _capture_asyncio_errors(loop)
        task = asyncio.create_task(handlers.cmd_run_wallet(update, MagicMock()))
        await asyncio.wait_for(entered_reply.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        future = captured["future"]
        assert future.cancelled() is False
        assert loop.is_running()
        release.set()
        with pytest.raises(RuntimeError, match="accepted job boom"):
            await asyncio.to_thread(future.result, 5)
        admitted = captured.pop("admitted")
        assert admitted._done.wait(timeout=5)
        holders = [admitted, task]
        del admitted
        await _assert_no_unhandled_asyncio(loop, bucket, holders)

    asyncio.run(_run())
    assert len(_job_fail_logs(logger)) == 1
    start_logs = [
        call
        for call in logger.exception.call_args_list
        if call.args and call.args[0] == "isolated %s start reply failed"
    ]
    assert start_logs == []


def test_cancel_during_future_wait_does_not_duplicate_job_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    started = threading.Event()
    release = threading.Event()
    orig_watch = watch_admitted_future

    def _watch(future, logger_obj, *, job_type):  # noqa: ANN001
        admitted = orig_watch(future, logger_obj, job_type=job_type)
        captured["admitted"] = admitted
        orig_wait = admitted.wait

        async def _wait():
            entered_wait.set()
            return await orig_wait()

        admitted.wait = _wait  # type: ignore[method-assign]
        return admitted

    def _job(job_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
        started.set()
        assert release.wait(timeout=5)
        raise RuntimeError("wait-cancel job boom")

    monkeypatch.setattr(WorkAdmission, "submit_job_if_open", _capture_submit(captured))
    monkeypatch.setattr("modules.antares.work_admission.request_job", _job)
    monkeypatch.setattr("modules.antares.handlers.watch_admitted_future", _watch)
    _open_bound()
    logger = MagicMock()
    handlers.bind_rules(_allow_dispatch())
    handlers.bind_logger(logger)
    update = _update()
    entered_wait = asyncio.Event()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        bucket = _capture_asyncio_errors(loop)
        task = asyncio.create_task(handlers.cmd_run_wallet(update, MagicMock()))
        await asyncio.wait_for(entered_wait.wait(), timeout=5)
        await asyncio.to_thread(started.wait, 5)
        assert update._replies[0] == "🚀 Запускаю: wallet"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        future = captured["future"]
        assert future.cancelled() is False
        assert loop.is_running()
        release.set()
        with pytest.raises(RuntimeError, match="wait-cancel job boom"):
            await asyncio.to_thread(future.result, 5)
        admitted = captured.pop("admitted")
        assert admitted._done.wait(timeout=5)
        holders = [admitted, task]
        del admitted
        await _assert_no_unhandled_asyncio(loop, bucket, holders)

    asyncio.run(_run())
    assert len(_job_fail_logs(logger)) == 1


def test_admitted_result_survives_closed_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def _job(job_type: str, actor: Actor, *, force_rules_sync: bool = False) -> str:
        started.set()
        assert release.wait(timeout=5)
        raise RuntimeError("late job boom")

    monkeypatch.setattr("modules.antares.work_admission.request_job", _job)
    admission = _open_bound()
    outcome = admission.submit_job_if_open(
        "wallet", Actor(kind="tg", chat_id=1, user_id=2)
    )
    assert isinstance(outcome, AdmissionAccepted)
    logger = MagicMock()
    first_bucket: list = []

    async def _attach():
        loop = asyncio.get_running_loop()
        bucket = _capture_asyncio_errors(loop)
        first_bucket.append(bucket)
        admitted = watch_admitted_future(outcome.future, logger, job_type="wallet")
        await _drain_scheduled(loop)
        return admitted

    admitted = asyncio.run(_attach())
    assert first_bucket[0] == []
    assert started.wait(timeout=5)
    release.set()
    with pytest.raises(RuntimeError, match="late job boom"):
        outcome.future.result(timeout=5)
    assert admitted._done.wait(timeout=5)
    assert admitted.job_error is not None
    assert "late job boom" in str(admitted.job_error)
    assert admitted.future.cancelled() is False

    async def _wait_stored(job):
        loop = asyncio.get_running_loop()
        bucket = _capture_asyncio_errors(loop)
        with pytest.raises(RuntimeError, match="late job boom"):
            await job.wait()
        holders = [job]
        del job
        await _assert_no_unhandled_asyncio(loop, bucket, holders)

    asyncio.run(_wait_stored(admitted))
    admitted = None
    gc.collect()
    assert len(_job_fail_logs(logger)) == 1
    assert first_bucket[0] == []


def test_helper_bind_open_seal_on_initialize_error() -> None:
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


def test_helper_bind_open_seal_on_start_error() -> None:
    admission = WorkAdmission()

    class _App:
        async def initialize(self) -> None:
            assert admission.state is AdmissionState.BOUND_CLOSED

        async def start(self) -> None:
            assert admission.state is AdmissionState.BOUND_CLOSED
            raise RuntimeError("start failed")

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
                with pytest.raises(RuntimeError, match="start failed"):
                    await run_ptb_lifecycle(_App(), stop=stop, admission=admission)
                cleanup.assert_awaited()
                assert admission.state is AdmissionState.SEALED
                assert admission.state is not AdmissionState.OPEN

    asyncio.run(_run())


def test_helper_opens_after_start_and_seals_before_cleanup() -> None:
    admission = WorkAdmission()
    marks: list[str] = []
    stop = asyncio.Event()

    class _App:
        async def initialize(self) -> None:
            marks.append(admission.state.value)

        async def start(self) -> None:
            marks.append(admission.state.value)
            stop.set()

    async def _cleanup(app):  # noqa: ANN001
        marks.append(admission.state.value)
        return [], (), [], None

    async def _run() -> None:
        with patch(
            "modules.antares.application_lifecycle.unsupported_application_reasons",
            return_value=(),
        ):
            with patch(
                "modules.antares.application_lifecycle._await_cleanup",
                side_effect=_cleanup,
            ):
                await run_ptb_lifecycle(_App(), stop=stop, admission=admission)

    asyncio.run(_run())
    assert marks[0] == AdmissionState.BOUND_CLOSED.value
    assert marks[1] == AdmissionState.BOUND_CLOSED.value
    assert marks[-1] == AdmissionState.SEALED.value


def test_stop_from_loop_thread_sets_event_directly() -> None:
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
        request_antares_stop(stop, admission)
        assert stop.is_set()
        assert admission.state is AdmissionState.SEALED
        assert seen == []

    asyncio.run(_run())


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


def test_stop_from_other_thread_without_loop_errors_and_stays_sealed() -> None:
    admission = _open_bound()
    caught: dict[str, BaseException] = {}

    async def _run() -> None:
        stop = asyncio.Event()

        def _other() -> None:
            try:
                request_antares_stop(stop, admission)
            except BaseException as exc:
                caught["exc"] = exc

        thread = threading.Thread(target=_other)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert isinstance(caught.get("exc"), AdmissionStopError)
        assert admission.state is AdmissionState.SEALED
        assert stop.is_set() is False

    asyncio.run(_run())
