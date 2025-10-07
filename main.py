# main.py
"""
Главный модуль обработки новых файлов:
- выполняет быстрый приоритетный анализ (run_fast);
- мгновенно формирует список карт на отключение;
- запускает фоновый полный анализ (run) для отчёта, Telegram и Dropbox.
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


# -----------------------------
# Настройки путей
# -----------------------------
DROPBOX_INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
DROPBOX_PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")
LOCAL_TMP_PATH = "/tmp"  # временное хранилище Railway / Linux


# -----------------------------
# Основная функция
# -----------------------------
def process_file(filename: str) -> None:
    logger.info(f"=== Обработка файла {filename} ===")

    local_path = os.path.join(LOCAL_TMP_PATH, filename)
    dropbox_path = f"{DROPBOX_INPUT_PATH}/{filename}"

    # 1️⃣ Скачивание
    if not download_file(dropbox_path, local_path):
        msg = f"❌ Не удалось скачать файл {filename} из Dropbox."
        logger.error(msg)
        send_message_sync(msg)
        return

    # 2️⃣ Быстрый анализ (критический путь)
    try:
        logger.info("🚀 Запуск ускоренного анализа run_fast()...")
        result_fast = conversion.run_fast(
            conv_file=local_path,
            card_files=[],
            col_mapping=conversion.COLUMNS,
            generate_excel=False
        )
        problem_cards_df = result_fast.get("problem_cards", pd.DataFrame())
        summary = result_fast.get("summary", {})
        logger.info(f"✅ Быстрый анализ завершён: {len(problem_cards_df)} карт для проверки.")
    except Exception as e:
        msg = f"❌ Ошибка при выполнении run_fast для {filename}: {e}"
        logger.exception(msg)
        send_message_sync(msg)
        return

    # 3️⃣ Запись карт на отключение
    try:
        if not problem_cards_df.empty:
            unique_cards = problem_cards_df.drop_duplicates(subset=["card"])
            today = datetime.utcnow()
            added = 0
            with get_session() as session:
                for _, row in unique_cards.iterrows():
                    card_number = str(row["card"]).strip()
                    if not card_number:
                        continue
                    exists = session.query(CardDisableHistory).filter_by(card_number=card_number).first()
                    if exists:
                        continue
                    session.add(CardDisableHistory(card_number=card_number, disabled_at=today))
                    added += 1
            if added:
                msg = f"🚫 Отключить карты ({added} шт):\n" + "\n".join(unique_cards["card"])
                send_message_sync(msg)
                logger.info(f"В историю добавлено {added} отключений.")
            else:
                logger.info("Новых карт для отключения не найдено.")
        else:
            logger.info("Нет карт для отключения.")
    except Exception as e:
        logger.exception(f"Ошибка при записи отключаемых карт: {e}")
        send_message_sync(f"⚠️ Ошибка при записи отключаемых карт: {e}")

    # 4️⃣ Telegram уведомление о завершении критического этапа
    try:
        summary_text = (
            f"✅ Анализ *{filename}* завершён.\n"
            f"Карт в работе: {summary.get('Карт в работе', '—')}\n"
            f"На отключение: {summary.get('Карты на отключение', '—')}"
        )
        send_message_sync(summary_text)
    except Exception as e:
        logger.exception(f"Ошибка при отправке Telegram уведомления: {e}")

    # 5️⃣ Фоновый полный анализ (Excel + Telegram-файл + Dropbox)
    def full_analysis():
        try:
            logger.info("🕓 Запуск полного анализа run() в фоне...")
            result_full = conversion.run(
                conv_file=local_path,
                card_files=[],
                col_mapping=conversion.COLUMNS
            )

            workbook = result_full.get("workbook")
            if not workbook:
                logger.warning(f"⚠️ run() не вернул workbook для {filename}")
                return

            output_path = os.path.join(LOCAL_TMP_PATH, f"report_{filename}")
            workbook.save(output_path)
            send_file_sync(output_path, caption=f"📊 Отчёт по {filename}")
            logger.info(f"📁 Отчёт {output_path} отправлен в Telegram.")

            move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename}")
            logger.info(f"Файл {filename} перемещён в /processed.")
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

    # --- защита от параллельного запуска ---
    if not acquire_lock(timeout=600):  # 10 минут защиты
        sys.exit(0)

    try:
        filename = sys.argv[1]
        process_file(filename)
    finally:
        release_lock()
