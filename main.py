# main.py
"""
Главный модуль обработки новых файлов:
- анализирует card и conversion файлы;
- выполняет быстрый анализ для conversion;
- обновляет базу и формирует отчёт;
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


def process_file(filename: str) -> None:
    logger.info(f"=== Обработка файла {filename} ===")
    local_path = os.path.join(LOCAL_TMP_PATH, filename)
    dropbox_path = f"{DROPBOX_INPUT_PATH}/{filename}"

    # 1️⃣ Скачиваем файл
    if not download_file(dropbox_path, local_path):
        msg = f"❌ Не удалось скачать файл {filename} из Dropbox."
        logger.error(msg)
        send_message_sync(msg)
        return

    # 2️⃣ Определяем тип и маппинг
    is_card_file = "card" in filename.lower()
    col_mapping = conversion.COLUMNS
    if is_card_file and hasattr(conversion, "CONFIG") and "columns_card" in conversion.CONFIG:
        col_mapping = conversion.CONFIG["columns_card"]
        logger.info(f"🧩 Используется columns_card для {filename}")

    # 3️⃣ Быстрый анализ — только для conversion
    problem_cards_df = pd.DataFrame()
    summary = {}
    if not is_card_file:
        try:
            logger.info("🚀 Запуск ускоренного анализа run_fast()...")
            result_fast = conversion.run_fast(
                conv_file=local_path,
                card_files=[],
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

        # 4️⃣ Формируем список карт на отключение (по бизнес-логике)
        if not problem_cards_df.empty:
            try:
                # Подготовка: карта + партнёр, отсортировано по партнёру
                card_partner_list = (
                    problem_cards_df[["card", "partner"]]
                    .dropna()
                    .astype(str)
                    .drop_duplicates()
                    .sort_values(by=["partner", "card"])
                )

                # Формируем строки вида: 123456******7890 — А-Мобайл
                card_lines = [f"{row['card']} — {row['partner']}" for _, row in card_partner_list.iterrows()]
                total = len(card_lines)
                BATCH_SIZE = 500

                # Отправка сообщений по 500 строк
                for i in range(0, total, BATCH_SIZE):
                    chunk = card_lines[i:i + BATCH_SIZE]
                    msg = (
                            f"🚫 Карты с превышением порога (строки {i + 1}–{i + len(chunk)} из {total}):\n"
                            + "\n".join(chunk)
                    )
                    send_message_sync(msg)
                logger.info(f"Отправлен список {len(unique_cards)} карт с ошибками.")
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

    # 6️⃣ Полный анализ — общий для обоих типов файлов
    def full_analysis():
        try:
            file_type = "CARD" if is_card_file else "CONVERSION"
            logger.info(f"🕓 Полный анализ ({file_type}) для {filename}")

            result_full = conversion.run(
                conv_file=local_path,
                card_files=[local_path] if is_card_file else [],
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

    if not acquire_lock(timeout=600):  # 10 минут защиты
        sys.exit(0)

    try:
        filename = sys.argv[1]
        process_file(filename)
    finally:
        release_lock()
