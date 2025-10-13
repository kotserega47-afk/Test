# run_once_guard.py
import os
import time
import logging

LOCK_FILE = "/tmp/dropbox_pipeline.lock"

logger = logging.getLogger(__name__)

def acquire_lock(timeout: int = 600) -> bool:
    """
    Создаёт lock-файл, если пайплайн ещё не запущен.
    :param timeout: время (в секундах), после которого lock считается устаревшим.
    :return: True — если удалось захватить блокировку; False — если уже запущен другой экземпляр.
    """
    try:
        if os.path.exists(LOCK_FILE):
            # Проверяем, не устарел ли lock
            age = time.time() - os.path.getmtime(LOCK_FILE)
            if age < timeout:
                logger.info("🔒 Пайплайн уже запущен — пропуск итерации.")
                return False
            else:
                logger.warning("⚠️ Найден старый lock-файл, удаляем.")
                os.remove(LOCK_FILE)

        # Создаём новый lock
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        logger.info(f"🔐 Захвачена блокировка PID={os.getpid()}")
        return True

    except Exception as e:
        logger.exception(f"Ошибка при создании lock-файла: {e}")
        return False


def release_lock():
    """Удаляет lock-файл, если он существует."""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            logger.info("🔓 Блокировка снята.")
    except Exception as e:
        logger.exception(f"Ошибка при снятии lock-файла: {e}")
