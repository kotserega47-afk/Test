"""TASK-41 production WE profile worker sentinel and join (not full drain)."""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from automation.runtime import WalletEditorTask
from core.job_dispatch import _reset_job_executor_for_tests
from integrations.wallet_editor_auto_enable_eligibility import CandidateRow
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings
from modules.antares.application_lifecycle import run_ptb_lifecycle
from modules.antares.work_admission import (
    AdmissionAccepted,
    WorkAdmission,
    bind_antares_admission,
    reset_antares_admission_for_tests,
)
from automation.worker import IsolatedProfileWorkerCreateRejected
from automation.worker import IsolatedProfileWorkerStopError


@pytest.fixture(autouse=True)
def _reset_bound() -> None:
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    yield
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()


@pytest.fixture
def workers():
    import automation.worker as worker_mod

    orig = worker_mod._profile_workers
    orig_frozen = worker_mod._profile_workers_frozen
    orig_done = worker_mod._profile_workers_stop_done
    worker_mod._profile_workers = {}
    worker_mod._profile_workers_frozen = False
    worker_mod._profile_workers_stop_done = False
    worker_mod._reset_we_stop_owner_for_tests()
    errors: list[BaseException] = []
    try:
        yield worker_mod
    finally:
        for worker in list(worker_mod._profile_workers.values()):
            thread = worker.thread
            real = getattr(thread, "_t41_real", thread)
            try:
                if real is not None and real.is_alive() and not worker.sentinel_put:
                    worker.queue.put(worker_mod.PROFILE_WORKER_STOP)
                    worker.sentinel_put = True
            except BaseException as exc:
                errors.append(exc)
            if real is not None:
                real.join(timeout=5)
                if real.is_alive():
                    errors.append(RuntimeError(f"worker still alive: {real.name}"))
        worker_mod._profile_workers = orig
        worker_mod._profile_workers_frozen = orig_frozen
        worker_mod._profile_workers_stop_done = orig_done
        worker_mod._reset_we_stop_owner_for_tests()
        if errors:
            raise errors[0]


def _open_bound() -> WorkAdmission:
    admission = WorkAdmission()
    bind_antares_admission(admission)
    admission.open()
    return admission


def _task(profile: str = "DENIS") -> WalletEditorTask:
    return WalletEditorTask(
        file_path="/tmp/t41.xlsx",
        chat_id=1,
        telegram_user_id=2,
        operator_profile=profile,
        source_file_name="t41.xlsx",
        login="l",
        password="p",
        auth_state_path="/tmp/auth.json",
    )


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


def test_lifecycle_helper_does_not_stop_profile_workers() -> None:
    source = inspect.getsource(run_ptb_lifecycle)
    assert "stop_isolated_profile_workers" not in source
    assert "PROFILE_WORKER_STOP" not in source


def test_idle_worker_exits_on_sentinel_and_joins(workers) -> None:
    admission = _open_bound()
    workers.ensure_profile_queue("DENIS")
    thread = workers._profile_workers["DENIS"].thread
    assert thread is not None and thread.is_alive()
    admission.seal()
    joined = asyncio.run(
        workers.stop_isolated_profile_workers(admission, producers_complete=True)
    )
    assert joined == ("DENIS",)
    assert thread.is_alive() is False
    assert workers._profile_workers["DENIS"].queue.unfinished_tasks == 0
    assert workers._profile_workers_frozen is True


def test_active_item_empty_queue_is_not_drained(workers, monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def _block(_profile, _item) -> None:
        try:
            started.set()
            assert release.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)
            raise

    monkeypatch.setattr(workers, "_run_disable_task", _block)
    admission = _open_bound()
    queue = workers.ensure_profile_queue("DENIS")
    queue.put(_task())
    try:
        assert started.wait(timeout=5)
        assert queue.qsize() == 0
        assert queue.unfinished_tasks == 1
        admission.seal()
        with pytest.raises(IsolatedProfileWorkerStopError) as ei:
            asyncio.run(
                workers.stop_isolated_profile_workers(
                    admission, producers_complete=True
                )
            )
        assert "not drained" in ei.value.remainder.reason
        assert workers._profile_workers["DENIS"].thread.is_alive()
        assert workers._profile_workers_frozen is False
    finally:
        release.set()
        if errors:
            raise errors[0]


def test_late_ae_worker_after_seal_is_in_final_stop_list(workers, monkeypatch) -> None:
    occupy_started = threading.Event()
    occupy_release = threading.Event()
    ae_started = threading.Event()
    errors: list[BaseException] = []
    monkeypatch.setattr(
        "integrations.wallet_editor_auto_enable_executor.build_run_config_from_conversion_env",
        lambda: SimpleNamespace(
            login="l", password="p", auth_state_path="/tmp/auth.json"
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

    def _occupy() -> None:
        try:
            occupy_started.set()
            assert occupy_release.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)
            raise

    def _ae_run() -> list:
        ae_started.set()
        from automation.worker import enqueue_auto_enable_batch

        return enqueue_auto_enable_batch(
            [_candidate()],
            _settings(),
            operator_profile="LATE",
        )

    admission = _open_bound()
    workers.ensure_profile_queue("DENIS")
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t41-ae")
    try:
        occupy = admission.submit_if_open(pool, _occupy)
        assert isinstance(occupy, AdmissionAccepted)
        assert occupy_started.wait(timeout=5)
        outcome = admission.submit_auto_enable_run_if_open(pool, _ae_run)
        assert isinstance(outcome, AdmissionAccepted)
        admission.seal()
        assert "LATE" not in workers._profile_workers
        occupy_release.set()
        assert ae_started.wait(timeout=5)
        asyncio.run(admission.wait_accepted_executor_work())
        assert "LATE" in workers._profile_workers
        late_thread = workers._profile_workers["LATE"].thread
        joined = asyncio.run(
            workers.stop_isolated_profile_workers(
                admission, producers_complete=True
            )
        )
        assert "LATE" in joined
        assert "DENIS" in joined
        assert late_thread is not None and late_thread.is_alive() is False
    finally:
        occupy_release.set()
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def test_pending_continuation_blocks_stop(workers) -> None:
    occupy_started = threading.Event()
    occupy_release = threading.Event()
    errors: list[BaseException] = []

    def _occupy() -> None:
        try:
            occupy_started.set()
            assert occupy_release.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)
            raise

    admission = _open_bound()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t41-pend")
    try:
        admission.submit_if_open(pool, _occupy)
        assert occupy_started.wait(timeout=5)
        outcome = admission.submit_auto_enable_run_if_open(pool, lambda: [])
        assert isinstance(outcome, AdmissionAccepted)
        admission.seal()
        with admission._lock:
            states = [r.state for r in admission._ae_continuations.values()]
        assert states == ["pending"]
        with pytest.raises(IsolatedProfileWorkerStopError) as ei:
            asyncio.run(
                workers.stop_isolated_profile_workers(
                    admission, producers_complete=True
                )
            )
        assert "accepted executor" in ei.value.remainder.reason
        assert ei.value.remainder.continuation_states == ("pending",)
        assert workers._profile_workers_frozen is False
    finally:
        occupy_release.set()
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def test_active_continuation_blocks_stop(workers) -> None:
    started = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def _ae() -> list:
        try:
            started.set()
            assert release.wait(timeout=5)
            return []
        except BaseException as exc:
            errors.append(exc)
            raise

    admission = _open_bound()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="t41-act")
    try:
        outcome = admission.submit_auto_enable_run_if_open(pool, _ae)
        assert isinstance(outcome, AdmissionAccepted)
        assert started.wait(timeout=5)
        with admission._lock:
            states = [r.state for r in admission._ae_continuations.values()]
        assert states == ["active"]
        admission.seal()
        with pytest.raises(IsolatedProfileWorkerStopError) as ei:
            asyncio.run(
                workers.stop_isolated_profile_workers(
                    admission, producers_complete=True
                )
            )
        assert ei.value.remainder.continuation_states == ("active",)
        assert workers._profile_workers_frozen is False
    finally:
        release.set()
        pool.shutdown(wait=True)
        if errors:
            raise errors[0]


def test_repeated_stop_does_not_put_second_sentinel(workers) -> None:
    admission = _open_bound()
    queue = workers.ensure_profile_queue("DENIS")
    puts: list[object] = []
    orig_put = queue.put

    def _put(item):  # noqa: ANN001
        puts.append(item)
        return orig_put(item)

    queue.put = _put  # type: ignore[method-assign]
    admission.seal()
    asyncio.run(
        workers.stop_isolated_profile_workers(admission, producers_complete=True)
    )
    first = sum(
        1 for item in puts if isinstance(item, workers.ProfileWorkerStopSentinel)
    )
    assert first == 1
    again = asyncio.run(
        workers.stop_isolated_profile_workers(admission, producers_complete=True)
    )
    second = sum(
        1 for item in puts if isinstance(item, workers.ProfileWorkerStopSentinel)
    )
    assert second == 1
    assert again == ("DENIS",)


def test_dead_worker_fails_without_retry(workers) -> None:
    admission = _open_bound()
    dead = threading.Thread(target=lambda: None, name="t41-dead")
    dead.start()
    dead.join(timeout=5)
    assert dead.is_alive() is False
    workers._profile_workers["DEAD"] = workers._ProfileWorker(
        queue=workers.Queue(), thread=dead
    )
    admission.seal()
    with pytest.raises(IsolatedProfileWorkerStopError) as ei:
        asyncio.run(
            workers.stop_isolated_profile_workers(admission, producers_complete=True)
        )
    assert "dead" in ei.value.remainder.reason
    assert workers._profile_workers_frozen is False
    with pytest.raises(IsolatedProfileWorkerStopError) as ei2:
        asyncio.run(
            workers.stop_isolated_profile_workers(admission, producers_complete=True)
        )
    assert "dead" in ei2.value.remainder.reason


def test_join_deadline_fails_without_retry(workers) -> None:
    admission = _open_bound()
    queue = workers.ensure_profile_queue("DENIS")
    worker = workers._profile_workers["DENIS"]
    real = worker.thread
    assert real is not None
    puts: list[object] = []
    orig_put = queue.put

    def _put(item):  # noqa: ANN001
        puts.append(item)
        return orig_put(item)

    queue.put = _put  # type: ignore[method-assign]

    class _Linger:
        _t41_real = real

        def join(self, timeout=None):  # noqa: ANN001
            if timeout is None:
                real.join()
                return
            time.sleep(min(float(timeout), 0.05))

        def is_alive(self) -> bool:
            return True

        def __getattr__(self, name: str):
            return getattr(real, name)

    worker.thread = _Linger()  # type: ignore[assignment]
    admission.seal()
    with pytest.raises(IsolatedProfileWorkerStopError) as ei:
        asyncio.run(
            workers.stop_isolated_profile_workers(
                admission, producers_complete=True, timeout=0.05
            )
        )
    assert "deadline" in ei.value.remainder.reason
    with pytest.raises(IsolatedProfileWorkerStopError) as ei2:
        asyncio.run(
            workers.stop_isolated_profile_workers(
                admission, producers_complete=True, timeout=0.05
            )
        )
    assert "deadline" in ei2.value.remainder.reason
    assert (
        sum(1 for item in puts if isinstance(item, workers.ProfileWorkerStopSentinel))
        == 1
    )


def test_sentinel_task_done_clears_unfinished(workers) -> None:
    admission = _open_bound()
    queue = workers.ensure_profile_queue("DENIS")
    admission.seal()
    asyncio.run(
        workers.stop_isolated_profile_workers(admission, producers_complete=True)
    )
    assert queue.unfinished_tasks == 0
    assert queue.qsize() == 0


def test_unbound_admission_does_not_stop_workers(workers) -> None:
    workers.ensure_profile_queue("MIX")
    thread = workers._profile_workers["MIX"].thread
    foreign = WorkAdmission()
    with pytest.raises(IsolatedProfileWorkerStopError) as ei:
        asyncio.run(
            workers.stop_isolated_profile_workers(foreign, producers_complete=True)
        )
    assert "mixed/unbound" in ei.value.remainder.reason
    assert thread is not None and thread.is_alive()
    assert workers._profile_workers_frozen is False


def test_frozen_registry_rejects_new_worker_after_stop(workers) -> None:
    admission = _open_bound()
    workers.ensure_profile_queue("DENIS")
    admission.seal()
    asyncio.run(
        workers.stop_isolated_profile_workers(admission, producers_complete=True)
    )
    with pytest.raises(IsolatedProfileWorkerCreateRejected):
        workers.ensure_profile_queue("AFTER")
