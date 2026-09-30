# automation/worker.py

from __future__ import annotations

import asyncio
import os
import traceback
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field, replace
from queue import Queue
from typing import Sequence, Union

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from automation.audit import log_timing
from automation.engine import run, RunConfig
from automation.runtime import (
    CONVERSION_AUTO_PROFILE,
    WalletEditorAddWalletTask,
    WalletEditorEditWalletTask,
    WalletEditorTask,
    build_add_wallet_result_path,
    build_edit_wallet_result_path,
    build_wallet_editor_result_path,
)
from core.datetime_utils import now_msk
from integrations.wallet_editor_auto_enable_eligibility import CandidateRow
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings
from integrations.wallet_editor_registry_async import (
    prepare_registry_outbox_and_schedule,
)
from integrations.wallet_editor_registry_db.config import manual_sync_enabled
from integrations.wallet_editor_registry_db.manual_sync import (
    RunSnapshotBinding,
    log_run_snapshot_binding,
    run_manual_sync_prerun_gate,
)
from transport.telegram_transport import send_text, send_document

icon, name = LOG_PROFILES["AUTOMATION"]
log = get_logger(name, icon)

_registry_lock = threading.Lock()
_profile_workers_frozen = False
_profile_workers_stop_done = False
_we_stop_owner_task: asyncio.Task | None = None
_we_stop_owner_loop: asyncio.AbstractEventLoop | None = None
_we_stop_result: tuple[str, ...] | None = None
_we_stop_error: BaseException | None = None
_we_stop_waiters: list[asyncio.Future] = []
_we_stop_sentinel_keys: set[str] = set()
_we_stop_joined_keys: set[str] = set()
_we_stop_admission_token: int | None = None


@dataclass(frozen=True)
class ProfileWorkerStopSentinel:
    """Production worker_loop stop token. Not a business item and not test end_loop."""


PROFILE_WORKER_STOP = ProfileWorkerStopSentinel()

ProfileQueueItem = Union[
    WalletEditorTask,
    WalletEditorAddWalletTask,
    WalletEditorEditWalletTask,
    "WalletEditorAutoEnableBatchTask",
    ProfileWorkerStopSentinel,
]


@dataclass
class WalletEditorAutoEnableBatchTask:
    """Antares auto-enable batch — runs on profile worker queue (CONVERSION_AUTO)."""

    operator_profile: str
    login: str
    password: str
    auth_state_path: str
    candidates: tuple[CandidateRow, ...]
    settings: AutoEnableSettings
    result_future: Future = field(default_factory=Future)
    queued_at: float = field(default_factory=time.perf_counter)
    manual_snapshot_binding: RunSnapshotBinding | None = None


@dataclass
class _ProfileWorker:
    queue: Queue[ProfileQueueItem] = field(default_factory=Queue)
    thread: threading.Thread | None = None
    sentinel_put: bool = False


@dataclass(frozen=True)
class ProfileWorkerRemainder:
    sealed: bool
    accepted_executor: int
    continuation_states: tuple[str, ...]
    profiles: tuple[tuple[str, int, int, bool], ...]
    frozen: bool
    reason: str


class IsolatedProfileWorkerStopError(RuntimeError):
    """Isolated WE worker stop refused or failed. Not a retry signal."""

    def __init__(self, remainder: ProfileWorkerRemainder) -> None:
        super().__init__(remainder.reason)
        self.remainder = remainder


class IsolatedProfileWorkerCreateRejected(RuntimeError):
    """Registry frozen: no new isolated profile worker after the stop snapshot."""


_profile_workers: dict[str, _ProfileWorker] = {}


def ensure_worker_started() -> None:
    """Legacy bootstrap for scheduler.py — workers start lazily per profile on add_task."""
    log.debug("🟢 [Worker] ensure_worker_started() — lazy per-profile workers")


def _ensure_profile_worker(profile_key: str) -> _ProfileWorker:
    with _registry_lock:
        worker = _profile_workers.get(profile_key)
        if worker is not None:
            return worker
        if _profile_workers_frozen:
            raise IsolatedProfileWorkerCreateRejected(
                f"isolated profile worker create rejected after stop snapshot: {profile_key}"
            )

        worker = _ProfileWorker(queue=Queue())
        thread = threading.Thread(
            target=worker_loop,
            args=(profile_key, worker.queue),
            daemon=True,
            name=f"wallet-editor-worker-{profile_key}",
        )
        thread.start()
        worker.thread = thread
        _profile_workers[profile_key] = worker
        log.info(f"🟢 [Worker] profile={profile_key} daemon thread registered")
        return worker


def ensure_profile_queue(profile_key: str):
    """Return the profile Queue after lazy worker start. Not an admit."""

    return _ensure_profile_worker(profile_key).queue


def add_task(task: WalletEditorTask) -> int:
    worker = _ensure_profile_worker(task.operator_profile)
    worker.queue.put(task)
    queue_size = worker.queue.qsize()
    log.info(
        f"📥 [Queue] profile={task.operator_profile} queue_size={queue_size} "
        f"chat_id={task.chat_id} user_id={task.telegram_user_id} file={task.file_path}"
    )
    return queue_size


def add_add_wallet_task(task: WalletEditorAddWalletTask) -> int:
    worker = _ensure_profile_worker(task.operator_profile)
    worker.queue.put(task)
    queue_size = worker.queue.qsize()
    log.info(
        f"📥 [Queue] add_wallet profile={task.operator_profile} queue_size={queue_size} "
        f"chat_id={task.chat_id} user_id={task.user_id} file={task.file_path} "
        f"dry_run={task.dry_run}"
    )
    return queue_size


def add_edit_wallet_task(task: WalletEditorEditWalletTask) -> int:
    worker = _ensure_profile_worker(task.operator_profile)
    worker.queue.put(task)
    queue_size = worker.queue.qsize()
    log.info(
        f"📥 [Queue] edit_wallet profile={task.operator_profile} queue_size={queue_size} "
        f"chat_id={task.chat_id} user_id={task.user_id} file={task.file_path}"
    )
    return queue_size


def enqueue_auto_enable_batch(
    candidates: Sequence[CandidateRow],
    settings: AutoEnableSettings,
    *,
    operator_profile: str | None = None,
) -> list:
    """
    Queue Antares auto-enable batch on the profile worker and wait for outcomes.

    Serializes with disable tasks on the same operator_profile (e.g. CONVERSION_AUTO).
    """
    from integrations.wallet_editor_auto_enable_executor import (
        build_run_config_from_conversion_env,
    )
    from automation.runtime import require_wallet_editor_antares_credentials
    from modules.antares.work_admission import (
        bound_admission,
        require_valid_auto_enable_continuation,
    )

    admission = bound_admission()
    if admission is not None:
        require_valid_auto_enable_continuation(admission)

    if not candidates:
        return []

    profile = (operator_profile or CONVERSION_AUTO_PROFILE).strip().upper()
    cfg = build_run_config_from_conversion_env()
    require_wallet_editor_antares_credentials(cfg)

    batch_task = WalletEditorAutoEnableBatchTask(
        operator_profile=profile,
        login=cfg.login,
        password=cfg.password,
        auth_state_path=cfg.auth_state_path,
        candidates=tuple(candidates),
        settings=settings,
    )
    worker = _ensure_profile_worker(profile)
    worker.queue.put(batch_task)
    try:
        queue_size = worker.queue.qsize()
        log.info(
            "[AutoEnable] queued profile=%s queue_size=%s batch_size=%s",
            profile,
            queue_size,
            len(candidates),
        )
    except Exception:
        _best_effort_log_exception(
            "[AutoEnable] queued diagnostics failed profile=%s batch_size=%s",
            profile,
            len(candidates),
        )
    return batch_task.result_future.result()


def delayed_cleanup(result_path: str, input_path: str, delay: int = 30):
    """Удаляет файлы с задержкой, чтобы Telegram успел их отправить"""
    time.sleep(delay)

    try:
        os.remove(result_path)
        log.info(f"🧹 [Cleanup] Удалён result файл: {result_path}")
    except Exception as e:
        log.warning(f"⚠️ [Cleanup] Не удалось удалить result файл: {e}")

    try:
        os.remove(input_path)
        log.info(f"🧹 [Cleanup] Удалён входной файл: {input_path}")
    except Exception as e:
        log.warning(f"⚠️ [Cleanup] Не удалось удалить входной файл: {e}")


def _worker_manual_sync_gate(
    *,
    chat_id: int,
    triggered_by: str,
    actor: str | None,
) -> RunSnapshotBinding | None:
    """
    Authoritative manual sync gate at worker execution start (I-MAN-10).

    Returns binding when gate passes (or sync disabled). Returns None when blocked.
    """
    gate = run_manual_sync_prerun_gate(triggered_by=triggered_by, actor=actor)
    if gate.ok:
        log_run_snapshot_binding(gate.binding, context=triggered_by)
        return gate.binding
    if gate.operator_message:
        send_text(chat_id=str(chat_id), text=gate.operator_message)
    return None


def _run_disable_task(profile_key: str, task: WalletEditorTask) -> None:
    if manual_sync_enabled():
        binding = _worker_manual_sync_gate(
            chat_id=task.chat_id,
            triggered_by="wallet_editor_disable",
            actor=f"profile:{profile_key}",
        )
        if binding is None:
            return
        task = replace(task, manual_snapshot_binding=binding)

    log.info(f"🚀 [Worker] profile={profile_key} file={task.file_path}")

    run_started_at = now_msk()
    cfg = RunConfig(
        login=task.login,
        password=task.password,
        auth_state_path=task.auth_state_path,
        result_file_path=build_wallet_editor_result_path(
            task.source_file_name,
            task.operator_profile,
        ),
        operator_profile=profile_key,
    )

    log.info(f"📊 [Worker] profile={profile_key} engine.run()")
    result_file, stats = run(task.file_path, cfg)
    run_finished_at = now_msk()

    summary = stats.summary()
    log.info(f"✅ [Worker] profile={profile_key} done: {summary}")

    send_text(
        chat_id=str(task.chat_id),
        text=f"📊 {summary}",
    )

    log.info(f"📤 [Worker] profile={profile_key} sending file: {result_file}")
    send_document(
        path=result_file,
        chat_id=str(task.chat_id),
        caption="Результат обработки",
    )

    user_output_file = os.path.basename(result_file)
    prepare_registry_outbox_and_schedule(
        task,
        result_file,
        stats,
        run_started_at=run_started_at,
        run_finished_at=run_finished_at,
        output_file=user_output_file,
    )

    threading.Thread(
        target=delayed_cleanup,
        args=(result_file, task.file_path),
        daemon=True,
    ).start()


def _run_add_wallet_task(profile_key: str, task: WalletEditorAddWalletTask) -> None:
    from automation.add_wallet_engine import run as run_add_wallet

    if task.requires_manual_snapshot_gate and manual_sync_enabled():
        binding = _worker_manual_sync_gate(
            chat_id=task.chat_id,
            triggered_by="wallet_editor_add_wallet",
            actor=f"profile:{profile_key}",
        )
        if binding is None:
            return
        log_run_snapshot_binding(binding, context="wallet_editor_add_wallet")

    log.info(
        f"🚀 [Worker] add_wallet profile={profile_key} file={task.file_path} dry_run={task.dry_run}"
    )
    cfg = RunConfig(
        login=task.login,
        password=task.password,
        auth_state_path=task.auth_state_path,
        operator_profile=profile_key,
        dry_run=task.dry_run,
        result_file_path=build_add_wallet_result_path(
            task.original_filename,
            task.operator_profile,
        ),
    )

    try:
        result_file, summary = run_add_wallet(task.file_path, cfg, result_file_path=cfg.result_file_path)
    except Exception as exc:
        log.error(f"❌ [Worker] add_wallet profile={profile_key} error: {exc}")
        log.error(traceback.format_exc())
        send_text(
            chat_id=str(task.chat_id),
            text=f"❌ [WalletEditorAdd] Ошибка: {exc}",
        )
        return

    summary_text = summary.telegram_summary()
    log.info(f"✅ [Worker] add_wallet profile={profile_key} done: {summary_text}")
    send_text(chat_id=str(task.chat_id), text=summary_text)
    send_document(
        path=result_file,
        chat_id=str(task.chat_id),
        caption="WalletEditor Add Wallet result",
    )

    threading.Thread(
        target=delayed_cleanup,
        args=(result_file, task.file_path),
        daemon=True,
    ).start()


def _run_edit_wallet_task(profile_key: str, task: WalletEditorEditWalletTask) -> None:
    from automation.edit_wallet_engine import run as run_edit_wallet

    log.info(
        f"🚀 [Worker] edit_wallet profile={profile_key} file={task.file_path}"
    )
    cfg = RunConfig(
        login=task.login,
        password=task.password,
        auth_state_path=task.auth_state_path,
        operator_profile=profile_key,
        result_file_path=build_edit_wallet_result_path(
            task.original_filename,
            task.operator_profile,
        ),
    )

    try:
        result_file, summary = run_edit_wallet(task.file_path, cfg, result_file_path=cfg.result_file_path)
    except Exception as exc:
        log.error(f"❌ [Worker] edit_wallet profile={profile_key} error: {exc}")
        log.error(traceback.format_exc())
        send_text(
            chat_id=str(task.chat_id),
            text=f"❌ [WalletEditorEdit] Ошибка: {exc}",
        )
        return

    summary_text = summary.telegram_summary()
    log.info(f"✅ [Worker] edit_wallet profile={profile_key} done: {summary_text}")
    send_text(chat_id=str(task.chat_id), text=summary_text)
    send_document(
        path=result_file,
        chat_id=str(task.chat_id),
        caption="WalletEditor Edit Wallet result",
    )

    threading.Thread(
        target=delayed_cleanup,
        args=(result_file, task.file_path),
        daemon=True,
    ).start()


def _best_effort_log_exception(msg: str, *args) -> None:
    try:
        log.exception(msg, *args)
    except Exception:
        pass


def _complete_auto_enable_batch_future(task: WalletEditorAutoEnableBatchTask, *, result=None, exc: BaseException | None = None) -> None:
    future = task.result_future
    if future.done():
        return
    try:
        if exc is not None:
            future.set_exception(exc)
        else:
            future.set_result(result)
    except Exception:
        if future.done():
            return
        raise


def _run_auto_enable_batch_task(profile_key: str, task: WalletEditorAutoEnableBatchTask) -> None:
    from integrations.wallet_editor_auto_enable_executor import EnableOutcome, execute_enable_batch

    batch_size = len(task.candidates)
    try:
        log.info(
            "[AutoEnable] started profile=%s batch_size=%s",
            profile_key,
            batch_size,
        )
        cfg = RunConfig(
            login=task.login,
            password=task.password,
            auth_state_path=task.auth_state_path,
            operator_profile=profile_key,
        )
        outcomes: list[EnableOutcome] = execute_enable_batch(
            task.candidates,
            task.settings,
            cfg=cfg,
        )
        _complete_auto_enable_batch_future(task, result=outcomes)
        try:
            log.info(
                "[AutoEnable] finished profile=%s batch_size=%s outcomes=%s",
                profile_key,
                batch_size,
                len(outcomes),
            )
        except Exception:
            _best_effort_log_exception(
                "[AutoEnable] finished-log failed profile=%s batch_size=%s",
                profile_key,
                batch_size,
            )
    except Exception as exc:
        try:
            _complete_auto_enable_batch_future(task, exc=exc)
        finally:
            _best_effort_log_exception(
                "[AutoEnable] failed profile=%s batch_size=%s",
                profile_key,
                batch_size,
            )


def _log_queue_wait(profile_key: str, item: ProfileQueueItem) -> None:
    queued_at = getattr(item, "queued_at", None)
    if queued_at is None:
        return
    wait_ms = round((time.perf_counter() - queued_at) * 1000)
    log_timing(
        profile=profile_key,
        scope="worker",
        step="queue_wait",
        duration_ms=wait_ms,
        outcome="ok",
    )


def _is_add_wallet_task_item(item: ProfileQueueItem) -> bool:
    return isinstance(item, WalletEditorAddWalletTask) or (
        type(item).__name__ == "WalletEditorAddWalletTask"
        and hasattr(item, "original_filename")
        and hasattr(item, "dry_run")
    )


def _is_edit_wallet_task_item(item: ProfileQueueItem) -> bool:
    return isinstance(item, WalletEditorEditWalletTask) or (
        type(item).__name__ == "WalletEditorEditWalletTask"
        and hasattr(item, "original_filename")
        and not hasattr(item, "dry_run")
    )


def _is_auto_enable_batch_item(item: ProfileQueueItem) -> bool:
    return isinstance(item, WalletEditorAutoEnableBatchTask) or (
        type(item).__name__ == "WalletEditorAutoEnableBatchTask"
        and hasattr(item, "result_future")
        and hasattr(item, "candidates")
    )


def _is_disable_task_item(item: ProfileQueueItem) -> bool:
    return isinstance(item, WalletEditorTask) or (
        type(item).__name__ == "WalletEditorTask"
        and hasattr(item, "source_file_name")
        and hasattr(item, "telegram_user_id")
    )


def worker_loop(profile_key: str, task_queue: Queue[ProfileQueueItem]) -> None:
    log.info(f"🟢 [Worker] profile={profile_key} worker_loop started")

    while True:
        item = task_queue.get()
        try:
            if isinstance(item, ProfileWorkerStopSentinel):
                log.info(f"🟢 [Worker] profile={profile_key} worker_loop stop")
                break
            if _is_auto_enable_batch_item(item):
                try:
                    _log_queue_wait(profile_key, item)
                    _run_auto_enable_batch_task(profile_key, item)  # type: ignore[arg-type]
                except Exception as exc:
                    try:
                        future = getattr(item, "result_future", None)
                        if future is not None and not future.done():
                            future.set_exception(exc)
                    finally:
                        _best_effort_log_exception(
                            "[AutoEnable] worker failed profile=%s item=%s",
                            profile_key,
                            type(item).__name__,
                        )
            elif _is_edit_wallet_task_item(item):
                _log_queue_wait(profile_key, item)
                _run_edit_wallet_task(profile_key, item)  # type: ignore[arg-type]
            elif _is_add_wallet_task_item(item):
                _log_queue_wait(profile_key, item)
                _run_add_wallet_task(profile_key, item)  # type: ignore[arg-type]
            elif _is_disable_task_item(item):
                _log_queue_wait(profile_key, item)
                try:
                    _run_disable_task(profile_key, item)  # type: ignore[arg-type]
                except Exception as e:
                    log.error(f"❌ [Worker] profile={profile_key} error: {e}")
                    log.error(traceback.format_exc())

                    send_text(
                        chat_id=str(item.chat_id),
                        text=f"❌ Ошибка: {e}",
                    )
            else:
                _log_queue_wait(profile_key, item)
                log.error(
                    "❌ [Worker] profile=%s unknown queue item type=%s",
                    profile_key,
                    type(item).__name__,
                )
        finally:
            task_queue.task_done()


def snapshot_isolated_profile_workers() -> dict[str, _ProfileWorker]:
    """Live registry copy. Observation during drain; not a stop."""

    with _registry_lock:
        return dict(_profile_workers)


def _continuation_states(admission) -> tuple[str, ...]:
    with admission._lock:
        return tuple(record.state for record in admission._ae_continuations.values())


def collect_profile_worker_remainder(admission, *, reason: str) -> ProfileWorkerRemainder:
    from modules.antares.work_admission import AdmissionState, bound_admission

    bound = bound_admission() is admission
    sealed = bool(bound and admission.state is AdmissionState.SEALED)
    accepted = len(admission.accepted_executor_futures()) if bound else 0
    continuations = _continuation_states(admission) if bound else ()
    profiles: list[tuple[str, int, int, bool]] = []
    with _registry_lock:
        frozen = _profile_workers_frozen
        items = list(_profile_workers.items())
    for key, worker in items:
        thread = worker.thread
        alive = bool(thread is not None and thread.is_alive())
        profiles.append(
            (key, worker.queue.qsize(), worker.queue.unfinished_tasks, alive)
        )
    return ProfileWorkerRemainder(
        sealed=sealed,
        accepted_executor=accepted,
        continuation_states=continuations,
        profiles=tuple(profiles),
        frozen=frozen,
        reason=reason,
    )


def _raise_stop(admission, reason: str) -> None:
    raise IsolatedProfileWorkerStopError(
        collect_profile_worker_remainder(admission, reason=reason)
    )


def _reset_we_stop_owner_for_tests() -> None:
    """Test-only: clear WE stop owner-session state (not production)."""

    global _we_stop_owner_task, _we_stop_owner_loop, _we_stop_result, _we_stop_error
    global _we_stop_waiters, _we_stop_sentinel_keys, _we_stop_joined_keys
    global _we_stop_admission_token, _profile_workers_frozen, _profile_workers_stop_done
    _we_stop_owner_task = None
    _we_stop_owner_loop = None
    _we_stop_result = None
    _we_stop_error = None
    _we_stop_waiters = []
    _we_stop_sentinel_keys = set()
    _we_stop_joined_keys = set()
    _we_stop_admission_token = None


def _publish_we_stop_waiters(result: tuple[str, ...] | None, error: BaseException | None) -> None:
    waiters = list(_we_stop_waiters)
    _we_stop_waiters.clear()
    for fut in waiters:
        if fut.done():
            continue
        if error is not None:
            fut.set_exception(error)
        else:
            assert result is not None
            fut.set_result(result)


async def _owner_stop_isolated_profile_workers(
    admission,
    *,
    timeout: float | None,
) -> tuple[str, ...]:
    """Owner procedure: freeze, sentinel once per profile, join; publish to waiters."""

    global _profile_workers_frozen, _profile_workers_stop_done
    global _we_stop_result, _we_stop_error

    try:
        with _registry_lock:
            if _profile_workers_stop_done and _we_stop_result is not None:
                return _we_stop_result
            snapshot = dict(_profile_workers)

        for key, worker in snapshot.items():
            if worker.queue.unfinished_tasks:
                _raise_stop(
                    admission,
                    f"profile {key} is not drained unfinished={worker.queue.unfinished_tasks}",
                )
            thread = worker.thread
            alive = bool(thread is not None and thread.is_alive())
            if not alive and key not in _we_stop_sentinel_keys:
                # Dead before this owner-session placed a sentinel.
                _raise_stop(admission, f"profile {key} worker is dead")

        with _registry_lock:
            _profile_workers_frozen = True
            snapshot = dict(_profile_workers)

        for key, worker in snapshot.items():
            if worker.queue.unfinished_tasks:
                _raise_stop(
                    admission,
                    f"profile {key} gained work before freeze unfinished={worker.queue.unfinished_tasks}",
                )
            thread = worker.thread
            alive = bool(thread is not None and thread.is_alive())
            if not alive:
                if key in _we_stop_sentinel_keys or key in _we_stop_joined_keys:
                    # Exited after our sentinel (or already joined) — observe as done.
                    _we_stop_joined_keys.add(key)
                    continue
                _raise_stop(admission, f"profile {key} worker is dead")
            if not worker.sentinel_put:
                worker.queue.put(PROFILE_WORKER_STOP)
                worker.sentinel_put = True
                _we_stop_sentinel_keys.add(key)

        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else (loop.time() + timeout)
        joined: list[str] = []
        for key, worker in snapshot.items():
            if key in _we_stop_joined_keys:
                joined.append(key)
                continue
            thread = worker.thread
            if thread is None:
                if key in _we_stop_sentinel_keys:
                    _we_stop_joined_keys.add(key)
                    joined.append(key)
                    continue
                _raise_stop(admission, f"profile {key} worker is dead")
            while thread.is_alive():
                remaining = None if deadline is None else max(0.0, deadline - loop.time())
                if deadline is not None and remaining == 0.0 and thread.is_alive():
                    _raise_stop(admission, f"profile {key} join deadline exceeded")
                slice_timeout = 0.05 if remaining is None else min(0.05, remaining)
                await asyncio.to_thread(thread.join, slice_timeout)
            _we_stop_joined_keys.add(key)
            joined.append(key)

        with _registry_lock:
            _profile_workers_stop_done = True
        result = tuple(sorted(set(joined) | _we_stop_joined_keys))
        _we_stop_result = result
        return result
    except BaseException as exc:
        _we_stop_error = exc
        raise
    finally:
        _publish_we_stop_waiters(_we_stop_result, _we_stop_error)


async def stop_isolated_profile_workers(
    admission,
    *,
    producers_complete: bool,
    timeout: float | None = None,
) -> tuple[str, ...]:
    """Lifecycle-owned WE stop session: sentinel + join (not full graceful shutdown).

    Caller attests PTB producers are done via ``producers_complete`` (must reflect
    accepted Q-PTB1 proof — never invent True). Empty Queue is not sufficient:
    ``unfinished_tasks`` must be 0. Does not stop mixed/unbound workers.

    One owner procedure per process: repeat/concurrent waiters observe the same
    session (no second sentinel, no second destructive freeze). Cancelling a
    waiter detaches only that waiter; the owner continues. Workers that exit
    after this session's sentinel are observed as joined; workers dead *before*
    the sentinel are failure/remainder — not success.
    """

    global _we_stop_owner_task, _we_stop_owner_loop, _we_stop_admission_token
    from modules.antares.work_admission import AdmissionState, bound_admission

    if bound_admission() is not admission:
        _raise_stop(admission, "mixed/unbound profile workers are not stopped")
    if not producers_complete:
        _raise_stop(admission, "PTB producers are not complete")
    if admission.state is not AdmissionState.SEALED:
        _raise_stop(admission, "admission is not sealed")
    if admission.accepted_executor_futures():
        _raise_stop(admission, "accepted executor work remains")
    if _continuation_states(admission):
        _raise_stop(admission, "auto-enable continuation remains")

    # Terminal success is idempotent across loops (observe-only; no new owner).
    if _profile_workers_stop_done and _we_stop_result is not None:
        return _we_stop_result
    # Terminal failure: re-raise without starting a new owner on another loop.
    if _we_stop_error is not None and (
        _we_stop_owner_task is None or _we_stop_owner_task.done()
    ):
        raise _we_stop_error

    loop = asyncio.get_running_loop()
    token = id(admission)
    if _we_stop_admission_token is not None and _we_stop_admission_token != token:
        _raise_stop(admission, "WE stop owner-session bound to another admission")
    if _we_stop_owner_loop is not None and _we_stop_owner_loop is not loop:
        _raise_stop(admission, "WE stop owner-session bound to another event loop")

    # Pre-check unfinished before arming owner (fail closed without freeze).
    with _registry_lock:
        snapshot = dict(_profile_workers)
    for key, worker in snapshot.items():
        if worker.queue.unfinished_tasks:
            _raise_stop(
                admission,
                f"profile {key} is not drained unfinished={worker.queue.unfinished_tasks}",
            )

    _we_stop_admission_token = token
    _we_stop_owner_loop = loop

    if _we_stop_owner_task is None or _we_stop_owner_task.done():
        if _we_stop_result is not None:
            return _we_stop_result
        _we_stop_owner_task = loop.create_task(
            _owner_stop_isolated_profile_workers(admission, timeout=timeout),
            name="antares-we-stop-owner",
        )

    waiter: asyncio.Future = loop.create_future()
    _we_stop_waiters.append(waiter)
    # If owner already finished between checks, publish immediately.
    if _we_stop_owner_task.done() and not waiter.done():
        if _we_stop_error is not None:
            if not waiter.done():
                waiter.set_exception(_we_stop_error)
        elif _we_stop_result is not None:
            if not waiter.done():
                waiter.set_result(_we_stop_result)

    try:
        return await waiter
    except asyncio.CancelledError:
        try:
            _we_stop_waiters.remove(waiter)
        except ValueError:
            pass
        if not waiter.done():
            waiter.cancel()
        raise


# Owner-session entry point (see ``_owner_stop_isolated_profile_workers``).

