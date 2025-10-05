# load_data.py
"""
Модуль загрузки и сохранения данных в базу.
Обеспечивает безопасную обработку NaN, конвертацию типов и запись событий.
"""

import math
import pandas as pd
from datetime import datetime
from db.database import get_session
from db.models import Card, CardEvent
from utils.logger import logger


# -----------------------------
# Утилиты очистки и нормализации
# -----------------------------
def is_blank(value) -> bool:
    """True, если значение пустое, NaN или строка из пробелов"""
    return (
        value is None
        or (isinstance(value, float) and math.isnan(value))
        or (isinstance(value, str) and value.strip() == "")
    )


def to_float(value):
    """Безопасное приведение строки к float"""
    if is_blank(value):
        return None
    try:
        # убираем неразрывные пробелы, запятые и пробелы в числе
        text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
        return float(text)
    except Exception:
        return None


def safe_str(value):
    """Возвращает нормализованную строку или None"""
    if is_blank(value):
        return None
    return str(value).strip()


def to_datetime(value):
    """Безопасное приведение значения к datetime"""
    if is_blank(value):
        return None
    try:
        return pd.to_datetime(value, errors="coerce")
    except Exception:
        return None


def normalize_card_number(value):
    """Очистка номера карты: удаляем пробелы и нецифровые символы"""
    if is_blank(value):
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits or None


def normalize_partner_name(value):
    """Нормализация имени партнёра: убираем пробелы, приводим к единому регистру"""
    if is_blank(value):
        return None
    name = str(value).strip().replace("ё", "е").replace("Ё", "Е")
    return " ".join(name.split()).title()


# -----------------------------
# Основная функция обработки
# -----------------------------
def process_conversion(df: pd.DataFrame, source_file: str) -> int:
    """
    Обработка таблицы конверсий:
    - фильтрация пустых строк
    - нормализация типов и полей
    - запись в БД
    :param df: входной DataFrame
    :param source_file: имя исходного файла
    :return: количество добавленных событий
    """
    if df.empty:
        logger.warning(f"Файл {source_file} пуст, пропуск.")
        return 0

    logger.info(f"Начата обработка {source_file}, строк: {len(df)}")

    with get_session() as session:
        added = 0

        for _, row in df.iterrows():
            # --- очистка и нормализация полей ---
            card_num = normalize_card_number(row.get("Карта"))
            if is_blank(card_num):
                continue

            status = safe_str(row.get("Статус"))
            partner = normalize_partner_name(row.get("Партнер"))
            amount = to_float(row.get("Сумма") or row.get("Amount"))
            operation_id = safe_str(row.get("operation_id") or row.get("Operation ID"))
            created_at = to_datetime(row.get("Дата/Время создания") or row.get("Date/Time Created"))

            if is_blank(status) or created_at is None:
                continue

            # --- поиск или создание карты ---
            card = session.query(Card).filter_by(card_number=card_num).first()
            if not card:
                card = Card(card_number=card_num, partner=partner)
                session.add(card)
                session.flush()

            # --- добавление события ---
            event = CardEvent(
                card_id=card.id,
                status=status,
                amount=amount,
                operation_id=operation_id,
                created_at=created_at,
                source_file=source_file,
                snapshot_data=row.to_dict(),
            )
            session.add(event)
            added += 1

        logger.info(f"Добавлено {added} событий из {len(df)} строк ({source_file})")

    return added


# -----------------------------
# Пример вызова
# -----------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Использование: python load_data.py path/to/file.xlsx")
        sys.exit(0)

    file_path = sys.argv[1]
    df = pd.read_excel(file_path)
    process_conversion(df, source_file=file_path)
