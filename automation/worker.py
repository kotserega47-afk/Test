# automation/worker.py

from __future__ import annotations

import os
import traceback
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from queue import Queue
from typing import Sequence, Union

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from automation.audit import log_timing
from automation.engine import run, RunConfig
from automation.runtime import (
    CONVERSION_AUTO_PROFILE,
    WalletEditorTask,
    build_wallet_editor_result_path,
)
from core.datetime_utils import now_msk
from integrations.wallet_editor_auto_enable_eligibility import CandidateRow
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings
from integrations.wallet_editor_registry_async import (
    schedule_registry_append,
    stage_registry_result_copy,
)
from transport.telegram_transport import send_text, send_document

icon, name = LOG_PROFILES["AUTOMATION"]
log = get_logger(name, icon)

_registry_lock = threading.Lock()

ProfileQueueItem = Union[WalletEditorTask, "WalletEditorAutoEnableBatchTask"]


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


@dataclass
class _ProfileWorker:
    queue: Queue[ProfileQueueItem] = field(default_factory=Queue)
    thread: threading.Thread | None = None


_profile_workers: dict[str, _ProfileWorker] = {}


def ensure_worker_started() -> None:
    """Legacy bootstrap for scheduler.py — workers start lazily per profile on add_task."""
    log.debug("🟢 [Worker] ensure_worker_started() — lazy per-profile workers")


def _ensure_profile_worker(profile_key: str) -> _ProfileWorker:
    with _registry_lock:
        worker = _profile_workers.get(profile_key)
        if worker is not None:
            return worker

        worker = _ProfileWorker()
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


def add_task(task: WalletEditorTask) -> int:
    worker = _ensure_profile_worker(task.operator_profile)
    worker.queue.put(task)
    queue_size = worker.queue.qsize()
    log.info(
        f"📥 [Queue] profile={task.operator_profile} queue_size={queue_size} "
        f"chat_id={task.chat_id} user_id={task.telegram_user_id} file={task.file_path}"
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
    queue_size = worker.queue.qsize()
    log.info(
        "[AutoEnable] queued profile=%s queue_size=%s batch_size=%s",
        profile,
        queue_size,
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


def _run_disable_task(profile_key: str, task: WalletEditorTask) -> None:
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
    registry_result_path, registry_is_copy = stage_registry_result_copy(result_file)
    schedule_registry_append(
        task,
        registry_result_path,
        stats,
        run_started_at=run_started_at,
        run_finished_at=run_finished_at,
        is_staged_copy=registry_is_copy,
        output_file=user_output_file,
    )

    threading.Thread(
        target=delayed_cleanup,
        args=(result_file, task.file_path),
        daemon=True,
    ).start()


def _run_auto_enable_batch_task(profile_key: str, task: WalletEditorAutoEnableBatchTask) -> None:
    from integrations.wallet_editor_auto_enable_executor import EnableOutcome, execute_enable_batch

    batch_size = len(task.candidates)
    log.info(
        "[AutoEnable] started profile=%s batch_size=%s",
        profile_key,
        batch_size,
    )
    try:
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
        task.result_future.set_result(outcomes)
        log.info(
            "[AutoEnable] finished profile=%s batch_size=%s outcomes=%s",
            profile_key,
            batch_size,
            len(outcomes),
        )
    except Exception as exc:
        log.exception("[AutoEnable] failed profile=%s batch_size=%s", profile_key, batch_size)
        task.result_future.set_exception(exc)


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


def _is_auto_enable_batch_item(item: ProfileQueueItem) -> bool:
    return hasattr(item, "result_future") and hasattr(item, "candidates")


def _is_disable_task_item(item: ProfileQueueItem) -> bool:
    return hasattr(item, "file_path") and hasattr(item, "chat_id")


def worker_loop(profile_key: str, task_queue: Queue[ProfileQueueItem]) -> None:
    log.info(f"🟢 [Worker] profile={profile_key} worker_loop started")

    while True:
        item = task_queue.get()
        _log_queue_wait(profile_key, item)
        try:
            if _is_auto_enable_batch_item(item):
                _run_auto_enable_batch_task(profile_key, item)  # type: ignore[arg-type]
            elif _is_disable_task_item(item):
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
                log.error(
                    "❌ [Worker] profile=%s unknown queue item type=%s",
                    profile_key,
                    type(item).__name__,
                )
        finally:
            task_queue.task_done()
