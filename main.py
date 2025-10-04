# main.py
import os
import logging
from typing import List
import threading

import pandas as pd

from utils.report_builder import build_report
from analyzers.selector import get_analyzer
from integrations.dropbox_watcher import list_files, download_file, upload_file, move_file
from integrations.telegram_bot import send_message_sync, send_file_sync
from utils.logger import logger
from scripts.card_events_report import run as send_card_events_report
from db.database import get_session
import load_data
from db.models import CardDisableHistory
from datetime import datetime

# -------------------------------
# Пути
# -------------------------------
LOCAL_DATA = "data"
LOCAL_REPORTS = "reports"
os.makedirs(LOCAL_DATA, exist_ok=True)
os.makedirs(LOCAL_REPORTS, exist_ok=True)

INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
OUTPUT_PATH = os.getenv("DROPBOX_OUTPUT_PATH")
PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")

# -------------------------------
# Флаг выполнения
# -------------------------------
is_running = False
lock = threading.Lock()

# -------------------------------
# Вспомогательные функции
# -------------------------------
def download_to_local(fname: str) -> str | None:
    dropbox_file_path = f"{INPUT_PATH}/{fname}"
    local_file_path = os.path.join(LOCAL_DATA, fname)
    try:
        download_file(dropbox_file_path, local_file_path)
        logger.info(f"Файл скачан: {fname}")
        return local_file_path
    except Exception as e:
        logger.error(f"Не удалось скачать {fname}: {e}")
        return None


def upload_report(local_path: str, fname: str):
    try:
        dropbox_report_path = f"{OUTPUT_PATH}/report_{fname}.xlsx"
        upload_file(local_path, dropbox_report_path)
        logger.info(f"Отчёт загружен в Dropbox: {dropbox_report_path}")
    except Exception as e:
        logger.error(f"Ошибка при загрузке отчёта {fname}: {e}")


def move_to_processed(fname: str):
    try:
        move_file(f"{INPUT_PATH}/{fname}", f"{PROCESSED_PATH}/{fname}")
        logger.info(f"Файл {fname} перемещён в {PROCESSED_PATH}")
    except Exception as e:
        logger.error(f"Ошибка при переносе {fname}: {e}")


def read_source_file(file_path: str) -> pd.DataFrame:
    try:
        if file_path.lower().endswith((".xlsx", ".xls")):
            return pd.read_excel(file_path, dtype=str)   # ✅ читаем как строки
        return pd.read_csv(
            file_path,
            sep=None,
            engine="python",
            encoding="utf-8",
            dtype=str,  # ✅ читаем как строки
        )
    except Exception:
        logger.exception(f"Не удалось загрузить данные из файла {file_path}")
        raise


def merge_card_dataframes(card_dfs: List[pd.DataFrame]) -> pd.DataFrame:
    base_columns = [
        "Карта", "Пул", "Направление", "Баланс",
        "Метод пополнения", "Имя", "Фамилия", "Bakai customer_id",
    ]

    if not card_dfs:
        return pd.DataFrame(columns=base_columns)

    combined = pd.concat(card_dfs, ignore_index=True, sort=False)
    for column in base_columns:
        if column not in combined.columns:
            combined[column] = None
    return combined[base_columns + [c for c in combined.columns if c not in base_columns]]

# -------------------------------
# Обработка одного файла
# -------------------------------
def process_file(fname: str, all_files: list[str]):
    logger.info(f"[{fname}] --- Начало обработки ---")

    local_file_path = download_to_local(fname)
    if not local_file_path:
        send_message_sync(f"❌ Не удалось скачать {fname}")
        return

    analyzer_func, config, requires_card = get_analyzer(fname)
    if not analyzer_func or not config:

        move_to_processed(fname)   # ⚡ сразу переносим в PROCESSED
        return

    card_files = []
    card_dataframes = []
    conversion_df_original = None
    is_conversion = bool(config.get("file_pattern") == "conversion")

    try:
        if is_conversion:
            conversion_df_original = read_source_file(local_file_path)
            logger.info(f"[{fname}] Конверсионный файл загружен: {len(conversion_df_original)} строк")

        if requires_card:
            for f in all_files:
                if "card" in f.lower():
                    local_card = download_to_local(f)
                    if local_card:
                        card_files.append(local_card)
                        if is_conversion:
                            card_df = read_source_file(local_card)
                            card_dataframes.append(card_df)
            logger.info(f"[{fname}] Найдено файлов для карты: {card_files}")

        # ✅ Записываем данные в БД
        if is_conversion and conversion_df_original is not None:
            card_df_for_db = merge_card_dataframes(card_dataframes)

            total_cards_in_file = len(card_df_for_db)
            logger.info(f"[{fname}] 📄 В card-файлах найдено {total_cards_in_file} карт.")

            # ✅ Сначала добавляем карты в БД
            with get_session() as session:
                load_data.process_cards(card_df_for_db, session)

            # ✅ Потом обрабатываем conversion (ивенты)
            with get_session() as session:
                load_data.process_conversion(card_df_for_db, conversion_df_original, session)

            # Логируем количество карт в БД
            with get_session() as session:
                from db.models import Card
                db_count = session.query(Card).count()
                logger.info(f"[{fname}] ✅ В таблице cards теперь {db_count} карт.")

        # ✅ Запуск анализатора
        result = analyzer_func(local_file_path, card_files, config.get("columns", {}))
        report_path = os.path.join(LOCAL_REPORTS, f"report_{fname}.xlsx")

        build_report(result, report_path)
        upload_report(report_path, fname)

        send_message_sync(f"✅ Отчёт по файлу {fname} готов")
        send_file_sync(report_path)

        # Проблемные карты (лист "Отключить")
        problem_cards_df = result.get("problem_cards")
        if problem_cards_df is not None and not problem_cards_df.empty:
            today = datetime.utcnow()
            with get_session() as session:
                for _, row in problem_cards_df.iterrows():
                    history = CardDisableHistory(
                        card_number=str(row["card"]),
                        disabled_at=today
                    )
                    session.add(history)
                session.commit()

            # Excel-отчёт остаётся без изменений
            # А вот сообщение в Telegram формируем иначе
            lines = [f"{row['card']} {row['partner']}" for _, row in problem_cards_df.iterrows()]
            text_for_telegram = "\n".join(lines)

            send_message_sync(f"⚠️ Карты на отключение:\n{text_for_telegram[:3900]}")

        move_to_processed(fname)

    except Exception as e:
        logger.exception(f"[{fname}] Ошибка при обработке: {e}")
        send_message_sync(f"❌ Ошибка при обработке {fname}: {e}")
        move_to_processed(fname)

# -------------------------------
# Основной цикл
# -------------------------------
def main_loop():
    global is_running
    with lock:
        if is_running:
            logger.warning("⚠️ main_loop пропущен — предыдущее выполнение ещё не завершено.")
            return
        is_running = True

    logger.info("🔍 Запуск боевого пайплайна (Dropbox)...")

    try:
        files = list_files(INPUT_PATH)
        if not files:
            logger.info("Нет файлов для обработки в Dropbox.")
            return

        for fname in files:
            process_file(fname, files)

        send_card_events_report()

    except Exception as e:
        logger.exception(f"Ошибка в основном процессе: {e}")
        send_message_sync(f"❌ Критическая ошибка: {e}")
    finally:
        with lock:
            is_running = False
        logger.info("✅ main_loop завершён")

if __name__ == "__main__":
    import time
    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
    while True:
        main_loop()
        time.sleep(CHECK_INTERVAL)
