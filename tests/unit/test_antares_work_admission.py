from __future__ import annotations

import asyncio
import gc
import inspect
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from core.access_guard import deny_message
from core.access_rules import AccessRules, CommandRule
from core.rules_v2.models import (
    AccessRule,
    CommandDef,
    CommandPolicy,
    MetaInfo,
    RoleDef,
    RulesSnapshotV2,
)
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
from integrations.wallet_editor_registry_db.registry_export_builder import (
    RegistryExportArtifact,
    RegistryExportSummary,
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


def _capture_submit_if_open(captured: dict[str, object]):
    orig_submit = WorkAdmission.submit_if_open

    def _submit(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        out = orig_submit(self, *args, **kwargs)
        if isinstance(out, AdmissionAccepted):
            captured["future"] = out.future
        return out

    return _submit


def _capture_watch(captured: dict[str, object], orig_watch):
    def _watch(future, logger_obj, **kwargs):  # noqa: ANN001
        admitted = orig_watch(future, logger_obj, **kwargs)
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


_EXPORT_START = "📤 Building registry export from PostgreSQL..."
_PLAN_START = "🧩 Строю WalletEditor Auto-Enable plan (plan-only)..."
_RUN_START = "🧩 Запускаю WalletEditor Auto-Enable (fresh plan + execution)..."
_REPLAY_START = "🔄 Replaying pending/failed registry outbox..."
_BUILDER = (
    "integrations.wallet_editor_registry_db.registry_export_builder.build_registry_export_from_postgres"
)
_PLAN_ORCH = "integrations.wallet_editor_auto_enable.run_auto_enable_plan"
_RUN_ORCH = "integrations.wallet_editor_auto_enable.run_auto_enable"
_REPLAY = "integrations.wallet_editor_registry.replay_pending_outbox_records"
_FORMAT = (
    "integrations.wallet_editor_registry_db.registry_export_builder.format_registry_export_summary"
)
_MSK = ZoneInfo("Europe/Moscow")


def _allow_direct(*commands: str) -> SimpleNamespace:
    commands_map = {
        command: CommandRule(
            required_level=1,
            allow_private=True,
            allow_groups=True,
            enabled=True,
        )
        for command in commands
    }
    rules = SimpleNamespace(
        commands_map=commands_map,
        access_map={("private", 22): 1, (11, 22): 1},
        source="snapshot_v2:test",
    )

    class _Rules:
        def __init__(self) -> None:
            self.invalidate_calls = 0
            self.snapshot_force: list[bool] = []

        def invalidate(self) -> None:
            self.invalidate_calls += 1

        def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
            self.snapshot_force.append(force_sync)
            return rules

    return _Rules()


def _export_artifact(tmp_path: Path) -> RegistryExportArtifact:
    export_path = tmp_path / "wallet_editor_export_sandbox.xlsx"
    export_path.write_bytes(b"fake-xlsx")
    summary = RegistryExportSummary(
        all_results_rows=42,
        runs_rows=7,
        hold_rows=2,
        otlezka_rows=3,
        last_manual_sync_at="2026-07-01T12:00:00+03:00",
        snapshot_hash_short="abc123def456",
        manual_sync_degraded=False,
        generated_at=datetime(2026, 7, 1, 12, 0, 0, tzinfo=_MSK).isoformat(),
        filename=export_path.name,
    )
    return RegistryExportArtifact(path=export_path, filename=export_path.name, summary=summary)


def _work_fail_logs(logger: MagicMock) -> list[object]:
    return [
        call
        for call in logger.exception.call_args_list
        if call.args and call.args[0] == "admitted %s work failed"
    ]


def _forbid_request_job(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("request_job must not run for direct ops")

    monkeypatch.setattr("modules.antares.work_admission.request_job", _boom)
    monkeypatch.setattr("core.job_runner.request_job", _boom)


def _forbid_direct_business(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def _builder(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("builder")
        raise AssertionError("build_registry_export_from_postgres must not run")

    def _plan(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("plan")
        raise AssertionError("run_auto_enable_plan must not run")

    def _run(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("run")
        raise AssertionError("run_auto_enable must not run")

    def _replay(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("replay")
        raise AssertionError("replay_pending_outbox_records must not run")

    def _clocks(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("clocks_reset")
        raise AssertionError("request_scheduler_clocks_reset must not run")

    monkeypatch.setattr(_BUILDER, _builder)
    monkeypatch.setattr(_PLAN_ORCH, _plan)
    monkeypatch.setattr(_RUN_ORCH, _run)
    monkeypatch.setattr(_REPLAY, _replay)
    monkeypatch.setattr(
        "core.scheduler_clocks_control.request_scheduler_clocks_reset",
        _clocks,
    )
    return calls


def _observe_used_executor_submit(monkeypatch: pytest.MonkeyPatch) -> list:
    """Record submit on the executor handlers actually pass into admission."""

    submits: list = []
    executor = get_job_executor()

    def _submit(fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        submits.append({"fn": fn, "args": args, "kwargs": kwargs})
        raise AssertionError("executor.submit must not run for rejected direct ops")

    monkeypatch.setattr(executor, "submit", _submit)
    return submits


def _wrap_job_executor_submit(monkeypatch: pytest.MonkeyPatch, recorded: list):
    executor = get_job_executor()
    orig = executor.submit

    def _submit(fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        recorded.append({"fn": fn, "args": args, "kwargs": kwargs, "executor": executor})
        return orig(fn, *args, **kwargs)

    monkeypatch.setattr(executor, "submit", _submit)
    return executor


def test_generic_submit_if_open_uses_given_executor() -> None:
    admission = _open_bound()
    seen: list[object] = []

    def _fn(value: int, *, flag: bool) -> str:
        seen.append((value, flag))
        return "direct-ok"

    class _Exec:
        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            future: Future = Future()
            future.set_result(fn(*args, **kwargs))
            seen.append("submitted")
            return future

    outcome = admission.submit_if_open(_Exec(), _fn, 7, flag=True)
    assert isinstance(outcome, AdmissionAccepted)
    assert outcome.future.result() == "direct-ok"
    assert seen == [(7, True), "submitted"]


def test_seal_wins_generic_submit_if_open() -> None:
    submitted = threading.Event()
    start_submit = threading.Event()
    result: dict[str, object] = {}

    class _Exec:
        def submit(self, *args, **kwargs):  # noqa: ANN002, ANN003
            submitted.set()
            raise AssertionError("seal won; submit must not run")

    admission = _open_bound()

    def _try_submit() -> None:
        assert start_submit.wait(timeout=5)
        result["outcome"] = admission.submit_if_open(_Exec(), lambda: 1)

    thread = threading.Thread(target=_try_submit)
    thread.start()
    admission.seal()
    start_submit.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert submitted.is_set() is False
    assert isinstance(result["outcome"], AdmissionRejected)


def test_generic_accept_may_finish_after_seal() -> None:
    started = threading.Event()
    release = threading.Event()

    def _work() -> str:
        started.set()
        assert release.wait(timeout=5)
        return "after-seal"

    admission = _open_bound()
    outcome = admission.submit_if_open(get_job_executor(), _work)
    assert isinstance(outcome, AdmissionAccepted)
    admission.seal()
    release.set()
    assert outcome.future.result(timeout=5) == "after-seal"
    assert started.wait(timeout=5)


def test_isolated_export_open_submit_before_reply_exact_callable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact = _export_artifact(tmp_path)
    recorded: list = []
    order: list[str] = []
    _forbid_request_job(monkeypatch)
    executor = _wrap_job_executor_submit(monkeypatch, recorded)
    monkeypatch.setattr(_BUILDER, lambda: artifact)
    _open_bound()
    handlers.bind_rules(_allow_direct("registry_export"))
    handlers.bind_logger(MagicMock())
    update = _update()
    update.message.reply_document = AsyncMock()

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        order.append("reply")
        update._replies.append(text)

    orig_submit = WorkAdmission.submit_if_open

    def _submit(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        order.append("submit")
        return orig_submit(self, *args, **kwargs)

    monkeypatch.setattr(WorkAdmission, "submit_if_open", _submit)
    update.message.reply_text = AsyncMock(side_effect=_reply)
    asyncio.run(handlers.cmd_registry_export(update, MagicMock()))
    assert order[0] == "submit"
    assert order.index("submit") < order.index("reply")
    assert update._replies[0] == _EXPORT_START
    assert recorded and recorded[0]["executor"] is executor
    from integrations.wallet_editor_registry_db.registry_export_builder import (
        build_registry_export_from_postgres,
    )

    assert recorded[0]["fn"] is build_registry_export_from_postgres
    assert recorded[0]["args"] == ()
    assert recorded[0]["kwargs"] == {}
    update.message.reply_document.assert_awaited()


def test_isolated_auto_enable_plan_exact_callable_and_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list = []
    result = SimpleNamespace(skipped_reason=None, sent=True)
    _forbid_request_job(monkeypatch)
    executor = _wrap_job_executor_submit(monkeypatch, recorded)
    monkeypatch.setattr(_PLAN_ORCH, lambda actor, *, manual: result)
    monkeypatch.setattr(_RUN_ORCH, lambda *a, **k: (_ for _ in ()).throw(AssertionError("run")))
    _open_bound()
    handlers.bind_rules(_allow_direct("auto_enable_plan"))
    handlers.bind_logger(MagicMock())
    update = _update()
    asyncio.run(handlers.cmd_auto_enable_plan(update, MagicMock()))
    from integrations.wallet_editor_auto_enable import run_auto_enable_plan

    assert recorded[0]["executor"] is executor
    assert recorded[0]["fn"] is run_auto_enable_plan
    actor = recorded[0]["args"][0]
    assert isinstance(actor, Actor)
    assert actor.kind == "tg"
    assert actor.chat_id == 11
    assert actor.user_id == 22
    assert recorded[0]["kwargs"] == {"manual": True}
    assert update._replies[0] == _PLAN_START
    assert update._replies[-1] == "✅ Plan-only report sent=True. Antares/registry unchanged."


def test_isolated_auto_enable_run_exact_callable_and_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list = []
    result = SimpleNamespace(skipped_reason=None, phase="executed", sent=True)
    _forbid_request_job(monkeypatch)
    _wrap_job_executor_submit(monkeypatch, recorded)
    monkeypatch.setattr(_RUN_ORCH, lambda actor, *, manual: result)
    monkeypatch.setattr(_PLAN_ORCH, lambda *a, **k: (_ for _ in ()).throw(AssertionError("plan")))
    _open_bound()
    handlers.bind_rules(_allow_direct("auto_enable_run"))
    handlers.bind_logger(MagicMock())
    update = _update()
    asyncio.run(handlers.cmd_auto_enable_run(update, MagicMock()))
    from integrations.wallet_editor_auto_enable import run_auto_enable

    assert recorded[0]["fn"] is run_auto_enable
    assert recorded[0]["kwargs"] == {"manual": True}
    assert update._replies[0] == _RUN_START
    assert update._replies[-1] == "✅ Auto-Enable execution finished. Telegram report sent=True"


@pytest.mark.parametrize(
    "callback,command,start",
    [
        (handlers.cmd_registry_export, "registry_export", _EXPORT_START),
        (handlers.cmd_auto_enable_plan, "auto_enable_plan", _PLAN_START),
        (handlers.cmd_auto_enable_run, "auto_enable_run", _RUN_START),
        (handlers.cmd_registry_replay, "registry_replay", _REPLAY_START),
    ],
)
def test_isolated_direct_closed_sealed_no_start(
    monkeypatch: pytest.MonkeyPatch, callback, command, start
) -> None:
    _forbid_request_job(monkeypatch)
    business = _forbid_direct_business(monkeypatch)
    submits = _observe_used_executor_submit(monkeypatch)
    handlers.bind_rules(_allow_direct(command))
    handlers.bind_logger(MagicMock())
    closed = WorkAdmission()
    bind_antares_admission(closed)
    update = _update()
    update.message.reply_document = AsyncMock()
    asyncio.run(callback(update, MagicMock()))
    assert update._replies == [ADMISSION_CLOSED_REPLY]
    update.message.reply_document.assert_not_awaited()
    assert all(start not in text for text in update._replies)
    assert submits == []
    assert business == []

    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    submits2 = _observe_used_executor_submit(monkeypatch)
    sealed = WorkAdmission()
    bind_antares_admission(sealed)
    sealed.open()
    sealed.seal()
    update2 = _update()
    update2.message.reply_document = AsyncMock()
    asyncio.run(callback(update2, MagicMock()))
    assert update2._replies == [ADMISSION_CLOSED_REPLY]
    update2.message.reply_document.assert_not_awaited()
    assert all(start not in text for text in update2._replies)
    assert submits2 == []
    assert business == []


@pytest.mark.parametrize(
    "callback,command,start",
    [
        (handlers.cmd_registry_export, "registry_export", _EXPORT_START),
        (handlers.cmd_auto_enable_plan, "auto_enable_plan", _PLAN_START),
        (handlers.cmd_auto_enable_run, "auto_enable_run", _RUN_START),
        (handlers.cmd_registry_replay, "registry_replay", _REPLAY_START),
    ],
)
def test_isolated_direct_acl_deny_no_submit(
    monkeypatch: pytest.MonkeyPatch, callback, command, start
) -> None:
    _forbid_request_job(monkeypatch)
    business = _forbid_direct_business(monkeypatch)
    submits = _observe_used_executor_submit(monkeypatch)
    _open_bound()

    class _Empty:
        def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
            return SimpleNamespace(commands_map={}, access_map={})

    handlers.bind_rules(_Empty())
    handlers.bind_logger(MagicMock())
    update = _update()
    update.message.reply_document = AsyncMock()
    asyncio.run(callback(update, MagicMock()))
    assert update._replies == [deny_message("unknown_command", {})]
    update.message.reply_document.assert_not_awaited()
    assert submits == []
    assert business == []
    assert all(start not in text for text in update._replies)


def test_isolated_direct_submit_error_not_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "modules.antares.handlers.get_job_executor",
        lambda: SimpleNamespace(
            submit=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("executor down"))
        ),
    )
    _open_bound()
    logger = MagicMock()
    handlers.bind_rules(_allow_direct("auto_enable_plan"))
    handlers.bind_logger(logger)
    update = _update()
    asyncio.run(handlers.cmd_auto_enable_plan(update, MagicMock()))
    logger.exception.assert_any_call("isolated %s submit failed", "auto_enable_plan")
    assert any("Ошибка при постановке" in text for text in update._replies)
    assert all(_PLAN_START not in text for text in update._replies)


def test_isolated_export_reply_failure_does_not_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started = threading.Event()
    release = threading.Event()
    artifact = _export_artifact(tmp_path)

    def _build():
        started.set()
        assert release.wait(timeout=5)
        return artifact

    monkeypatch.setattr(_BUILDER, _build)
    _forbid_request_job(monkeypatch)
    _open_bound()
    logger = MagicMock()
    handlers.bind_rules(_allow_direct("registry_export"))
    handlers.bind_logger(logger)
    update = _update()
    update.message.reply_document = AsyncMock()
    boom = {"n": 0}

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("telegram down")
        update._replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)

    async def _run() -> None:
        task = asyncio.create_task(handlers.cmd_registry_export(update, MagicMock()))
        await asyncio.to_thread(started.wait, 5)
        assert started.is_set()
        release.set()
        await task

    asyncio.run(_run())
    update.message.reply_document.assert_awaited()
    logger.exception.assert_any_call("isolated %s start reply failed", "registry_export")
    assert _work_fail_logs(logger) == []


def test_isolated_plan_late_error_logged_once_no_unhandled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    release = threading.Event()

    def _plan(actor, *, manual):  # noqa: ANN001
        assert release.wait(timeout=5)
        raise RuntimeError("accepted plan boom")

    orig_watch = watch_admitted_future

    def _watch(future, logger_obj, **kwargs):  # noqa: ANN001
        admitted = orig_watch(future, logger_obj, **kwargs)
        captured["admitted"] = admitted
        return admitted

    monkeypatch.setattr(WorkAdmission, "submit_if_open", _capture_submit_if_open(captured))
    monkeypatch.setattr(_PLAN_ORCH, _plan)
    monkeypatch.setattr("modules.antares.handlers.watch_admitted_future", _watch)
    _open_bound()
    logger = MagicMock()
    handlers.bind_rules(_allow_direct("auto_enable_plan"))
    handlers.bind_logger(logger)
    update = _update()
    entered_reply = asyncio.Event()

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        if _PLAN_START in text:
            entered_reply.set()
            await asyncio.Event().wait()
        update._replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        bucket = _capture_asyncio_errors(loop)
        task = asyncio.create_task(handlers.cmd_auto_enable_plan(update, MagicMock()))
        await asyncio.wait_for(entered_reply.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        future = captured["future"]
        assert future.cancelled() is False
        release.set()
        with pytest.raises(RuntimeError, match="accepted plan boom"):
            await asyncio.to_thread(future.result, 5)
        admitted = captured.pop("admitted")
        assert admitted._done.wait(timeout=5)
        holders = [admitted, task]
        del admitted
        await _assert_no_unhandled_asyncio(loop, bucket, holders)

    asyncio.run(_run())
    assert len(_work_fail_logs(logger)) == 1
    assert all(
        not (call.args and call.args[0] == "cmd_auto_enable_plan failed")
        for call in logger.exception.call_args_list
    )


def test_isolated_direct_seal_before_handler_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_cmd = threading.Event()
    _forbid_request_job(monkeypatch)
    business = _forbid_direct_business(monkeypatch)
    submits = _observe_used_executor_submit(monkeypatch)
    admission = _open_bound()
    handlers.bind_rules(_allow_direct("auto_enable_run"))
    handlers.bind_logger(MagicMock())
    update = _update()
    update.message.reply_document = AsyncMock()
    result: dict[str, object] = {}

    def _run() -> None:
        assert start_cmd.wait(timeout=5)
        asyncio.run(handlers.cmd_auto_enable_run(update, MagicMock()))
        result["replies"] = list(update._replies)

    thread = threading.Thread(target=_run)
    thread.start()
    admission.seal()
    start_cmd.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert submits == []
    assert business == []
    assert result["replies"] == [ADMISSION_CLOSED_REPLY]
    assert all(_RUN_START not in text for text in result["replies"])
    update.message.reply_document.assert_not_awaited()


def test_unbound_direct_ops_keep_default_executor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact = _export_artifact(tmp_path)
    seen: list[object] = []
    handlers.bind_rules(_allow_direct("registry_export", "auto_enable_plan"))
    handlers.bind_logger(MagicMock())

    orig = asyncio.BaseEventLoop.run_in_executor

    def _rie(self, executor, func, *args):  # noqa: ANN001, ANN002
        seen.append(executor)
        return orig(self, executor, func, *args)

    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", _rie)
    monkeypatch.setattr(_BUILDER, lambda: artifact)
    monkeypatch.setattr(
        _PLAN_ORCH,
        lambda actor, *, manual: SimpleNamespace(skipped_reason=None, sent=True),
    )
    update = _update()
    update.message.reply_document = AsyncMock()
    asyncio.run(handlers.cmd_registry_export(update, MagicMock()))
    asyncio.run(handlers.cmd_auto_enable_plan(update, MagicMock()))
    assert seen
    assert all(item is None for item in seen)
    assert update._replies[0] == _EXPORT_START


def test_isolated_export_filename_caption_and_closed_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact = _export_artifact(tmp_path)
    monkeypatch.setattr(_BUILDER, lambda: artifact)
    monkeypatch.setattr(_FORMAT, lambda summary: "s" * 1025)
    _open_bound()
    handlers.bind_rules(_allow_direct("registry_export"))
    handlers.bind_logger(MagicMock())
    update = _update()
    captured: dict[str, object] = {}
    opened: list[object] = []
    real_open = Path.open

    def _tracking_open(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        handle = real_open(self, *args, **kwargs)
        if self == artifact.path:
            opened.append(handle)
        return handle

    async def _capture(**kwargs):  # noqa: ANN003
        document = kwargs["document"]
        captured["filename"] = document.filename
        captured["caption"] = kwargs["caption"]

    update.message.reply_document = AsyncMock(side_effect=_capture)
    with patch.object(Path, "open", _tracking_open):
        asyncio.run(handlers.cmd_registry_export(update, MagicMock()))
    assert captured["filename"] == artifact.filename
    assert captured["caption"] == "s" * 1024
    assert update._replies[-1] == "s" * 1025
    assert opened
    assert all(handle.closed for handle in opened)


def test_isolated_auto_enable_result_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_bound()
    handlers.bind_rules(_allow_direct("auto_enable_plan", "auto_enable_run"))
    handlers.bind_logger(MagicMock())
    plan_cases = [
        (SimpleNamespace(skipped_reason="disabled"), "ℹ️ Auto-Enable disabled (job_params enabled=0)."),
        (SimpleNamespace(skipped_reason="error"), "⚠️ Auto-Enable plan failed. См. route-отчёт."),
    ]
    for result, expected in plan_cases:
        update = _update()
        monkeypatch.setattr(_PLAN_ORCH, lambda actor, *, manual, _r=result: _r)
        asyncio.run(handlers.cmd_auto_enable_plan(update, MagicMock()))
        assert update._replies[-1] == expected
    run_cases = [
        (
            SimpleNamespace(skipped_reason=None, phase="plan-only"),
            "ℹ️ Execution blocked by settings (dry_run=1). Plan-only report sent.",
        ),
        (
            SimpleNamespace(skipped_reason=None, phase="executed", sent=False),
            "✅ Auto-Enable execution finished. Telegram report sent=False",
        ),
    ]
    for result, expected in run_cases:
        update = _update()
        monkeypatch.setattr(_RUN_ORCH, lambda actor, *, manual, _r=result: _r)
        asyncio.run(handlers.cmd_auto_enable_run(update, MagicMock()))
        assert update._replies[-1] == expected


def _replay_result(**overrides) -> SimpleNamespace:
    base = dict(attempted=4, synced=2, failed=1, skipped=1, errors=("e1", "e2"))
    base.update(overrides)
    return SimpleNamespace(**base)


def test_isolated_replay_open_submit_before_reply_exact_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list = []
    order: list[str] = []
    off_loop: list[bool] = []
    result = _replay_result()
    _forbid_request_job(monkeypatch)
    executor = _wrap_job_executor_submit(monkeypatch, recorded)

    def _replay():
        order.append("replay")
        off_loop.append(threading.current_thread() is not threading.main_thread())
        return result

    orig_submit = WorkAdmission.submit_if_open

    def _submit(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        order.append("submit")
        return orig_submit(self, *args, **kwargs)

    monkeypatch.setattr(WorkAdmission, "submit_if_open", _submit)
    monkeypatch.setattr(_REPLAY, _replay)
    _open_bound()
    handlers.bind_rules(_allow_direct("registry_replay"))
    handlers.bind_logger(MagicMock())
    update = _update()

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        order.append("reply")
        update._replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)
    asyncio.run(handlers.cmd_registry_replay(update, MagicMock()))
    from integrations.wallet_editor_registry import replay_pending_outbox_records

    assert order[0] == "submit"
    assert order.index("submit") < order.index("reply")
    assert "replay" in order
    assert order.index("submit") < order.index("replay")
    assert recorded[0]["executor"] is executor
    assert recorded[0]["fn"] is replay_pending_outbox_records
    assert recorded[0]["args"] == ()
    assert recorded[0]["kwargs"] == {}
    assert off_loop == [True]
    assert update._replies[0] == _REPLAY_START
    assert update._replies[-1] == (
        "Registry outbox replay\nattempted: 4\nsynced: 2\nfailed: 1\nskipped: 1\n"
        "\nerrors:\n- e1\n- e2"
    )


def test_isolated_replay_empty_errors_and_truncate(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_request_job(monkeypatch)
    _open_bound()
    handlers.bind_rules(_allow_direct("registry_replay"))
    handlers.bind_logger(MagicMock())
    empty = _replay_result(attempted=1, synced=1, failed=0, skipped=0, errors=())
    monkeypatch.setattr(_REPLAY, lambda: empty)
    update = _update()
    asyncio.run(handlers.cmd_registry_replay(update, MagicMock()))
    assert update._replies[-1] == "Registry outbox replay\nattempted: 1\nsynced: 1\nfailed: 0\nskipped: 0"
    assert "errors:" not in update._replies[-1]
    errors = tuple(f"err-{i}" for i in range(12))
    monkeypatch.setattr(_REPLAY, lambda: _replay_result(attempted=12, synced=0, failed=12, skipped=0, errors=errors))
    update2 = _update()
    asyncio.run(handlers.cmd_registry_replay(update2, MagicMock()))
    body = update2._replies[-1]
    assert "- err-0" in body
    assert "- err-9" in body
    assert "- err-10" not in body
    assert body.count("- err-") == 10


def test_isolated_replay_submit_error_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "modules.antares.handlers.get_job_executor",
        lambda: SimpleNamespace(
            submit=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("executor down"))
        ),
    )
    _forbid_request_job(monkeypatch)
    business = []
    monkeypatch.setattr(_REPLAY, lambda: business.append("replay") or (_ for _ in ()).throw(AssertionError()))
    _open_bound()
    logger = MagicMock()
    handlers.bind_rules(_allow_direct("registry_replay"))
    handlers.bind_logger(logger)
    update = _update()
    asyncio.run(handlers.cmd_registry_replay(update, MagicMock()))
    logger.exception.assert_any_call("isolated %s submit failed", "registry_replay")
    assert any("Ошибка при постановке" in text for text in update._replies)
    assert all(_REPLAY_START not in text for text in update._replies)
    assert business == []


def test_isolated_replay_finishes_after_seal(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()
    result = _replay_result()

    def _replay():
        started.set()
        assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(_REPLAY, _replay)
    _forbid_request_job(monkeypatch)
    admission = _open_bound()
    handlers.bind_rules(_allow_direct("registry_replay"))
    handlers.bind_logger(MagicMock())
    update = _update()

    async def _run() -> None:
        task = asyncio.create_task(handlers.cmd_registry_replay(update, MagicMock()))
        await asyncio.to_thread(started.wait, 5)
        admission.seal()
        release.set()
        await task

    asyncio.run(_run())
    assert started.is_set()
    assert admission.state is AdmissionState.SEALED
    assert update._replies[0] == _REPLAY_START
    assert "attempted: 4" in update._replies[-1]


def test_isolated_replay_seal_before_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    start_cmd = threading.Event()
    _forbid_request_job(monkeypatch)
    business = _forbid_direct_business(monkeypatch)
    submits = _observe_used_executor_submit(monkeypatch)
    admission = _open_bound()
    handlers.bind_rules(_allow_direct("registry_replay"))
    handlers.bind_logger(MagicMock())
    update = _update()
    result: dict[str, object] = {}

    def _run() -> None:
        assert start_cmd.wait(timeout=5)
        asyncio.run(handlers.cmd_registry_replay(update, MagicMock()))
        result["replies"] = list(update._replies)

    thread = threading.Thread(target=_run)
    thread.start()
    admission.seal()
    start_cmd.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert submits == []
    assert business == []
    assert result["replies"] == [ADMISSION_CLOSED_REPLY]


def test_isolated_replay_reply_cancel_observes_late_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    release = threading.Event()

    def _replay():
        assert release.wait(timeout=5)
        raise RuntimeError("accepted replay boom")

    orig_watch = watch_admitted_future

    def _watch(future, logger_obj, **kwargs):  # noqa: ANN001
        admitted = orig_watch(future, logger_obj, **kwargs)
        captured["admitted"] = admitted
        return admitted

    monkeypatch.setattr(WorkAdmission, "submit_if_open", _capture_submit_if_open(captured))
    monkeypatch.setattr(_REPLAY, _replay)
    monkeypatch.setattr("modules.antares.handlers.watch_admitted_future", _watch)
    _forbid_request_job(monkeypatch)
    _open_bound()
    logger = MagicMock()
    handlers.bind_rules(_allow_direct("registry_replay"))
    handlers.bind_logger(logger)
    update = _update()
    entered_reply = asyncio.Event()

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        if _REPLAY_START in text:
            entered_reply.set()
            await asyncio.Event().wait()
        update._replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        bucket = _capture_asyncio_errors(loop)
        task = asyncio.create_task(handlers.cmd_registry_replay(update, MagicMock()))
        await asyncio.wait_for(entered_reply.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        future = captured["future"]
        assert future.cancelled() is False
        release.set()
        with pytest.raises(RuntimeError, match="accepted replay boom"):
            await asyncio.to_thread(future.result, 5)
        admitted = captured.pop("admitted")
        assert admitted._done.wait(timeout=5)
        holders = [admitted, task]
        del admitted
        await _assert_no_unhandled_asyncio(loop, bucket, holders)

    asyncio.run(_run())
    assert len(_work_fail_logs(logger)) == 1
    assert all(
        not (call.args and call.args[0] == "cmd_registry_replay failed")
        for call in logger.exception.call_args_list
    )


def test_unbound_replay_stays_on_loop_without_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list = []
    order: list[str] = []
    off_loop: list[bool] = []
    result = _replay_result()
    executor = _wrap_job_executor_submit(monkeypatch, recorded)

    def _replay():
        order.append("replay")
        off_loop.append(threading.current_thread() is not threading.main_thread())
        return result

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        order.append("reply")
        update._replies.append(text)

    monkeypatch.setattr(_REPLAY, _replay)
    handlers.bind_rules(_allow_direct("registry_replay"))
    handlers.bind_logger(MagicMock())
    update = _update()
    update.message.reply_text = AsyncMock(side_effect=_reply)
    asyncio.run(handlers.cmd_registry_replay(update, MagicMock()))
    assert recorded == []
    assert executor is get_job_executor()
    assert off_loop == [False]
    assert order[0] == "reply"
    assert order[1] == "replay"
    assert order[2] == "reply"
    assert update._replies[0] == _REPLAY_START
    assert "attempted: 4" in update._replies[-1]


_RELOAD_OK = "♻️ rules snapshot перечитан.\nsource: snapshot_v2:test"
_RELOAD_FAIL = "xlsx boom"


def _reload_v2(*, version: str, updated_at: datetime, allow_run_wallet: bool) -> RulesSnapshotV2:
    roles = {"level_1": RoleDef(role_key="level_1", role_level=1, display_name="L1")}
    commands = {
        "reload_rules": CommandDef(
            command_key="reload_rules",
            command_text="reload_rules",
            job_key=None,
            display_name="reload_rules",
            enabled=True,
        ),
    }
    policies = {
        "reload_rules": CommandPolicy(
            command_key="reload_rules",
            min_role_key="level_1",
            allow_private=True,
            allow_groups=True,
            enabled=True,
        ),
    }
    if allow_run_wallet:
        commands["run_wallet"] = CommandDef(
            command_key="run_wallet",
            command_text="run_wallet",
            job_key=None,
            display_name="run_wallet",
            enabled=True,
        )
        policies["run_wallet"] = CommandPolicy(
            command_key="run_wallet",
            min_role_key="level_1",
            allow_private=True,
            allow_groups=True,
            enabled=True,
        )
    return RulesSnapshotV2(
        meta=MetaInfo(ruleset_version=version, updated_at=updated_at, updated_by="test"),
        roles=roles,
        commands=commands,
        command_policies=policies,
        access_rules=[
            AccessRule(chat_id="private", user_id="22", role_key="level_1", enabled=True),
        ],
    )


def test_access_rules_and_provider_have_no_cache_lock() -> None:
    import core.access_rules as access_rules
    import core.rules_provider as rules_provider

    rules_src = inspect.getsource(access_rules.AccessRules)
    provider_src = inspect.getsource(rules_provider)
    invalidate_src = inspect.getsource(access_rules.AccessRules.invalidate)
    assert "Lock" not in rules_src
    assert "RLock" not in rules_src
    assert "threading.Lock" not in provider_src
    assert "RLock" not in provider_src
    assert "invalidate_rules_v2_cache" not in invalidate_src


def test_isolated_reload_closed_sealed_no_mutate(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_request_job(monkeypatch)
    business = _forbid_direct_business(monkeypatch)
    submits = _observe_used_executor_submit(monkeypatch)
    rules = _allow_direct("reload_rules")
    handlers.bind_rules(rules)
    handlers.bind_logger(MagicMock())
    closed = WorkAdmission()
    bind_antares_admission(closed)
    update = _update()
    asyncio.run(handlers.cmd_reload_rules(update, MagicMock()))
    assert update._replies == [ADMISSION_CLOSED_REPLY]
    assert True not in rules.snapshot_force
    assert rules.invalidate_calls == 0
    assert submits == []
    assert business == []
    assert all("Запускаю" not in text for text in update._replies)
    assert all("♻️" not in text for text in update._replies)

    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    submits2 = _observe_used_executor_submit(monkeypatch)
    sealed = WorkAdmission()
    bind_antares_admission(sealed)
    sealed.open()
    sealed.seal()
    update2 = _update()
    asyncio.run(handlers.cmd_reload_rules(update2, MagicMock()))
    assert update2._replies == [ADMISSION_CLOSED_REPLY]
    assert True not in rules.snapshot_force
    assert rules.invalidate_calls == 0
    assert submits2 == []
    assert business == []


def test_isolated_reload_acl_deny_snapshot_is_not_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_request_job(monkeypatch)
    business = _forbid_direct_business(monkeypatch)
    submits = _observe_used_executor_submit(monkeypatch)
    _open_bound()

    class _Empty:
        def __init__(self) -> None:
            self.invalidate_calls = 0
            self.snapshot_force: list[bool] = []

        def invalidate(self) -> None:
            self.invalidate_calls += 1
            raise AssertionError("invalidate must not run on ACL deny")

        def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
            self.snapshot_force.append(force_sync)
            return SimpleNamespace(commands_map={}, access_map={})

    rules = _Empty()
    handlers.bind_rules(rules)
    handlers.bind_logger(MagicMock())
    update = _update()
    asyncio.run(handlers.cmd_reload_rules(update, MagicMock()))
    assert update._replies == [deny_message("unknown_command", {})]
    assert submits == []
    assert business == []
    assert rules.invalidate_calls == 0
    assert True not in rules.snapshot_force
    assert False in rules.snapshot_force
    assert all("♻️" not in text for text in update._replies)


def test_isolated_reload_submit_error_does_not_mutate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "modules.antares.handlers.get_job_executor",
        lambda: SimpleNamespace(
            submit=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("executor down"))
        ),
    )
    _forbid_request_job(monkeypatch)
    clocks: list[str] = []

    def _clocks(*args, **kwargs):  # noqa: ANN002, ANN003
        clocks.append("reset")
        raise AssertionError("reset must not run when submit fails")

    monkeypatch.setattr("core.scheduler_clocks_control.request_scheduler_clocks_reset", _clocks)
    _open_bound()
    logger = MagicMock()
    rules = _allow_direct("reload_rules")
    handlers.bind_rules(rules)
    handlers.bind_logger(logger)
    update = _update()
    asyncio.run(handlers.cmd_reload_rules(update, MagicMock()))
    logger.exception.assert_any_call("isolated %s submit failed", "reload_rules")
    assert any("Ошибка при постановке" in text for text in update._replies)
    assert rules.invalidate_calls == 0
    assert True not in rules.snapshot_force
    assert clocks == []
    assert all("Запускаю" not in text for text in update._replies)


def test_isolated_reload_open_order_identity_no_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list = []
    order: list[object] = []
    off_loop: list[bool] = []
    _forbid_request_job(monkeypatch)
    executor = _wrap_job_executor_submit(monkeypatch, recorded)

    def _clocks(*, reason: str = "manual_reload") -> None:
        order.append(("reset", reason))
        off_loop.append(threading.current_thread() is not threading.main_thread())

    orig_submit = WorkAdmission.submit_if_open

    def _submit(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        order.append("submit")
        return orig_submit(self, *args, **kwargs)

    class _Rules:
        def __init__(self) -> None:
            self.invalidate_calls = 0

        def invalidate(self) -> None:
            self.invalidate_calls += 1
            order.append("invalidate")
            off_loop.append(threading.current_thread() is not threading.main_thread())

        def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
            order.append(("snapshot", force_sync))
            return SimpleNamespace(
                commands_map={
                    "reload_rules": CommandRule(
                        required_level=1,
                        allow_private=True,
                        allow_groups=True,
                        enabled=True,
                    )
                },
                access_map={("private", 22): 1, (11, 22): 1},
                source="snapshot_v2:test",
            )

    rules = _Rules()
    monkeypatch.setattr(WorkAdmission, "submit_if_open", _submit)
    monkeypatch.setattr("core.scheduler_clocks_control.request_scheduler_clocks_reset", _clocks)
    _open_bound()
    handlers.bind_rules(rules)
    handlers.bind_logger(MagicMock())
    update = _update()

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        order.append("reply")
        update._replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)
    asyncio.run(handlers.cmd_reload_rules(update, MagicMock()))
    assert order[0] == ("snapshot", False)
    assert order.index("submit") < order.index("invalidate")
    assert order.index("submit") < order.index("reply")
    assert order[order.index("invalidate") : order.index("invalidate") + 3] == [
        "invalidate",
        ("snapshot", True),
        ("reset", "reload_rules"),
    ]
    assert recorded[0]["executor"] is executor
    assert recorded[0]["fn"] is handlers._reload_bound_rules
    assert recorded[0]["args"] == (rules,)
    assert recorded[0]["kwargs"] == {}
    assert handlers._rules is rules
    assert True in off_loop
    assert update._replies == [_RELOAD_OK]
    assert all("Запускаю" not in text for text in update._replies)


def test_isolated_reload_snapshot_failure_skips_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clocks: list[str] = []

    def _clocks(*args, **kwargs):  # noqa: ANN002, ANN003
        clocks.append("reset")

    class _Rules:
        def __init__(self) -> None:
            self.invalidate_calls = 0

        def invalidate(self) -> None:
            self.invalidate_calls += 1

        def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
            if force_sync:
                raise RuntimeError(_RELOAD_FAIL)
            return SimpleNamespace(
                commands_map={
                    "reload_rules": CommandRule(
                        required_level=1,
                        allow_private=True,
                        allow_groups=True,
                        enabled=True,
                    )
                },
                access_map={("private", 22): 1, (11, 22): 1},
                source="snapshot_v2:old",
            )

    monkeypatch.setattr("core.scheduler_clocks_control.request_scheduler_clocks_reset", _clocks)
    _forbid_request_job(monkeypatch)
    _open_bound()
    logger = MagicMock()
    rules = _Rules()
    handlers.bind_rules(rules)
    handlers.bind_logger(logger)
    update = _update()
    asyncio.run(handlers.cmd_reload_rules(update, MagicMock()))
    assert rules.invalidate_calls == 1
    assert clocks == []
    assert update._replies == [f"⚠️ Не смог перечитать rules.xlsx: {_RELOAD_FAIL}"]
    assert len(_work_fail_logs(logger)) == 1


def test_isolated_reload_finishes_after_seal(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    class _Rules:
        def invalidate(self) -> None:
            started.set()
            assert release.wait(timeout=5)

        def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
            return SimpleNamespace(
                commands_map={
                    "reload_rules": CommandRule(
                        required_level=1,
                        allow_private=True,
                        allow_groups=True,
                        enabled=True,
                    )
                },
                access_map={("private", 22): 1, (11, 22): 1},
                source="snapshot_v2:test",
            )

    _forbid_request_job(monkeypatch)
    admission = _open_bound()
    handlers.bind_rules(_Rules())
    handlers.bind_logger(MagicMock())
    update = _update()

    async def _run() -> None:
        task = asyncio.create_task(handlers.cmd_reload_rules(update, MagicMock()))
        await asyncio.to_thread(started.wait, 5)
        admission.seal()
        release.set()
        await task

    asyncio.run(_run())
    assert started.is_set()
    assert admission.state is AdmissionState.SEALED
    assert update._replies == [_RELOAD_OK]


def test_isolated_reload_seal_before_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    start_cmd = threading.Event()
    _forbid_request_job(monkeypatch)
    business = _forbid_direct_business(monkeypatch)
    submits = _observe_used_executor_submit(monkeypatch)
    admission = _open_bound()
    rules = _allow_direct("reload_rules")
    handlers.bind_rules(rules)
    handlers.bind_logger(MagicMock())
    update = _update()
    result: dict[str, object] = {}

    def _run() -> None:
        assert start_cmd.wait(timeout=5)
        asyncio.run(handlers.cmd_reload_rules(update, MagicMock()))
        result["replies"] = list(update._replies)

    thread = threading.Thread(target=_run)
    thread.start()
    admission.seal()
    start_cmd.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert submits == []
    assert business == []
    assert rules.invalidate_calls == 0
    assert result["replies"] == [ADMISSION_CLOSED_REPLY]


def test_isolated_reload_wait_cancel_observes_late_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    started = threading.Event()
    release = threading.Event()

    class _Rules:
        def invalidate(self) -> None:
            started.set()
            assert release.wait(timeout=5)
            raise RuntimeError("accepted reload boom")

        def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
            return SimpleNamespace(
                commands_map={
                    "reload_rules": CommandRule(
                        required_level=1,
                        allow_private=True,
                        allow_groups=True,
                        enabled=True,
                    )
                },
                access_map={("private", 22): 1, (11, 22): 1},
                source="snapshot_v2:test",
            )

    orig_watch = watch_admitted_future

    def _watch(future, logger_obj, **kwargs):  # noqa: ANN001
        admitted = orig_watch(future, logger_obj, **kwargs)
        captured["admitted"] = admitted
        return admitted

    monkeypatch.setattr(WorkAdmission, "submit_if_open", _capture_submit_if_open(captured))
    monkeypatch.setattr("modules.antares.handlers.watch_admitted_future", _watch)
    _forbid_request_job(monkeypatch)
    _open_bound()
    logger = MagicMock()
    handlers.bind_rules(_Rules())
    handlers.bind_logger(logger)
    update = _update()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        bucket = _capture_asyncio_errors(loop)
        task = asyncio.create_task(handlers.cmd_reload_rules(update, MagicMock()))
        await asyncio.to_thread(started.wait, 5)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        future = captured["future"]
        assert future.cancelled() is False
        release.set()
        with pytest.raises(RuntimeError, match="accepted reload boom"):
            await asyncio.to_thread(future.result, 5)
        admitted = captured.pop("admitted")
        assert admitted._done.wait(timeout=5)
        holders = [admitted, task]
        del admitted
        await _assert_no_unhandled_asyncio(loop, bucket, holders)

    asyncio.run(_run())
    assert len(_work_fail_logs(logger)) == 1
    assert all(
        not (call.args and call.args[0] == "cmd_reload_rules failed")
        for call in logger.exception.call_args_list
    )


def test_unbound_reload_stays_on_loop_without_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list = []
    order: list[object] = []
    off_loop: list[bool] = []
    executor = _wrap_job_executor_submit(monkeypatch, recorded)

    def _clocks(*, reason: str = "manual_reload") -> None:
        order.append(("reset", reason))
        off_loop.append(threading.current_thread() is not threading.main_thread())

    class _Rules:
        def invalidate(self) -> None:
            order.append("invalidate")
            off_loop.append(threading.current_thread() is not threading.main_thread())

        def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
            order.append(("snapshot", force_sync))
            return SimpleNamespace(
                commands_map={
                    "reload_rules": CommandRule(
                        required_level=1,
                        allow_private=True,
                        allow_groups=True,
                        enabled=True,
                    )
                },
                access_map={("private", 22): 1, (11, 22): 1},
                source="snapshot_v2:test",
            )

    monkeypatch.setattr("core.scheduler_clocks_control.request_scheduler_clocks_reset", _clocks)
    handlers.bind_rules(_Rules())
    handlers.bind_logger(MagicMock())
    update = _update()

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        order.append("reply")
        update._replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)
    asyncio.run(handlers.cmd_reload_rules(update, MagicMock()))
    assert recorded == []
    assert executor is get_job_executor()
    assert off_loop == [False, False]
    assert order == [
        ("snapshot", False),
        "invalidate",
        ("snapshot", True),
        ("reset", "reload_rules"),
        "reply",
    ]
    assert update._replies == [_RELOAD_OK]
    assert all("Запускаю" not in text for text in update._replies)


def test_isolated_reload_real_access_rules_next_caller_sees_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deny_v2 = _reload_v2(
        version="deny",
        updated_at=datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
        allow_run_wallet=False,
    )
    allow_v2 = _reload_v2(
        version="allow",
        updated_at=datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc),
        allow_run_wallet=True,
    )
    source = {"current": deny_v2}

    def _load_v2(*, force_sync: bool = False) -> RulesSnapshotV2:
        return source["current"]

    monkeypatch.setattr("core.access_rules.get_snapshot_v2", _load_v2)
    _forbid_request_job(monkeypatch)
    _open_bound()
    rules = AccessRules()
    handlers.bind_rules(rules)
    handlers.bind_logger(MagicMock())
    clocks: list[str] = []
    monkeypatch.setattr(
        "core.scheduler_clocks_control.request_scheduler_clocks_reset",
        lambda *, reason="manual_reload": clocks.append(reason),
    )
    before = rules.get_snapshot()
    assert "run_wallet" not in before.commands_map
    source["current"] = allow_v2
    update = _update()
    asyncio.run(handlers.cmd_reload_rules(update, MagicMock()))
    assert handlers._rules is rules
    after = rules.get_snapshot()
    assert after is not before
    assert "run_wallet" in after.commands_map
    assert after.source == "snapshot_v2:allow"
    assert clocks == ["reload_rules"]
    assert update._replies == ["♻️ rules snapshot перечитан.\nsource: snapshot_v2:allow"]


def test_isolated_reload_overlap_is_not_a_thread_safety_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overlapping get_snapshot vs force reload can last-write-win on AccessRules._snap.

    Passing this test does not mean AccessRules or rules_provider are thread-safe.
    """
    v1 = _reload_v2(
        version="v1",
        updated_at=datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
        allow_run_wallet=False,
    )
    v2 = _reload_v2(
        version="v2",
        updated_at=datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc),
        allow_run_wallet=True,
    )
    entered = threading.Event()
    release = threading.Event()

    def _load_v2(*, force_sync: bool = False) -> RulesSnapshotV2:
        if force_sync:
            entered.set()
            assert release.wait(timeout=5)
            return v2
        return v1

    monkeypatch.setattr("core.access_rules.get_snapshot_v2", _load_v2)
    _forbid_request_job(monkeypatch)
    _open_bound()
    rules = AccessRules()
    handlers.bind_rules(rules)
    handlers.bind_logger(MagicMock())
    monkeypatch.setattr(
        "core.scheduler_clocks_control.request_scheduler_clocks_reset",
        lambda **kwargs: None,
    )
    update = _update()
    overlap: dict[str, object] = {}

    def _reader() -> None:
        assert entered.wait(timeout=5)
        overlap["mid"] = rules.get_snapshot().source
        release.set()

    reader = threading.Thread(target=_reader)
    reader.start()
    asyncio.run(handlers.cmd_reload_rules(update, MagicMock()))
    reader.join(timeout=5)
    assert not reader.is_alive()
    final = rules.get_snapshot().source
    assert final in {"snapshot_v2:v1", "snapshot_v2:v2"}
    assert overlap["mid"] in {"snapshot_v2:v1", "snapshot_v2:v2"}
    assert handlers._rules is rules
