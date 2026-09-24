"""TASK-43 isolated WE registry daemon accounting and join (not full graceful)."""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from datetime import datetime, timezone

import pytest

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from core.job_dispatch import _reset_job_executor_for_tests
from modules.antares.application_lifecycle import run_ptb_lifecycle
from modules.antares.work_admission import (
    WorkAdmission,
    bind_antares_admission,
    reset_antares_admission_for_tests,
)
from integrations.wallet_editor_registry_async import (
    IsolatedRegistryDaemonCreateRejected,
    IsolatedRegistryDaemonStopError,
    RegistryDaemonLifecycle,
    _reset_registry_daemon_ops_for_tests,
    create_outbox_record,
    get_outbox_record,
    schedule_registry_append,
    snapshot_isolated_registry_daemon_ops,
    wait_isolated_registry_daemon_ops,
)
from integrations.wallet_editor_registry_lifecycle import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
)


@pytest.fixture(autouse=True)
def _reset_bound(tmp_path, monkeypatch) -> None:
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    _reset_registry_daemon_ops_for_tests()
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    yield
    reset_antares_admission_for_tests()
    _reset_job_executor_for_tests()
    _reset_registry_daemon_ops_for_tests()


@pytest.fixture
def workers():
    import automation.worker as worker_mod

    orig = worker_mod._profile_workers
    orig_frozen = worker_mod._profile_workers_frozen
    orig_done = worker_mod._profile_workers_stop_done
    worker_mod._profile_workers = {}
    worker_mod._profile_workers_frozen = False
    worker_mod._profile_workers_stop_done = False
    errors: list[BaseException] = []
    try:
        yield worker_mod
    finally:
        for worker in list(worker_mod._profile_workers.values()):
            thread = worker.thread
            try:
                if thread is not None and thread.is_alive() and not worker.sentinel_put:
                    worker.queue.put(worker_mod.PROFILE_WORKER_STOP)
                    worker.sentinel_put = True
            except BaseException as exc:
                errors.append(exc)
            if thread is not None:
                thread.join(timeout=5)
        worker_mod._profile_workers = orig
        worker_mod._profile_workers_frozen = orig_frozen
        worker_mod._profile_workers_stop_done = orig_done
        if errors:
            raise errors[0]


def _open_bound() -> WorkAdmission:
    admission = WorkAdmission()
    bind_antares_admission(admission)
    admission.open()
    return admission


def _task(run_id: str = "run-t43") -> WalletEditorTask:
    return WalletEditorTask(
        file_path="/tmp/t43.xlsx",
        chat_id=1,
        telegram_user_id=2,
        operator_profile="DENIS",
        source_file_name="t43.xlsx",
        login="l",
        password="p",
        auth_state_path="/tmp/auth.json",
        run_id=run_id,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stats() -> Stats:
    return Stats()


def _schedule(task: WalletEditorTask, *, append_impl) -> None:
    schedule_registry_append(
        task,
        "/tmp/result.xlsx",
        _stats(),
        run_started_at=_now(),
        run_finished_at=_now(),
        output_file="result.xlsx",
        from_durable_copy=True,
    )


@pytest.fixture
def daemon_cleanup():
    errors: list[BaseException] = []
    threads: list[threading.Thread] = []

    def _track() -> None:
        for op in snapshot_isolated_registry_daemon_ops().values():
            if op.thread is not None:
                threads.append(op.thread)

    try:
        yield _track
    finally:
        for op in list(snapshot_isolated_registry_daemon_ops().values()):
            if op.thread is not None and op.thread not in threads:
                threads.append(op.thread)
        for thread in threads:
            if thread.ident is None:
                continue
            thread.join(timeout=5)
            if thread.is_alive():
                errors.append(RuntimeError(f"daemon still alive: {thread.name}"))
        _reset_registry_daemon_ops_for_tests()
        if errors:
            raise errors[0]


def test_lifecycle_helper_does_not_wait_registry_daemons() -> None:
    source = inspect.getsource(run_ptb_lifecycle)
    assert "wait_isolated_registry_daemon_ops" not in source
    assert "IsolatedRegistryDaemon" not in source


def test_r1_we_done_append_held_blocks_wait(
    workers, monkeypatch, daemon_cleanup
) -> None:
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def _hold(*_a, **_k) -> None:
        try:
            entered.set()
            assert release.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)
            raise

    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        _hold,
    )
    admission = _open_bound()
    admission.seal()
    task = _task("r1")
    _schedule(task, append_impl=_hold)
    daemon_cleanup()
    assert entered.wait(timeout=5)
    with pytest.raises(IsolatedRegistryDaemonStopError) as ei:
        asyncio.run(
            wait_isolated_registry_daemon_ops(
                admission, producers_complete=True, timeout=0.05
            )
        )
    assert "deadline" in ei.value.remainder.reason
    assert any(e.alive for e in ei.value.remainder.entries)
    release.set()
    if errors:
        raise errors[0]


def test_r2_started_and_append_entered(
    workers, monkeypatch, daemon_cleanup
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def _hold(*_a, **_k) -> None:
        entered.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        _hold,
    )
    admission = _open_bound()
    admission.seal()
    _schedule(_task("r2"), append_impl=_hold)
    daemon_cleanup()
    assert entered.wait(timeout=5)
    snap = snapshot_isolated_registry_daemon_ops()
    assert "r2" in snap
    assert snap["r2"].state is RegistryDaemonLifecycle.STARTED
    assert snap["r2"].started is True
    release.set()
    joined = asyncio.run(
        wait_isolated_registry_daemon_ops(admission, producers_complete=True)
    )
    assert joined == ("r2",)


def test_r3_start_ok_before_append_body(
    workers, monkeypatch, daemon_cleanup
) -> None:
    gate = threading.Barrier(2)
    release = threading.Event()
    errors: list[BaseException] = []

    def _hold(*_a, **_k) -> None:
        try:
            gate.wait(timeout=5)
            assert release.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)
            raise

    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        _hold,
    )
    admission = _open_bound()
    admission.seal()
    _schedule(_task("r3"), append_impl=_hold)
    daemon_cleanup()
    # schedule() returned under lock after successful start → visible before append body.
    snap = snapshot_isolated_registry_daemon_ops()
    assert "r3" in snap
    assert snap["r3"].started is True
    assert snap["r3"].state is RegistryDaemonLifecycle.STARTED
    gate.wait(timeout=5)
    release.set()
    asyncio.run(wait_isolated_registry_daemon_ops(admission, producers_complete=True))
    if errors:
        raise errors[0]


def test_r4_finished_before_wait_is_success(
    workers, monkeypatch, daemon_cleanup
) -> None:
    def _fast(*_a, **_k) -> None:
        return None

    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        _fast,
    )
    admission = _open_bound()
    admission.seal()
    _schedule(_task("r4"), append_impl=_fast)
    daemon_cleanup()
    snap = snapshot_isolated_registry_daemon_ops()
    assert "r4" in snap
    snap["r4"].thread.join(timeout=5)
    assert snap["r4"].thread.is_alive() is False
    joined = asyncio.run(
        wait_isolated_registry_daemon_ops(admission, producers_complete=True)
    )
    assert "r4" in joined
    assert snapshot_isolated_registry_daemon_ops() == {}


def test_r5_business_failure_join_success(
    workers, monkeypatch, daemon_cleanup, tmp_path
) -> None:
    def _boom(*_a, **_k) -> None:
        from integrations.wallet_editor_registry_async import update_outbox_status

        update_outbox_status(
            "r5", status=OUTBOX_STATUS_FAILED, last_error="boom", increment_attempt=True
        )

    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        _boom,
    )
    admission = _open_bound()
    admission.seal()
    task = _task("r5")
    durable = tmp_path / "r5.xlsx"
    durable.write_bytes(b"x")
    create_outbox_record(
        task,
        durable_path=str(durable),
        stats=_stats(),
        run_started_at=_now(),
        run_finished_at=_now(),
        output_file="r5.xlsx",
    )
    _schedule(task, append_impl=_boom)
    daemon_cleanup()
    joined = asyncio.run(
        wait_isolated_registry_daemon_ops(admission, producers_complete=True)
    )
    assert joined == ("r5",)
    record = get_outbox_record("r5")
    assert record is not None
    assert record.status == OUTBOX_STATUS_FAILED


def test_r6_cancel_wait_keeps_underlying(
    workers, monkeypatch, daemon_cleanup
) -> None:
    entered = threading.Event()
    release = threading.Event()
    frozen_seen = threading.Event()
    errors: list[BaseException] = []

    def _hold(*_a, **_k) -> None:
        try:
            entered.set()
            if not release.wait(timeout=30):
                errors.append(TimeoutError("r6 hold not released"))
        except BaseException as exc:
            errors.append(exc)
            raise

    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        _hold,
    )
    admission = _open_bound()
    admission.seal()
    _schedule(_task("r6"), append_impl=_hold)
    daemon_cleanup()
    assert entered.wait(timeout=5)

    import integrations.wallet_editor_registry_async as async_mod

    async def _cancelled() -> None:
        wait_task = asyncio.create_task(
            wait_isolated_registry_daemon_ops(
                admission, producers_complete=True, timeout=None
            )
        )
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            with async_mod._daemon_ops_lock:
                if async_mod._daemon_ops_frozen:
                    frozen_seen.set()
                    break
            await asyncio.sleep(0)
        assert frozen_seen.is_set()
        wait_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wait_task

    asyncio.run(_cancelled())
    snap = snapshot_isolated_registry_daemon_ops()
    assert "r6" in snap
    assert snap["r6"].started is True
    assert snap["r6"].thread.is_alive()
    assert async_mod._daemon_ops_wait_done is False
    release.set()
    joined = asyncio.run(
        wait_isolated_registry_daemon_ops(admission, producers_complete=True)
    )
    assert joined == ("r6",)
    if errors:
        raise errors[0]


def test_r7_repeat_wait_same_daemon(
    workers, monkeypatch, daemon_cleanup
) -> None:
    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        lambda *a, **k: None,
    )
    admission = _open_bound()
    admission.seal()
    _schedule(_task("r7"), append_impl=None)
    daemon_cleanup()
    first = asyncio.run(
        wait_isolated_registry_daemon_ops(admission, producers_complete=True)
    )
    second = asyncio.run(
        wait_isolated_registry_daemon_ops(admission, producers_complete=True)
    )
    assert first == ("r7",)
    assert second == first
    assert snapshot_isolated_registry_daemon_ops() == {}


def test_r8_deadline_alive_fails(
    workers, monkeypatch, daemon_cleanup
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def _hold(*_a, **_k) -> None:
        entered.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        _hold,
    )
    admission = _open_bound()
    admission.seal()
    _schedule(_task("r8"), append_impl=_hold)
    daemon_cleanup()
    assert entered.wait(timeout=5)
    with pytest.raises(IsolatedRegistryDaemonStopError) as ei:
        asyncio.run(
            wait_isolated_registry_daemon_ops(
                admission, producers_complete=True, timeout=0.05
            )
        )
    assert "deadline" in ei.value.remainder.reason
    assert any(e.run_id == "r8" and e.alive for e in ei.value.remainder.entries)
    with pytest.raises(IsolatedRegistryDaemonStopError):
        asyncio.run(
            wait_isolated_registry_daemon_ops(
                admission, producers_complete=True, timeout=0.05
            )
        )
    release.set()


def test_r9_successful_drain_reaps(
    workers, monkeypatch, daemon_cleanup
) -> None:
    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        lambda *a, **k: None,
    )
    admission = _open_bound()
    admission.seal()
    _schedule(_task("r9a"), append_impl=None)
    _schedule(_task("r9b"), append_impl=None)
    daemon_cleanup()
    joined = asyncio.run(
        wait_isolated_registry_daemon_ops(admission, producers_complete=True)
    )
    assert set(joined) == {"r9a", "r9b"}
    assert snapshot_isolated_registry_daemon_ops() == {}


def test_r10_freeze_rejects_new_schedule(
    workers, monkeypatch, daemon_cleanup
) -> None:
    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        lambda *a, **k: None,
    )
    admission = _open_bound()
    admission.seal()
    asyncio.run(wait_isolated_registry_daemon_ops(admission, producers_complete=True))
    with pytest.raises(IsolatedRegistryDaemonCreateRejected):
        _schedule(_task("r10"), append_impl=None)
    assert snapshot_isolated_registry_daemon_ops() == {}


def test_r11_pending_outbox_not_inflight(
    workers, monkeypatch, daemon_cleanup, tmp_path
) -> None:
    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        lambda *a, **k: None,
    )
    admission = _open_bound()
    admission.seal()
    task = _task("r11")
    durable = tmp_path / "r11.xlsx"
    durable.write_bytes(b"x")
    create_outbox_record(
        task,
        durable_path=str(durable),
        stats=_stats(),
        run_started_at=_now(),
        run_finished_at=_now(),
        output_file="r11.xlsx",
    )
    assert get_outbox_record("r11").status == OUTBOX_STATUS_PENDING
    _schedule(task, append_impl=None)
    daemon_cleanup()
    joined = asyncio.run(
        wait_isolated_registry_daemon_ops(admission, producers_complete=True)
    )
    assert joined == ("r11",)
    assert get_outbox_record("r11").status == OUTBOX_STATUS_PENDING


def test_r12_unbound_does_not_freeze(workers, monkeypatch, daemon_cleanup) -> None:
    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        lambda *a, **k: None,
    )
    foreign = WorkAdmission()
    with pytest.raises(IsolatedRegistryDaemonStopError) as ei:
        asyncio.run(
            wait_isolated_registry_daemon_ops(foreign, producers_complete=True)
        )
    assert "mixed/unbound" in ei.value.remainder.reason
    _schedule(_task("r12"), append_impl=None)
    daemon_cleanup()
    assert "r12" in snapshot_isolated_registry_daemon_ops()


def test_r14_delayed_cleanup_ignored(
    workers, monkeypatch, daemon_cleanup
) -> None:
    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        lambda *a, **k: None,
    )
    hold = threading.Event()
    cleanup_started = threading.Event()

    def _cleanup() -> None:
        cleanup_started.set()
        hold.wait(timeout=5)

    cleanup_thread = threading.Thread(
        target=_cleanup, name="delayed-cleanup-r14", daemon=True
    )
    cleanup_thread.start()
    try:
        assert cleanup_started.wait(timeout=5)
        admission = _open_bound()
        admission.seal()
        _schedule(_task("r14"), append_impl=None)
        daemon_cleanup()
        joined = asyncio.run(
            wait_isolated_registry_daemon_ops(admission, producers_complete=True)
        )
        assert joined == ("r14",)
        assert cleanup_thread.is_alive()
        assert all(
            not e.run_id.startswith("delayed")
            for e in snapshot_isolated_registry_daemon_ops()
        )
    finally:
        hold.set()
        cleanup_thread.join(timeout=5)


def test_r15_start_exception_no_live(
    workers, monkeypatch, daemon_cleanup
) -> None:
    admission = _open_bound()
    admission.seal()
    task = _task("r15")

    real_thread = threading.Thread

    class _BoomThread:
        def __init__(self, *a, **k):
            self.name = k.get("name", "boom")
            self._target = a[0] if a else k.get("target")

        def start(self) -> None:
            raise RuntimeError("start failed")

        def is_alive(self) -> bool:
            return False

        def join(self, timeout=None) -> None:
            return None

    monkeypatch.setattr(threading, "Thread", _BoomThread)
    with pytest.raises(RuntimeError, match="start failed"):
        _schedule(task, append_impl=None)
    monkeypatch.setattr(threading, "Thread", real_thread)
    assert snapshot_isolated_registry_daemon_ops() == {}
    joined = asyncio.run(
        wait_isolated_registry_daemon_ops(admission, producers_complete=True)
    )
    assert joined == ()


def test_active_we_item_blocks_daemon_wait(
    workers, monkeypatch, daemon_cleanup
) -> None:
    started = threading.Event()
    release = threading.Event()

    def _block(_profile, _item) -> None:
        started.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(workers, "_run_disable_task", _block)
    monkeypatch.setattr(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        lambda *a, **k: None,
    )
    admission = _open_bound()
    queue = workers.ensure_profile_queue("DENIS")
    queue.put(
        WalletEditorTask(
            file_path="/tmp/t.xlsx",
            chat_id=1,
            telegram_user_id=2,
            operator_profile="DENIS",
            source_file_name="t.xlsx",
            login="l",
            password="p",
            auth_state_path="/tmp/a.json",
        )
    )
    try:
        assert started.wait(timeout=5)
        admission.seal()
        with pytest.raises(IsolatedRegistryDaemonStopError) as ei:
            asyncio.run(
                wait_isolated_registry_daemon_ops(
                    admission, producers_complete=True
                )
            )
        assert "not drained" in ei.value.remainder.reason
    finally:
        release.set()


def test_inconsistent_registered_stuck_fails(
    workers, monkeypatch, daemon_cleanup
) -> None:
    import integrations.wallet_editor_registry_async as async_mod

    admission = _open_bound()
    admission.seal()
    stuck = threading.Thread(target=lambda: None, name="we-registry-stuck")
    with async_mod._daemon_ops_lock:
        async_mod._daemon_ops["stuck"] = async_mod._RegistryDaemonOp(
            run_id="stuck",
            thread=stuck,
            state=RegistryDaemonLifecycle.REGISTERED,
            started=False,
        )
    with pytest.raises(IsolatedRegistryDaemonStopError) as ei:
        asyncio.run(
            wait_isolated_registry_daemon_ops(admission, producers_complete=True)
        )
    assert "inconsistent" in ei.value.remainder.reason
