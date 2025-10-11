# main.py
"""
Главный модуль обработки новых файлов:
- анализирует только conversion-файлы;
- использует последний card-файл как справочник;
- выполняет быстрый анализ + формирует отчёт;
- отправляет результаты в Telegram;
- перемещает обработанные файлы в Dropbox /processed.
"""

import os
import pandas as pd
from datetime import datetime
from threading import Thread

from db.database import get_session
from db.models import CardDisableHistory
from integrations.telegram_bot import send_message_sync, send_file_sync
from integrations.dropbox_watcher import download_file, move_file
from analyzers import conversion
from utils.logger import logger
from run_once_guard import acquire_lock, release_lock

DROPBOX_INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
DROPBOX_PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")
LOCAL_TMP_PATH = "/tmp"

# 🧩 сохраняем путь к последнему card-файлу
last_card_path = None


def process_file(filename: str) -> None:
    global last_card_path

    logger.info(f"=== Обработка файла {filename} ===")
    local_path = os.path.join(LOCAL_TMP_PATH, filename)
    dropbox_path = f"{DROPBOX_INPUT_PATH}/{filename}"

    # 1️⃣ Скачиваем файл
    if not download_file(dropbox_path, local_path):
        msg = f"❌ Не удалось скачать файл {filename} из Dropbox."
        logger.error(msg)
        send_message_sync(msg)
        return

    is_card_file = "card" in filename.lower()

    # 2️⃣ Если это card-файл — просто запоминаем путь
    if is_card_file:
        last_card_path = local_path
        logger.info(f"🧩 Card-файл загружен и сохранён: {filename}")
        move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename}")
        logger.info(f"✅ Card-файл {filename} перемещён в /processed.")
        return

    # 3️⃣ Обработка conversion-файла
    card_files = [last_card_path] if last_card_path else []
    col_mapping = conversion.COLUMNS

    problem_cards_df = pd.DataFrame()
    summary = {}

    try:
        logger.info("🚀 Запуск ускоренного анализа run_fast()...")
        result_fast = conversion.run_fast(
            conv_file=local_path,
            card_files=card_files,
            col_mapping=col_mapping,
            generate_excel=False
        )
        problem_cards_df = result_fast.get("problem_cards", pd.DataFrame())
        summary = result_fast.get("summary", {})
        logger.info(f"✅ Быстрый анализ завершён: {len(problem_cards_df)} карт для проверки.")
    except Exception as e:
        msg = f"❌ Ошибка в run_fast для {filename}: {e}"
        logger.exception(msg)
        send_message_sync(msg)
        return

    # 4️⃣ Формируем и отправляем список карт на отключение
    if not problem_cards_df.empty:
        try:
            if all(col in problem_cards_df.columns for col in ["card", "partner", "max_consecutive_errors"]):
                card_lines = [
                    f"{row['card']} {row['partner']} {row['max_consecutive_errors']}"
                    for _, row in (
                        problem_cards_df[["card", "partner", "max_consecutive_errors"]]
                        .dropna()
                        .astype(str)
                        .drop_duplicates()
                        .sort_values(by=["partner", "card"])
                        .iterrows()
                    )
                ]

                total = len(card_lines)
                BATCH_SIZE = 500
                for i in range(0, total, BATCH_SIZE):
                    chunk = card_lines[i:i + BATCH_SIZE]
                    msg = "🚫 Карты на отключение:\n" + "\n".join(chunk)
                    send_message_sync(msg)
                logger.info(f"Отправлен список {total} карт с ошибками.")
            else:
                send_message_sync("⚠️ Пропущено формирование списка: отсутствуют нужные колонки.")
        except Exception as e:
            logger.exception(f"Ошибка при формировании списка карт: {e}")
            send_message_sync(f"⚠️ Ошибка при формировании списка карт: {e}")
    else:
        logger.info("Нет карт, превысивших порог ошибок.")

    # 5️⃣ Telegram уведомление об общем результате fast-run
    try:
        summary_text = (
            f"✅ Анализ *{filename}* завершён.\n"
            f"Карт в работе: {summary.get('Карт в работе', '—')}\n"
            f"На отключение: {summary.get('Карты на отключение', '—')}"
        )
        send_message_sync(summary_text)
    except Exception as e:
        logger.exception(f"Ошибка при отправке Telegram уведомления: {e}")

    # 6️⃣ Фоновый полный анализ (run)
    def full_analysis():
        try:
            logger.info(f"🕓 Полный анализ для {filename}")
            result_full = conversion.run(
                conv_file=local_path,
                card_files=card_files,
                col_mapping=col_mapping
            )
            workbook = result_full.get("workbook")
            if workbook:
                output_path = os.path.join(LOCAL_TMP_PATH, f"report_{filename}")
                workbook.save(output_path)
                send_file_sync(output_path, caption=f"📊 Отчёт по {filename}")
                logger.info(f"📁 Отчёт отправлен: {output_path}")
            else:
                logger.warning(f"⚠️ run() не вернул workbook для {filename}")

            move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename}")
            logger.info(f"✅ Файл {filename} перемещён в /processed.")
        except Exception as e:
            logger.exception(f"Ошибка фонового анализа: {e}")
            send_message_sync(f"⚠️ Ошибка фонового анализа {filename}: {e}")

    Thread(target=full_analysis, daemon=True).start()


# -----------------------------
# Точка входа
# -----------------------------
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
