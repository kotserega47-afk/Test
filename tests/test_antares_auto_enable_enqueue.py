"""TASK-38 isolated Auto-Enable enqueue continuation (E1–E15)."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from automation.worker import enqueue_auto_enable_batch
from core.access_rules import CommandRule
from core.job_dispatch import _reset_job_executor_for_tests, get_job_executor
from integrations.wallet_editor_auto_enable_eligibility import CandidateRow
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings
from modules.antares import handlers
from modules.antares.auto_enable_continuation import (
    IsolatedAutoEnableEnqueueRejected,
    bind_thread_continuation,
    current_auto_enable_continuation,
)
from modules.antares.work_admission import (
    AdmissionAccepted,
    AdmissionRejected,
    WorkAdmission,
    bind_antares_admission,
    reset_antares_admission_for_tests,
    watch_admitted_future,
)


class _EndWorkerLoop(Exception):
    """Test harness: unwind production worker_loop after queued items."""


class _HarnessQueue(Queue):
    _END = object()

    def end_loop(self) -> None:
        Queue.put(self, self._END)

    def get(self, block=True, timeout=None):
        item = Queue.get(self, block=block, timeout=timeout)
        if item is self._END:
            Queue.task_done(self)
            raise _EndWorkerLoop()
        return item


def _settings() -> AutoEnableSettings:
    return AutoEnableSettings(
        enabled=True,
        dry_run=False,
        approval_required=False,
        max_rows_per_batch=200,
        max_rows_per_run=0,
        seconds_per_card_timeout=10,
        batch_timeout_buffer_seconds=300,
        working_statuses=("готов к работе",),
        auto_return_statuses=(),
        auto_return_target_status="Готов к работе",
        allowed_statuses_for_enable=("готов к работе",),
        deprecated_working_statuses_fallback=False,
        include_overdue=True,
        telegram_route_report="wallet_editor_auto_enable",
        telegram_route_alert="wallet_editor_auto_enable_alert",
    )


def _candidate() -> CandidateRow:
    return CandidateRow(
        card="4111111111111111",
        partner="P1",
        disable_at="2026-01-01",
        enable_status="К ВКЛЮЧЕНИЮ",
        vklyucheno="",
        source_row_index=0,
    )


def _allow_run() -> SimpleNamespace:
    rules = SimpleNamespace(
        commands_map={
            "auto_enable_run": CommandRule(
                required_level=1,
                allow_private=True,
                allow_groups=True,
                enabled=True,
            )
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
    update._replies: list[str] = []

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        update._replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)
    return update


def _instrument_future(fut: Future, counts: dict[str, int]) -> None:
    orig_r = fut.set_result
    orig_e = fut.set_exception

    def set_result(result):  # noqa: ANN001
        counts["set_result"] += 1
        try:
            orig_r(result)
        except Exception:
            counts["invalid"] += 1
            raise

    def set_exception(exc):  # noqa: ANN001
        counts["set_exception"] += 1
        try:
            orig_e(exc)
        except Exception:
            counts["invalid"] += 1
            raise

    fut.set_result = set_result  # type: ignore[method-assign]
    fut.set_exception = set_exception  # type: ignore[method-assign]


def _wait_queue_idle(queue: Queue, extra_threads: list, timeout: float = 5) -> None:
    finished = threading.Event()
    errors: list[BaseException] = []

    def _join() -> None:
        try:
            queue.join()
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=_join, name="ae-queue-join")
    extra_threads.append(thread)
    thread.start()
    assert finished.wait(timeout=timeout)
    thread.join(timeout=timeout)
    assert not thread.is_alive()
    if errors:
        raise errors[0]


def _open_bound() -> WorkAdmission:
    admission = WorkAdmission()
    bind_antares_admission(admission)
    admission.open()
    return admission


def _block_ensure_put(monkeypatch: pytest.MonkeyPatch, worker_mod) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        worker_mod,
        "_ensure_profile_worker",
        lambda profile: calls.append("ensure")
        or (_ for _ in ()).throw(AssertionError("ensure")),
    )
    monkeypatch.setattr(
        worker_mod.Queue,
        "put",
        lambda *a, **k: calls.append("put") or (_ for _ in ()).throw(AssertionError("put")),
    )
    return calls


@pytest.fixture
def isolated_runtime(monkeypatch: pytest.MonkeyPatch):
    import automation.worker as worker_mod

    prev_rules = handlers._rules
    prev_logger = handlers._logger
    orig_registry = worker_mod._profile_workers
    test_registry: dict = {}
    worker_mod._profile_workers = test_registry
    monkeypatch.setattr(worker_mod, "Queue", _HarnessQueue)
    orig_loop = worker_mod.worker_loop

    def _harness_worker_loop(profile_key, task_queue):  # noqa: ANN001
        try:
            orig_loop(profile_key, task_queue)
        except _EndWorkerLoop:
            return

    monkeypatch.setattr(worker_mod, "worker_loop", _harness_worker_loop)
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    monkeypatch.setattr(
        "integrations.wallet_editor_auto_enable_executor.build_run_config_from_conversion_env",
        lambda: SimpleNamespace(
            login="l",
            password="p",
            auth_state_path="/tmp/auth_state_wallet_editor_CONVERSION_AUTO.json",
        ),
    )
    monkeypatch.setattr(
        "automation.runtime.require_wallet_editor_antares_credentials",
        lambda cfg: None,
    )
    monkeypatch.setattr(
        "integrations.wallet_editor_auto_enable_executor.execute_enable_batch",
        lambda *a, **k: [],
    )
    extra_threads: list[threading.Thread] = []
    counts = {"set_result": 0, "set_exception": 0, "invalid": 0}
    orig_put = _HarnessQueue.put

    def _put(self, item):  # noqa: ANN001
        if item is not _HarnessQueue._END and hasattr(item, "result_future"):
            _instrument_future(item.result_future, counts)
        return orig_put(self, item)

    monkeypatch.setattr(worker_mod.Queue, "put", _put)
    runtime = SimpleNamespace(
        worker_mod=worker_mod,
        registry=test_registry,
        extra_threads=extra_threads,
        counts=counts,
    )
    try:
        yield runtime
    finally:
        join_errors: list[BaseException] = []
        workers = list(test_registry.values())
        for worker in workers:
            queue = worker.queue
            if hasattr(queue, "end_loop"):
                try:
                    queue.end_loop()
                except BaseException as exc:
                    join_errors.append(exc)
        for worker in workers:
            thread = worker.thread
            if thread is not None:
                thread.join(timeout=5)
                if thread.is_alive():
                    join_errors.append(RuntimeError(f"worker still alive: {thread.name}"))
        for thread in extra_threads:
            thread.join(timeout=5)
            if thread.is_alive():
                join_errors.append(RuntimeError(f"test thread still alive: {thread.name}"))
        worker_mod._profile_workers = orig_registry
        handlers._rules = prev_rules
        handlers._logger = prev_logger
        reset_antares_admission_for_tests()
        _reset_job_executor_for_tests()
        if join_errors:
            raise join_errors[0]


def test_e1_seal_before_new_run(isolated_runtime) -> None:
    admission = _open_bound()
    admission.seal()
    called = []

    def _run():
        called.append(True)

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    assert isinstance(outcome, AdmissionRejected)
    assert admission._ae_continuations == {}
    assert called == []


def test_e2_accepted_then_seal_before_enqueue(isolated_runtime) -> None:
    admission = _open_bound()
    started = threading.Event()
    release = threading.Event()

    def _run():
        started.set()
        assert release.wait(timeout=5)
        return enqueue_auto_enable_batch([_candidate()], _settings())

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    assert isinstance(outcome, AdmissionAccepted)
    assert started.wait(timeout=5)
    admission.seal()
    assert isinstance(
        admission.submit_auto_enable_run_if_open(get_job_executor(), lambda: None),
        AdmissionRejected,
    )
    release.set()
    assert outcome.future.result(timeout=5) == []
    worker = next(iter(isolated_runtime.registry.values()))
    _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)


def test_e3_n_batches_one_run(isolated_runtime) -> None:
    admission = _open_bound()

    def _run():
        first = enqueue_auto_enable_batch([_candidate()], _settings())
        second = enqueue_auto_enable_batch([_candidate()], _settings())
        return first, second

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    assert outcome.future.result(timeout=5) == ([], [])
    assert admission._ae_continuations == {}
    worker = next(iter(isolated_runtime.registry.values()))
    _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)


def test_e3b_batches_after_seal(isolated_runtime) -> None:
    admission = _open_bound()
    started = threading.Event()
    release = threading.Event()

    def _run():
        started.set()
        assert release.wait(timeout=5)
        return (
            enqueue_auto_enable_batch([_candidate()], _settings()),
            enqueue_auto_enable_batch([_candidate()], _settings()),
        )

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    assert started.wait(timeout=5)
    admission.seal()
    release.set()
    assert outcome.future.result(timeout=5) == ([], [])
    worker = next(iter(isolated_runtime.registry.values()))
    _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)


def test_e4_error_before_put(isolated_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    admission = _open_bound()
    monkeypatch.setattr(
        "automation.runtime.require_wallet_editor_antares_credentials",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("creds")),
    )
    calls = _block_ensure_put(monkeypatch, isolated_runtime.worker_mod)

    def _run():
        enqueue_auto_enable_batch([_candidate()], _settings())

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    with pytest.raises(RuntimeError, match="creds"):
        outcome.future.result(timeout=5)
    assert calls == []
    assert admission._ae_continuations == {}
    assert isolated_runtime.registry == {}


def test_e5_put_ok_execute_set_exception(isolated_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    admission = _open_bound()
    monkeypatch.setattr(
        "integrations.wallet_editor_auto_enable_executor.execute_enable_batch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("execute boom")),
    )

    def _run():
        return enqueue_auto_enable_batch([_candidate()], _settings())

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    with pytest.raises(RuntimeError, match="execute boom"):
        outcome.future.result(timeout=5)
    worker = next(iter(isolated_runtime.registry.values()))
    _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)
    assert isolated_runtime.counts["set_exception"] == 1
    assert isolated_runtime.counts["set_result"] == 0
    assert isolated_runtime.counts["invalid"] == 0


def test_e6_isolated_direct_enqueue_without_continuation(
    isolated_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open_bound()
    calls = _block_ensure_put(monkeypatch, isolated_runtime.worker_mod)
    with pytest.raises(IsolatedAutoEnableEnqueueRejected):
        enqueue_auto_enable_batch([_candidate()], _settings())
    assert calls == []
    assert isolated_runtime.registry == {}


def test_e6_foreign_admission_instance(isolated_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    bound = _open_bound()
    other = WorkAdmission()
    other._bind_instance()
    other.open()
    calls = _block_ensure_put(monkeypatch, isolated_runtime.worker_mod)
    started = threading.Event()
    release = threading.Event()

    def _run():
        started.set()
        assert release.wait(timeout=5)
        enqueue_auto_enable_batch([_candidate()], _settings())

    outcome = other.submit_auto_enable_run_if_open(get_job_executor(), _run)
    assert started.wait(timeout=5)
    assert bound is not other
    release.set()
    with pytest.raises(IsolatedAutoEnableEnqueueRejected):
        outcome.future.result(timeout=5)
    assert calls == []
    assert isolated_runtime.registry == {}


def test_e6_revoked_record_replay(isolated_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    admission = _open_bound()
    stolen: dict[str, object] = {}

    def _run():
        stolen["record"] = current_auto_enable_continuation()

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    outcome.future.result(timeout=5)
    record = stolen["record"]
    assert record is not None
    assert record.state == "revoked"
    calls = _block_ensure_put(monkeypatch, isolated_runtime.worker_mod)
    bind_thread_continuation(record)
    try:
        with pytest.raises(IsolatedAutoEnableEnqueueRejected):
            enqueue_auto_enable_batch([_candidate()], _settings())
        assert calls == []
    finally:
        from modules.antares.auto_enable_continuation import clear_thread_continuation

        clear_thread_continuation()


def test_e6_foreign_thread(isolated_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    admission = _open_bound()
    calls = _block_ensure_put(monkeypatch, isolated_runtime.worker_mod)
    started = threading.Event()
    release = threading.Event()
    stolen: dict[str, object] = {}

    def _run():
        stolen["record"] = current_auto_enable_continuation()
        started.set()
        assert release.wait(timeout=5)

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    assert started.wait(timeout=5)
    foreign_errors: list[BaseException] = []

    def _foreign() -> None:
        try:
            bind_thread_continuation(stolen["record"])
            enqueue_auto_enable_batch([_candidate()], _settings())
        except BaseException as exc:
            foreign_errors.append(exc)

    foreign = threading.Thread(target=_foreign, name="ae-foreign")
    isolated_runtime.extra_threads.append(foreign)
    try:
        foreign.start()
        foreign.join(timeout=5)
        assert not foreign.is_alive()
        assert foreign_errors and isinstance(
            foreign_errors[0], IsolatedAutoEnableEnqueueRejected
        )
        assert calls == []
    finally:
        release.set()
        outcome.future.result(timeout=5)


def test_e7_seal_during_result_wait(isolated_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    admission = _open_bound()
    waiting = threading.Event()
    release_batch = threading.Event()
    errors: list[BaseException] = []

    def _slow_execute(*args, **kwargs):  # noqa: ANN002, ANN003
        waiting.set()
        assert release_batch.wait(timeout=5)
        return []

    monkeypatch.setattr(
        "integrations.wallet_editor_auto_enable_executor.execute_enable_batch",
        _slow_execute,
    )

    def _run():
        return enqueue_auto_enable_batch([_candidate()], _settings())

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    assert waiting.wait(timeout=5)
    held = threading.Event()

    def _seal() -> None:
        try:
            admission.seal()
        except BaseException as exc:
            errors.append(exc)
        finally:
            held.set()

    sealer = threading.Thread(target=_seal, name="ae-seal")
    isolated_runtime.extra_threads.append(sealer)
    try:
        sealer.start()
        assert held.wait(timeout=5)
        assert admission.state.value == "sealed"
        assert admission._lock.acquire(blocking=False)
        admission._lock.release()
        release_batch.set()
        assert outcome.future.result(timeout=5) == []
        worker = next(iter(isolated_runtime.registry.values()))
        _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)
    finally:
        release_batch.set()
        if errors:
            raise errors[0]


def test_e8_unbound_mixed_enqueue_unchanged(isolated_runtime) -> None:
    reset_antares_admission_for_tests()
    assert enqueue_auto_enable_batch([_candidate()], _settings()) == []
    worker = next(iter(isolated_runtime.registry.values()))
    _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)


def test_e9_callable_starts_before_submit_returns(
    isolated_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission = _open_bound()
    body_started = threading.Event()
    release = threading.Event()
    entered_activate = threading.Event()
    orig_activate = WorkAdmission.activate_auto_enable_continuation

    def _activate(self, token):  # noqa: ANN001
        entered_activate.set()
        return orig_activate(self, token)

    monkeypatch.setattr(WorkAdmission, "activate_auto_enable_continuation", _activate)

    def _run():
        body_started.set()
        assert release.wait(timeout=5)
        return "ok"

    class _Exec:
        thread: threading.Thread | None = None

        def submit(self, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            future: Future = Future()

            def _target() -> None:
                try:
                    future.set_result(fn(*args, **kwargs))
                except BaseException as exc:
                    if not future.done():
                        future.set_exception(exc)

            self.thread = threading.Thread(target=_target, name="ae-e9")
            self.thread.start()
            assert entered_activate.wait(timeout=5)
            assert admission._lock.locked()
            assert not body_started.is_set()
            return future

    executor = _Exec()
    try:
        outcome = admission.submit_auto_enable_run_if_open(executor, _run)
        assert isinstance(outcome, AdmissionAccepted)
        assert body_started.wait(timeout=5)
        release.set()
        assert outcome.future.result(timeout=5) == "ok"
    finally:
        release.set()
        if executor.thread is not None:
            isolated_runtime.extra_threads.append(executor.thread)
            executor.thread.join(timeout=5)
            assert not executor.thread.is_alive()


def test_e10_queued_accepted_then_seal_then_start(isolated_runtime) -> None:
    admission = _open_bound()
    occupant_started = threading.Event()
    occupant_release = threading.Event()
    run_started = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)
    occupant_future = None
    try:
        occupant_future = executor.submit(
            lambda: occupant_started.set() or occupant_release.wait(timeout=5)
        )
        assert occupant_started.wait(timeout=5)

        def _run():
            run_started.set()
            return enqueue_auto_enable_batch([_candidate()], _settings())

        outcome = admission.submit_auto_enable_run_if_open(executor, _run)
        assert isinstance(outcome, AdmissionAccepted)
        assert not run_started.is_set()
        admission.seal()
        occupant_release.set()
        assert outcome.future.result(timeout=5) == []
        assert run_started.is_set()
        worker = next(iter(isolated_runtime.registry.values()))
        _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)
    finally:
        occupant_release.set()
        if occupant_future is not None:
            occupant_future.result(timeout=5)
        executor.shutdown(wait=True)


def test_e11_submit_exception_cleans_continuation(isolated_runtime) -> None:
    admission = _open_bound()

    class _Exec:
        def submit(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("executor down")

    with pytest.raises(RuntimeError, match="executor down"):
        admission.submit_auto_enable_run_if_open(_Exec(), lambda: None)
    assert admission._ae_continuations == {}


def test_e12_cancel_tg_handler_after_accepted(
    isolated_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission = _open_bound()
    started = threading.Event()
    release = threading.Event()

    def _run(actor, *, manual):  # noqa: ANN001
        started.set()
        assert release.wait(timeout=5)
        return SimpleNamespace(skipped_reason=None, phase="executed", sent=True)

    monkeypatch.setattr("integrations.wallet_editor_auto_enable.run_auto_enable", _run)
    handlers.bind_rules(_allow_run())
    logger = MagicMock()
    handlers.bind_logger(logger)
    update = _update()
    captured: dict[str, object] = {}
    orig_watch = watch_admitted_future

    def _watch(future, logger_obj, **kwargs):  # noqa: ANN001
        captured["future"] = future
        return orig_watch(future, logger_obj, **kwargs)

    monkeypatch.setattr("modules.antares.handlers.watch_admitted_future", _watch)

    async def _main() -> None:
        task = asyncio.create_task(handlers.cmd_auto_enable_run(update, MagicMock()))
        await asyncio.to_thread(started.wait, 5)
        assert started.is_set()
        assert admission._ae_continuations
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        future = captured["future"]
        assert future.cancelled() is False
        assert admission._ae_continuations
        release.set()
        await asyncio.to_thread(future.result, 5)

    try:
        asyncio.run(_main())
        assert admission._ae_continuations == {}
    finally:
        release.set()


def test_e13_qsize_info_and_exception_log_after_put(
    isolated_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_mod = isolated_runtime.worker_mod
    admission = _open_bound()
    puts: list[int] = []
    orig_put = worker_mod.Queue.put

    def capturing_put(self, item):  # noqa: ANN001
        if item is not _HarnessQueue._END and hasattr(item, "result_future"):
            puts.append(1)
        return orig_put(self, item)

    monkeypatch.setattr(worker_mod.Queue, "put", capturing_put)
    monkeypatch.setattr(
        worker_mod.Queue,
        "qsize",
        lambda self: (_ for _ in ()).throw(RuntimeError("qsize")),
    )
    orig_info = worker_mod.log.info
    orig_exc = worker_mod.log.exception

    def _info(msg, *args, **kwargs):  # noqa: ANN002, ANN003
        if "queued" in str(msg):
            raise RuntimeError("info boom")
        return orig_info(msg, *args, **kwargs)

    def _exception(msg, *args, **kwargs):  # noqa: ANN002, ANN003
        if "queued diagnostics" in str(msg):
            raise RuntimeError("exc-log boom")
        return orig_exc(msg, *args, **kwargs)

    monkeypatch.setattr(worker_mod.log, "info", _info)
    monkeypatch.setattr(worker_mod.log, "exception", _exception)

    def _run():
        return enqueue_auto_enable_batch([_candidate()], _settings())

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    assert outcome.future.result(timeout=5) == []
    assert puts == [1]
    worker = next(iter(isolated_runtime.registry.values()))
    _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)
    assert isolated_runtime.counts["set_result"] == 1
    assert isolated_runtime.counts["set_exception"] == 0
    assert isolated_runtime.counts["invalid"] == 0


def _boom_exception_logger(worker_mod, monkeypatch: pytest.MonkeyPatch) -> None:
    orig_exc = worker_mod.log.exception

    def _exception(msg, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("exc-log boom")

    monkeypatch.setattr(worker_mod.log, "exception", _exception)
    del orig_exc


def test_e14_wait_log_and_exception_logger(
    isolated_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_mod = isolated_runtime.worker_mod
    admission = _open_bound()
    monkeypatch.setattr(
        worker_mod,
        "_log_queue_wait",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("wait-log")),
    )
    _boom_exception_logger(worker_mod, monkeypatch)

    def _run():
        return enqueue_auto_enable_batch([_candidate()], _settings())

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    with pytest.raises(RuntimeError, match="wait-log"):
        outcome.future.result(timeout=5)
    worker = next(iter(isolated_runtime.registry.values()))
    _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)
    assert worker.queue.unfinished_tasks == 0
    assert isolated_runtime.counts["set_exception"] == 1
    assert isolated_runtime.counts["set_result"] == 0
    assert isolated_runtime.counts["invalid"] == 0


def test_e14_execute_and_exception_logger(
    isolated_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_mod = isolated_runtime.worker_mod
    admission = _open_bound()
    monkeypatch.setattr(
        "integrations.wallet_editor_auto_enable_executor.execute_enable_batch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("execute boom")),
    )
    _boom_exception_logger(worker_mod, monkeypatch)

    def _run():
        return enqueue_auto_enable_batch([_candidate()], _settings())

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    with pytest.raises(RuntimeError, match="execute boom"):
        outcome.future.result(timeout=5)
    worker = next(iter(isolated_runtime.registry.values()))
    _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)
    assert isolated_runtime.counts["set_exception"] == 1
    assert isolated_runtime.counts["invalid"] == 0


def test_e14_start_log(isolated_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    worker_mod = isolated_runtime.worker_mod
    admission = _open_bound()
    orig_info = worker_mod.log.info

    def _start_boom(msg, *args, **kwargs):  # noqa: ANN002, ANN003
        if "[AutoEnable] started" in str(msg):
            raise RuntimeError("start-log")
        return orig_info(msg, *args, **kwargs)

    monkeypatch.setattr(worker_mod.log, "info", _start_boom)
    _boom_exception_logger(worker_mod, monkeypatch)

    def _run():
        return enqueue_auto_enable_batch([_candidate()], _settings())

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    with pytest.raises(RuntimeError, match="start-log"):
        outcome.future.result(timeout=5)
    worker = next(iter(isolated_runtime.registry.values()))
    _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)
    assert isolated_runtime.counts["set_exception"] == 1
    assert isolated_runtime.counts["invalid"] == 0


def test_e14_real_import_before_execute(
    isolated_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    admission = _open_bound()
    import integrations.wallet_editor_auto_enable_executor as executor_mod

    monkeypatch.delattr(executor_mod, "execute_enable_batch")

    def _run():
        return enqueue_auto_enable_batch([_candidate()], _settings())

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    with pytest.raises(ImportError):
        outcome.future.result(timeout=5)
    worker = next(iter(isolated_runtime.registry.values()))
    _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)
    assert isolated_runtime.counts["set_exception"] == 1
    assert isolated_runtime.counts["invalid"] == 0


def test_e15_diag_after_set_result_does_not_set_exception(
    isolated_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_mod = isolated_runtime.worker_mod
    admission = _open_bound()
    orig_info = worker_mod.log.info
    finished_diag = threading.Event()

    def _info(msg, *args, **kwargs):  # noqa: ANN002, ANN003
        if "finished" in str(msg):
            finished_diag.set()
            raise RuntimeError("diag boom")
        return orig_info(msg, *args, **kwargs)

    monkeypatch.setattr(worker_mod.log, "info", _info)
    _boom_exception_logger(worker_mod, monkeypatch)

    def _run():
        return enqueue_auto_enable_batch([_candidate()], _settings())

    outcome = admission.submit_auto_enable_run_if_open(get_job_executor(), _run)
    assert outcome.future.result(timeout=5) == []
    worker = next(iter(isolated_runtime.registry.values()))
    _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)
    assert finished_diag.is_set()
    assert isolated_runtime.counts["set_result"] == 1
    assert isolated_runtime.counts["set_exception"] == 0
    assert isolated_runtime.counts["invalid"] == 0


def test_tls_cleared_on_executor_thread_reuse(isolated_runtime) -> None:
    admission = _open_bound()
    executor = ThreadPoolExecutor(max_workers=1)
    seen: list = []
    try:
        def _first():
            seen.append(current_auto_enable_continuation() is not None)
            return enqueue_auto_enable_batch([_candidate()], _settings())

        def _second():
            seen.append(current_auto_enable_continuation())
            return "idle"

        first = admission.submit_auto_enable_run_if_open(executor, _first)
        assert first.future.result(timeout=5) == []
        worker = next(iter(isolated_runtime.registry.values()))
        _wait_queue_idle(worker.queue, isolated_runtime.extra_threads)
        second = executor.submit(_second)
        assert second.result(timeout=5) == "idle"
        assert seen[0] is True
        assert seen[1] is None
        assert current_auto_enable_continuation() is None
    finally:
        executor.shutdown(wait=True)


def test_plan_does_not_register_continuation(isolated_runtime) -> None:
    admission = _open_bound()
    seen: list = []

    def _plan(*args, **kwargs):  # noqa: ANN002, ANN003
        seen.append(current_auto_enable_continuation())
        return SimpleNamespace(skipped_reason=None, sent=True)

    outcome = admission.submit_if_open(get_job_executor(), _plan)
    outcome.future.result(timeout=5)
    assert seen == [None]
    assert admission._ae_continuations == {}


def test_empty_candidates_bound_without_continuation_rejected(
    isolated_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open_bound()
    calls = _block_ensure_put(monkeypatch, isolated_runtime.worker_mod)
    with pytest.raises(IsolatedAutoEnableEnqueueRejected):
        enqueue_auto_enable_batch([], _settings())
    assert calls == []
