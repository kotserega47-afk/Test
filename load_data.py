# load_data.py
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yaml

from db.database import SessionLocal
from db.models import Card, CardEvent, ErrorType


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "conversion_config.yaml")


def load_conversion_config(path: str = CONFIG_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def normalize_partner_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    normalized = name.lower().strip()
    normalized = normalized.replace("ё", "е")
    normalized = normalized.replace("амобайл", "а-мобайл")
    normalized = re.sub(r"\(\d+\)$", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip(", ")


def build_partner_exclusions(config: dict) -> Dict[str, List[Tuple[datetime, datetime]]]:
    exclusions: Dict[str, List[Tuple[datetime, datetime]]] = {}
    partners = config.get("partners", {})

    for raw_name, settings in partners.items():
        partner_key = normalize_partner_name(raw_name)
        periods: List[Tuple[datetime, datetime]] = []

        for period in settings.get("exclude", []):
            start_raw = pd.to_datetime(period.get("start"), format="%d.%m.%Y %H:%M:%S", errors="coerce")
            end_raw = pd.to_datetime(period.get("end"), format="%d.%m.%Y %H:%M:%S", errors="coerce")

            if pd.notna(start_raw) and pd.notna(end_raw):
                start_dt = start_raw.to_pydatetime() if hasattr(start_raw, "to_pydatetime") else start_raw
                end_dt = end_raw.to_pydatetime() if hasattr(end_raw, "to_pydatetime") else end_raw
                periods.append((start_dt, end_dt))

        exclusions[partner_key] = periods

    return exclusions


def parse_datetime(value: object) -> Optional[datetime]:
    if value is None or pd.isna(value):
        return None

    parsed = pd.to_datetime(value, format="%d.%m.%Y %H:%M:%S", errors="coerce")
    if pd.isna(parsed):
        return None

    return parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed

def process_conversion(card_df: pd.DataFrame, conversion_df: pd.DataFrame, session):
    """
    Обрабатывает данные при конверсии:
    1. Добавляет или обновляет карты
    2. Добавляет новые события (CardEvent), учитывая периоды исключений и дубликаты
    3. Пересчитывает агрегаты карт
    """

    # -------------------------------
    # Подготовка
    # -------------------------------
    config = load_conversion_config()
    partner_exclusions = build_partner_exclusions(config)

    STATUS_MAP = {
        "ошибка": "error",
        "оплачен": "success",
        "готов к работе": "ready",
        "активный вход": "active_in",
    }

    def normalize_status(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return STATUS_MAP.get(value.strip().lower(), value.strip().lower())

    added, skipped_dupes, skipped_excluded = 0, 0, 0

    # -------------------------------
    # 1️⃣ Карты
    # -------------------------------
    for _, row in card_df.iterrows():
        card = session.query(Card).filter_by(card_number=row["Карта"]).first()
        if not card:
            card = Card(
                card_number=row["Карта"],
                pool_id=row.get("Пул"),
                direction=row.get("Направление"),
                balance=row.get("Баланс"),
                replenishment_method=row.get("Метод пополнения"),
                first_name=row.get("Имя"),
                last_name=row.get("Фамилия"),
                bakai_customer_id=row.get("Bakai customer_id"),
            )
            session.add(card)
        else:
            card.pool_id = row.get("Пул") or card.pool_id
            card.direction = row.get("Направление") or card.direction
            if row.get("Баланс") is not None:
                card.balance = row.get("Баланс")
            card.replenishment_method = row.get("Метод пополнения") or card.replenishment_method
            card.first_name = row.get("Имя") or card.first_name
            card.last_name = row.get("Фамилия") or card.last_name
            card.bakai_customer_id = row.get("Bakai customer_id") or card.bakai_customer_id

    session.commit()

    # -------------------------------
    # 2️⃣ События
    # -------------------------------
    for _, row in conversion_df.iterrows():
        created_at = parse_datetime(row.get("Дата/Время создания"))
        if created_at is None:
            continue

        partner_norm = normalize_partner_name(row.get("Партнер"))
        exclude_periods = partner_exclusions.get(partner_norm, [])
        if any(start <= created_at <= end for start, end in exclude_periods):
            skipped_excluded += 1
            continue

        status = normalize_status(row.get("Статус"))

        # получаем карту
        card = session.query(Card).filter_by(card_number=row["Карта"]).first()
        if not card:
            card = Card(card_number=row["Карта"])
            session.add(card)
            session.flush()

        # проверка дубля
        if session.query(CardEvent).filter_by(operation_id=row["ID операции"]).first():
            skipped_dupes += 1
            continue

        # ошибка
        error_id = None
        if status == "error" and row.get("Инфо"):
            error = session.query(ErrorType).filter_by(code=row["Инфо"]).first()
            if not error:
                error = ErrorType(code=row["Инфо"], description=row["Инфо"])
                session.add(error)
                session.flush()
            error_id = error.id

        # создаём событие
        event = CardEvent(
            card_id=card.id,
            status=status,  # уже нормализован
            amount=row.get("Сумма"),
            operation_id=row["ID операции"],
            created_at=created_at,
            error_id=error_id,
            source_file=None,
            snapshot_data=row.to_dict(),
        )
        session.add(event)
        added += 1

    session.commit()

    # -------------------------------
    # 3️⃣ Агрегаты
    # -------------------------------
    for card in session.query(Card).all():
        success_events = session.query(CardEvent).filter_by(card_id=card.id, status="success").all()
        if success_events:
            card.first_success_at = min(e.created_at for e in success_events)
            card.last_success_at = max(e.created_at for e in success_events)
            card.total_success_amount = sum(e.amount or 0 for e in success_events)
        else:
            card.first_success_at = card.last_success_at = None
            card.total_success_amount = 0

        error_events = session.query(CardEvent).filter_by(card_id=card.id, status="error").all()
        if error_events:
            card.first_error_at = min(e.created_at for e in error_events)
            card.last_error_at = max(e.created_at for e in error_events)
        else:
            card.first_error_at = card.last_error_at = None

    session.commit()

    # -------------------------------
    # Логирование итогов
    # -------------------------------
    from utils.logger import logger
    logger.info(
        f"События сохранены: добавлено {added}, "
        f"дубликатов пропущено {skipped_dupes}, "
        f"исключено по датам {skipped_excluded}"
    )
