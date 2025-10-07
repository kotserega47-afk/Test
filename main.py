# main.py
"""
Главный модуль обработки новых файлов:
- определяет нужный анализатор;
- запускает обработку данных;
- формирует Excel-отчёт;
- отправляет уведомления в Telegram;
- фиксирует отключаемые карты;
- перемещает обработанные файлы в Dropbox /processed.
"""

import os
import pandas as pd
from datetime import datetime

from db.database import get_session
from db.models import CardDisableHistory
from integrations.telegram_bot import send_message_sync, send_file_sync
from integrations.dropbox_watcher import download_file, move_file
from utils.excel_utils import flatten_lists_in_df, write_df_to_sheet
from analyzers import conversion
from utils.logger import logger

from openpyxl import Workbook


# -----------------------------
# Настройки путей из окружения
# -----------------------------
DROPBOX_INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
DROPBOX_PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")
LOCAL_TMP_PATH = "/tmp"  # временное хранилище Railway / Linux

# -----------------------------
# Основная функция обработки файла
# -----------------------------
def process_file(filename: str) -> None:
    """
    Обработка одного файла из Dropbox:
    1. Скачивание
    2. Анализ через analyzers/conversion
    3. Сохранение Excel-отчёта
    4. Telegram-уведомления
    5. Запись карт на отключение
    6. Перемещение в /processed
    """

    logger.info(f"=== Обработка файла {filename} ===")

    local_path = os.path.join(LOCAL_TMP_PATH, filename)
    dropbox_path = f"{DROPBOX_INPUT_PATH}/{filename}"

    # 1️⃣ Скачивание
    if not download_file(dropbox_path, local_path):
        msg = f"❌ Не удалось скачать файл {filename} из Dropbox."
        logger.error(msg)
        send_message_sync(msg)
        return

    # 2️⃣ Анализ файла
    try:
        logger.info(f"Запуск анализа для {filename}")

        result = conversion.run(
            conv_file=local_path,
            card_files=[],
            col_mapping=conversion.COLUMNS
        )

        if not result:
            raise ValueError("Анализатор не вернул результат")

        problem_cards_df = result.get("problem_cards")
        summary = result.get("summary")
        workbook = result.get("workbook")

    except Exception as e:
        msg = f"❌ Ошибка при анализе {filename}: {e}"
        logger.exception(msg)
        send_message_sync(msg)
        return

    # 3️⃣ Сохранение отчёта
    output_path = os.path.join(LOCAL_TMP_PATH, f"report_{filename}")
    if workbook:
        try:
            workbook.save(output_path)
            logger.info(f"Отчёт сохранён: {output_path}")
        except Exception as e:
            logger.exception(f"Ошибка при сохранении отчёта для {filename}: {e}")
            send_message_sync(f"⚠️ Ошибка при сохранении отчёта для {filename}: {e}")
            return
    else:
        logger.warning(f"Анализатор не вернул workbook для {filename}")

    # 4️⃣ Уведомление Telegram
    try:
        summary_text = (
            f"✅ Анализ файла *{filename}* завершён успешно.\n"
            f"Карт в работе: {summary.get('Карт в работе', '—')}\n"
            f"На отключение: {summary.get('Карты на отключение', '—')}"
        )
        send_message_sync(summary_text)
        if workbook:
            send_file_sync(output_path, caption=f"📊 Отчёт по {filename}")
        logger.info("Отчёт отправлен в Telegram")
    except Exception as e:
        logger.exception(f"Ошибка отправки отчёта в Telegram: {e}")

    # 5️⃣ Запись отключаемых карт
    try:
        if problem_cards_df is None or problem_cards_df.empty:
            logger.info("Проблемных карт нет — отключений не требуется.")
        else:
            unique_cards = problem_cards_df.drop_duplicates(subset=["card"])
            today = datetime.utcnow()

            added = 0
            with get_session() as session:
                for _, row in unique_cards.iterrows():
                    card_number = str(row["card"]).strip()
                    if not card_number:
                        continue
                    exists = (
                        session.query(CardDisableHistory)
                        .filter_by(card_number=card_number)
                        .first()
                    )
                    if exists:
                        continue
                    session.add(CardDisableHistory(card_number=card_number, disabled_at=today))
                    added += 1

            if added:
                cards_list = "\n".join(unique_cards["card"].astype(str))
                send_message_sync(f"🚫 Отключить карты ({added} шт):\n{cards_list}")
                logger.info(f"В историю добавлено {added} отключений.")
            else:
                logger.info("Новых карт для отключения не найдено.")

    except Exception as e:
        logger.exception(f"Ошибка при записи отключаемых карт: {e}")
        send_message_sync(f"⚠️ Ошибка при записи отключаемых карт: {e}")

    # 6️⃣ Перемещение обработанного файла
    try:
        move_file(dropbox_path, f"{DROPBOX_PROCESSED_PATH}/{filename}")
        logger.info(f"Файл {filename} перемещён в /processed.")
    except Exception as e:
        logger.exception(f"Ошибка при перемещении файла {filename}: {e}")
        send_message_sync(f"⚠️ Не удалось переместить {filename} в /processed.")

def save_disabled_cards(problem_cards_df: pd.DataFrame):
    """Сохраняет отключаемые карты в БД без дублей и уведомляет Telegram"""
    if problem_cards_df is None or problem_cards_df.empty:
        logger.info("Нет карт для отключения")
        return

    unique_cards = problem_cards_df.drop_duplicates(subset=["card"])
    today = datetime.utcnow()

    saved = 0
    with get_session() as session:
        for _, row in unique_cards.iterrows():
            card_num = str(row["card"]).strip()
            if not card_num:
                continue

            # Проверяем, есть ли уже в истории
            exists = (
                session.query(CardDisableHistory)
                .filter_by(card_number=card_num)
                .first()
            )
            if exists:
                continue

            session.add(
                CardDisableHistory(card_number=card_num, disabled_at=today)
            )
            saved += 1

    if saved:
        msg = "\n".join(unique_cards["card"].astype(str))
        send_message_sync(f"🚫 Отключить карты ({saved} шт):\n{msg}")
        logger.info(f"Добавлено {saved} записей в историю отключений.")
    else:
        logger.info("Новых карт для отключения не найдено.")


# -----------------------------
# Точка входа
# -----------------------------
from run_once_guard import acquire_lock, release_lock

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Использование: python main.py <имя_файла>")
        sys.exit(0)

    # --- защита от параллельного запуска ---
    if not acquire_lock(timeout=600):  # 10 мин защиты
        sys.exit(0)

    try:
        filename = sys.argv[1]
        process_file(filename)
    finally:
        release_lock()