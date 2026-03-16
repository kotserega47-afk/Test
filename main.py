# main.py
"""
Главный модуль обработки новых файлов:
- анализирует conversion-файлы с помощью analyzers/conversion.py;
- использует последний card-файл как справочник;
- запускает анализ, Telegram и формирование отчёта;
- перемещает обработанные файлы в Dropbox /processed.
"""

import os
from datetime import datetime

from utils.logger import logger
from integrations.telegram_bot import send_message_sync
from integrations.dropbox_watcher import download_file, move_file
from run_once_guard import acquire_lock, release_lock
from analyzers.selector import get_analyzer

DROPBOX_INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
DROPBOX_PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_ANALIZ")
LOCAL_TMP_PATH = "/tmp"

# сохраняем путь к последнему card/cd-файлу
last_card_path = None


def _safe_send(msg: str) -> None:
    """Безопасная отправка в Telegram, чтобы уведомления не роняли пайплайн."""
    try:
        if CHAT_ID:
            send_message_sync(msg, chat_id=CHAT_ID)
        else:
            logger.warning(f"CHAT_ID не задан, сообщение не отправлено: {msg}")
    except Exception as e:
        logger.error(f"⚠️ Не удалось отправить сообщение в Telegram: {e}")


def process_file(filename: str, aux_filename: str | None = None) -> None:
    """Обработка одного файла из Dropbox."""
    global last_card_path

    logger.info(f"=== Обработка файла {filename} ===")

    analyzer_func, config, requires_card = get_analyzer(filename)
    if not analyzer_func:
        logger.warning(f"⚠️ Не найден анализатор для {filename}")
        return

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
                        f"{aux_filename[:-5]}_{datetime.now().strftime('(%d.%m.%Y)')}.xlsx"
                    )
                    move_file(aux_dropbox_path, aux_processed_path)
                    logger.info(f"✅ Вспомогательный файл {aux_filename} перемещён в /processed.")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось переместить вспомогательный файл {aux_filename}: {e}")
            else:
                logger.warning(f"⚠️ Не удалось скачать вспомогательный файл {aux_filename}")
                aux_local_path = None

    current_date = datetime.now().strftime("%d.%m.%Y")
    name, ext = os.path.splitext(filename)
    filename_with_date = f"{name}_({current_date}){ext}"

    # 1) скачиваем файл
    if not download_file(dropbox_path, local_path):
        msg = f"❌ Не удалось скачать файл {filename} из Dropbox."
        logger.error(msg)
        _safe_send(msg)
        return

    is_card_file = any(tag in filename.lower() for tag in ["card", "cd"])

    # 2) card/cd-файлы только сохраняем как вспомогательные
    if is_card_file:
        last_card_path = local_path
        logger.info(f"🧩 Card/CD-файл загружен и сохранён: {filename}")
        move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename}")
        logger.info(f"✅ Файл {filename} перемещён в /processed.")
        return

    # 3) запускаем анализатор
    try:
        logger.info(f"🚀 Запуск анализа {analyzer_func.__module__}.run()...")

        arg_name = "conv_file" if "conversion" in analyzer_func.__module__ else "payout_file"
        pair_path = aux_local_path or last_card_path

        if requires_card and not pair_path:
            msg = f"⚠️ Для {filename} не найден вспомогательный файл (card/cd). Анализ пропущен."
            logger.warning(msg)
            _safe_send(msg)
            return

        kwargs = {
            arg_name: local_path,
            "card_files": [pair_path] if requires_card else [],
        }

        # Для conversion не тянем conversion.COLUMNS из модуля:
        # в analyzer есть дефолтный mapping в сигнатуре run(), этого достаточно.
        result = analyzer_func(**kwargs)

        summary = result.get("summary", {}) if isinstance(result, dict) else {}
        logger.info(f"✅ Анализ завершён: {summary}")

    except Exception as e:
        msg = f"❌ Ошибка в анализаторе {analyzer_func.__module__} для {filename}: {e}"
        logger.exception(msg)
        _safe_send(msg)
        return

    # 4) перемещаем обработанный файл
    try:
        move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename_with_date}")
        logger.info(f"✅ Файл {filename} перемещён в /processed.")
    except Exception as e:
        msg = f"⚠️ Ошибка при перемещении {filename}: {e}"
        logger.error(msg)
        _safe_send(msg)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Использование: python main.py <имя_файла>")
        sys.exit(0)

    if not acquire_lock(timeout=600):
        sys.exit(0)

    try:
        filename = sys.argv[1]
        process_file(filename)
    finally:
        release_lock()
