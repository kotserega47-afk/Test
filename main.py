import os
import logging
from typing import List

import pandas as pd

from utils.report_builder import build_report
from analyzers.selector import get_analyzer
from integrations.dropbox_watcher import list_files, download_file, upload_file, move_file
from integrations.telegram_bot import send_message_sync, send_file_sync
from utils.logger import logger
from scripts.card_events_report import run as send_card_events_report

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
# Вспомогательные функции
# -------------------------------
def download_to_local(fname: str) -> str | None:
    """Скачивает файл из Dropbox в локальную папку"""
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
    """Загружает готовый отчёт в Dropbox"""
    try:
        dropbox_report_path = f"{OUTPUT_PATH}/report_{fname}.xlsx"
        upload_file(local_path, dropbox_report_path)
        logger.info(f"Отчёт загружен в Dropbox: {dropbox_report_path}")
    except Exception as e:
        logger.error(f"Ошибка при загрузке отчёта {fname}: {e}")


def move_to_processed(fname: str):
    """Переносит файл в PROCESSED в Dropbox"""
    try:
        move_file(f"{INPUT_PATH}/{fname}", f"{PROCESSED_PATH}/{fname}")
        logger.info(f"Файл {fname} перемещён в {PROCESSED_PATH}")
    except Exception as e:
        logger.error(f"Ошибка при переносе {fname}: {e}")


def read_source_file(file_path: str) -> pd.DataFrame:
    """Загружает CSV/XLSX-файл с сохранением исходных колонок."""
    try:
        if file_path.lower().endswith((".xlsx", ".xls")):
            return pd.read_excel(file_path)
        return pd.read_csv(file_path, sep=None, engine="python", encoding="utf-8")
    except Exception:
        logger.exception(f"Не удалось загрузить данные из файла {file_path}")
        raise


def merge_card_dataframes(card_dfs: List[pd.DataFrame]) -> pd.DataFrame:
    """Объединяет карточные DataFrame в один и гарантирует наличие колонок."""
    base_columns = [
        "Карта",
        "Пул",
        "Направление",
        "Баланс",
        "Метод пополнения",
        "Имя",
        "Фамилия",
        "Bakai customer_id",
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

    # Скачиваем основной файл
    local_file_path = download_to_local(fname)
    if not local_file_path:
        send_message_sync(f"❌ Не удалось скачать {fname}")
        return

    # Получаем анализатор
    analyzer_func, config, requires_card = get_analyzer(fname)
    if not analyzer_func or not config:
        msg = f"❌ Не найден анализатор для файла {fname}"
        logger.warning(msg)
        send_message_sync(msg)
        return

    # Скачиваем карточные файлы, если требуется
    card_files = []
    card_dataframes: List[pd.DataFrame] = []
    conversion_df_original: pd.DataFrame | None = None
    is_conversion = bool(config.get("file_pattern") == "conversion")

    if is_conversion:
        try:
            conversion_df_original = read_source_file(local_file_path)
            logger.info(
                f"[{fname}] Конверсионный файл загружен: {len(conversion_df_original)} строк"
            )
        except Exception as exc:
            logger.error(f"[{fname}] Ошибка при чтении конверсионного файла: {exc}")

    try:
        if requires_card:
            for f in all_files:
                if "card" in f.lower():
                    local_card = download_to_local(f)
                    if local_card:
                        card_files.append(local_card)
                        if is_conversion:
                            try:
                                card_df = read_source_file(local_card)
                                card_dataframes.append(card_df)
                            except Exception as exc:
                                logger.error(
                                    f"[{fname}] Ошибка при чтении карточного файла {local_card}: {exc}"
                                )
            logger.info(f"[{fname}] Найдено файлов для карты: {card_files}")

        if is_conversion and conversion_df_original is not None:
            try:
                from db.database import get_session
                import load_data

                card_df_for_db = merge_card_dataframes(card_dataframes)
                with get_session() as session:
                    load_data.process_conversion(card_df_for_db, conversion_df_original, session)
                logger.info(f"[{fname}] Данные из конверсионного файла сохранены в БД")
            except Exception as exc:
                logger.exception(f"[{fname}] Ошибка при сохранении данных в БД: {exc}")

        # Запуск анализатора
        result = analyzer_func(local_file_path, card_files, config.get("columns", {}))
        report_path = os.path.join(LOCAL_REPORTS, f"report_{fname}.xlsx")

        # Генерация отчёта
        try:
            build_report(result, report_path)
            logger.info(f"[{fname}] Отчёт успешно сгенерирован: {report_path}")
        except Exception as e:
            logger.error(f"[{fname}] Ошибка при генерации отчёта: {e}")
            from openpyxl import Workbook
            wb = Workbook()
            wb.save(report_path)
            logger.info(f"[{fname}] Создан пустой отчёт: {report_path}")

        # Отправка отчёта в Telegram
        send_message_sync(f"✅ Отчёт по файлу {fname} готов")
        send_file_sync(report_path)

        # Отправка листа "Отключить" в Telegram, если есть проблемные карты
        MAX_LEN = 4000  # запас меньше лимита 4096

        problem_cards_df = result.get("problem_cards")
        if problem_cards_df is not None and not problem_cards_df.empty:
            # Заголовок таблицы