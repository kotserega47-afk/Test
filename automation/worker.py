# automation/worker.py

import os
import traceback
import threading
import time
from queue import Queue

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from automation.engine import run, RunConfig
from transport.telegram_transport import send_text, send_document

icon, name = LOG_PROFILES["AUTOMATION"]
log = get_logger(name, icon)

# очередь задач (file_path, chat_id)
task_queue: Queue = Queue()

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


def add_task(file_path: str, chat_id: int):
    log.info(f"📥 [Queue] Добавлена задача: {file_path}, chat_id={chat_id}")
    task_queue.put((file_path, chat_id))


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
        file_path, chat_id = task_queue.get()
        log.info(f"🚀 [Worker] Взята задача: {file_path}")

        try:
            cfg = RunConfig()

            log.info("📊 [Worker] Запуск engine.run()")
            result_file, stats = run(file_path, cfg)

            summary = stats.summary()
            log.info(f"✅ [Worker] Готово: {summary}")

            # отправка summary
            send_text(
                chat_id=str(chat_id),
                text=f"📊 {summary}"
            )

            # отправка файла
            log.info(f"📤 [Worker] Отправка файла: {result_file}")
            send_document(
                path=result_file,
                chat_id=str(chat_id),
                caption="Результат обработки"
            )

            # cleanup в фоне (ВАЖНО)
            threading.Thread(
                target=delayed_cleanup,
                args=(result_file, file_path),
                daemon=True
            ).start()

        except Exception as e:
            log.error(f"❌ [Worker] Ошибка: {e}")
            log.error(traceback.format_exc())

            send_text(
                chat_id=str(chat_id),
                text=f"❌ Ошибка: {e}"
            )

        finally:
            task_queue.task_done()
