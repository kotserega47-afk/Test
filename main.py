# main.py
import os
from datetime import datetime
from core.datetime_utils import now_msk
from analyzers.selector import get_analyzer
from integrations.dropbox_watcher import download_file, move_file
from integrations.telegram_bot import send_message_sync
from run_once_guard import acquire_lock, release_lock
from utils.logger import logger

DROPBOX_INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
DROPBOX_PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_ANALIZ")
LOCAL_TMP_PATH = "/tmp"

# Последний вспомогательный файл card/cd, если process_file вызывается без aux_filename
last_card_path = None

# Явный mapping для conversion
CONVERSION_COLUMNS = {
    "datetime": "Дата/Время создания",
    "card": "Карта",
    "partner": "Партнёр",
    "status": "Статус",
}


def _safe_send(msg: str) -> None:
    """Безопасная отправка Telegram-сообщения без повторного падения пайплайна."""
    try:
        if CHAT_ID:
            send_message_sync(msg, chat_id=CHAT_ID)
        else:
            logger.warning(f"CHAT_ID не задан, сообщение не отправлено: {msg}")
    except Exception as e:
        logger.error(f"⚠️ Не удалось отправить сообщение в Telegram: {e}")


def process_file(filename: str, aux_filename: str | None = None) -> bool:
    """
    Обрабатывает один файл из Dropbox.
    Возвращает True, если файл успешно проанализирован или корректно обработан как вспомогательный.
    Возвращает False, если анализ/скачивание/перемещение завершились ошибкой.
    """
    global last_card_path

    logger.info(f"=== Обработка файла {filename} ===")

    analyzer_func, config, requires_card = get_analyzer(filename)
    if not analyzer_func:
        msg = f"⚠️ Не найден анализатор для {filename}"
        logger.warning(msg)
        _safe_send(msg)
        return False

    local_path = os.path.join(LOCAL_TMP_PATH, filename)
    dropbox_path = f"{DROPBOX_INPUT_PATH}/{filename}"

    aux_local_path = None
    if aux_filename:
        aux_dropbox_path = f"{DROPBOX_INPUT_PATH}/{aux_filename}"
        aux_local_path = os.path.join(LOCAL_TMP_PATH, aux_filename)

        if not os.path.exists(aux_local_path):
            if download_file(aux_dropbox_path, aux_local_path):
                logger.info(f"🧩 Вспомогательный файл скачан: {aux_filename}")
                try:
                    aux_processed_path = (
                        f"{DROPBOX_PROCESSED_PATH}/"
                        f"{aux_filename[:-5]}_{now_msk().strftime('(%d.%m.%Y)')}.xlsx"
                    )
                    move_file(aux_dropbox_path, aux_processed_path)
                    logger.info(f"✅ Вспомогательный файл {aux_filename} перемещён в /processed.")
                except Exception as e:
                    logger.warning(
                        f"⚠️ Не удалось переместить вспомогательный файл {aux_filename}: {e}"
                    )
            else:
                logger.warning(f"⚠️ Не удалось скачать вспомогательный файл {aux_filename}")
                aux_local_path = None

    current_date = now_msk().strftime("%d.%m.%Y")
    name, ext = os.path.splitext(filename)
    filename_with_date = f"{name}_({current_date}){ext}"

    # 1. Скачиваем основной файл
    if not download_file(dropbox_path, local_path):
        msg = f"❌ Не удалось скачать файл {filename} из Dropbox."
        logger.error(msg)
        _safe_send(msg)
        return False

    lower_name = filename.lower()
    is_card_file = any(tag in lower_name for tag in ["card", "cd"])

    # 2. Вспомогательные файлы card/cd не анализируем здесь
    if is_card_file:
        last_card_path = local_path
        logger.info(f"🧩 Card/CD-файл загружен и сохранён: {filename}")

        try:
            move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename}")
            logger.info(f"✅ Файл {filename} перемещён в /processed.")
        except Exception as e:
            msg = f"⚠️ Ошибка при перемещении {filename}: {e}"
            logger.error(msg)
            _safe_send(msg)
            return False

        return True

    # 3. Подготавливаем аргументы анализатора
    try:
        logger.info(f"🚀 Запуск анализа {analyzer_func.__module__}.run()...")

        arg_name = "conv_file" if "conversion" in analyzer_func.__module__ else "payout_file"
        pair_path = aux_local_path or last_card_path

        if requires_card and not pair_path:
            msg = f"⚠️ Для {filename} не найден вспомогательный файл (card/cd). Анализ пропущен."
            logger.warning(msg)
            _safe_send(msg)
            return False

        kwargs = {
            arg_name: local_path,
            "card_files": [pair_path] if requires_card else [],
        }

        # Критично: conversion.run(...) требует col_mapping
        if "conversion" in analyzer_func.__module__:
            kwargs["col_mapping"] = CONVERSION_COLUMNS

        result = analyzer_func(**kwargs)
        summary = result.get("summary", {}) if isinstance(result, dict) else {}
        logger.info(f"✅ Анализ завершён: {summary}")

    except Exception as e:
        msg = f"❌ Ошибка в анализаторе {analyzer_func.__module__} для {filename}: {e}"
        logger.exception(msg)
        _safe_send(msg)
        return False

    # 4. Перемещаем основной файл в processed
    try:
        move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename_with_date}")
        logger.info(f"✅ Файл {filename} перемещён в /processed.")
    except Exception as e:
        msg = f"⚠️ Ошибка при перемещении {filename}: {e}"
        logger.error(msg)
        _safe_send(msg)
        return False

    return True


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Использование: python main.py <имя_файла> [aux_filename]")
        raise SystemExit(0)

    if not acquire_lock(timeout=600):
        logger.info("⏳ Анализ уже выполняется, повторный запуск пропущен.")
        raise SystemExit(0)

    try:
        filename = sys.argv[1]
        aux_filename = sys.argv[2] if len(sys.argv) > 2 else None
        ok = process_file(filename, aux_filename=aux_filename)
        raise SystemExit(0 if ok else 1)
    finally:
        release_lock()