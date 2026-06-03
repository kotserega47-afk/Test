# automation/worker.py

import os
import traceback
import threading
import time
from dataclasses import dataclass, field
from queue import Queue

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from automation.engine import run, RunConfig
from automation.runtime import WalletEditorTask, build_wallet_editor_result_path
from core.datetime_utils import now_msk
from integrations.wallet_editor_registry import append_run_to_dropbox_registry
from transport.telegram_transport import send_text, send_document

icon, name = LOG_PROFILES["AUTOMATION"]
log = get_logger(name, icon)

_registry_lock = threading.Lock()


@dataclass
class _ProfileWorker:
    queue: Queue[WalletEditorTask] = field(default_factory=Queue)
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


def worker_loop(profile_key: str, task_queue: Queue[WalletEditorTask]) -> None:
    log.info(f"🟢 [Worker] profile={profile_key} worker_loop started")

    while True:
        task = task_queue.get()
        log.info(
            f"🚀 [Worker] profile={profile_key} file={task.file_path}"
        )

        try:
            run_started_at = now_msk()
            cfg = RunConfig(
                login=task.login,
                password=task.password,
                auth_state_path=task.auth_state_path,
                result_file_path=build_wallet_editor_result_path(
                    task.source_file_name,
                    task.operator_profile,
                ),
            )

            log.info(f"📊 [Worker] profile={profile_key} engine.run()")
            result_file, stats = run(task.file_path, cfg)
            run_finished_at = now_msk()

            append_run_to_dropbox_registry(
                task,
                result_file,
                stats,
                run_started_at=run_started_at,
                run_finished_at=run_finished_at,
            )

            summary = stats.summary()
            log.info(f"✅ [Worker] profile={profile_key} done: {summary}")

            send_text(
                chat_id=str(task.chat_id),
                text=f"📊 {summary}"
            )

            log.info(f"📤 [Worker] profile={profile_key} sending file: {result_file}")
            send_document(
                path=result_file,
                chat_id=str(task.chat_id),
                caption="Результат обработки"
            )

            threading.Thread(
                target=delayed_cleanup,
                args=(result_file, task.file_path),
                daemon=True
            ).start()

        except Exception as e:
            log.error(f"❌ [Worker] profile={profile_key} error: {e}")
            log.error(traceback.format_exc())

            send_text(
                chat_id=str(task.chat_id),
                text=f"❌ Ошибка: {e}"
            )

        finally:
            task_queue.task_done()
