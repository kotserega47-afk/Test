# automation/worker.py

import os
import traceback
import threading
import time
from queue import Queue

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from automation.engine import run, RunConfig
from automation.runtime import WalletEditorTask
from transport.telegram_transport import send_text, send_document

icon, name = LOG_PROFILES["AUTOMATION"]
log = get_logger(name, icon)

task_queue: Queue[WalletEditorTask] = Queue()

_worker_started = False
_worker_start_lock = threading.Lock()


def ensure_worker_started() -> None:
    """Start WalletEditor worker daemon once per process."""
    global _worker_started
    with _worker_start_lock:
        if _worker_started:
            return
        threading.Thread(
            target=worker_loop,
            daemon=True,
            name="wallet-editor-worker",
        ).start()
        _worker_started = True
        log.info("🟢 [Worker] daemon thread registered")


def add_task(task: WalletEditorTask) -> None:
    log.info(
        f"📥 [Queue] profile={task.operator_profile} chat_id={task.chat_id} "
        f"user_id={task.telegram_user_id} file={task.file_path}"
    )
    task_queue.put(task)


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


def worker_loop():
    log.info("🟢 [Worker] Запущен worker_loop")

    while True:
        task = task_queue.get()
        log.info(
            f"🚀 [Worker] Взята задача profile={task.operator_profile} file={task.file_path}"
        )

        try:
            cfg = RunConfig(
                login=task.login,
                password=task.password,
                auth_state_path=task.auth_state_path,
            )

            log.info("📊 [Worker] Запуск engine.run()")
            result_file, stats = run(task.file_path, cfg)

            summary = stats.summary()
            log.info(f"✅ [Worker] Готово: {summary}")

            send_text(
                chat_id=str(task.chat_id),
                text=f"📊 {summary}"
            )

            log.info(f"📤 [Worker] Отправка файла: {result_file}")
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
            log.error(f"❌ [Worker] Ошибка: {e}")
            log.error(traceback.format_exc())

            send_text(
                chat_id=str(task.chat_id),
                text=f"❌ Ошибка: {e}"
            )

        finally:
            task_queue.task_done()
