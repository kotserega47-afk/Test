# load_data.py
import os
import re
from datetime import datetime
from typing import Dict, List, Tuple

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

def process_conversion(card_df: pd.DataFrame, conversion_df: pd.DataFrame, session):
    """
    Обрабатывает данные при конверсии:
    - Добавляет новые карты или обновляет существующие
    - Добавляет новые события с snapshot состояния карты
    - Пересчитывает агрегаты карты (жизненный цикл и сумму успешных операций)
    """
    config = load_conversion_config()
    partner_exclusions = build_partner_exclusions(config)

    # 1️⃣ Добавление или обновление карт
    for _, row in card_df.iterrows():
        card = session.query(Card).filter_by(card_number=row['Карта']).first()
        if not card:
            card = Card(
                card_number=row['Карта'],
                pool_id=row.get('Пул'),
                direction=row.get('Направление'),
                balance=row.get('Баланс'),
                replenishment_method=row.get('Метод пополнения'),
                first_name=row.get('Имя'),
                last_name=row.get('Фамилия'),
                bakai_customer_id=row.get('Bakai customer_id')
            )
            session.add(card)
        else:
            # Обновляем карту текущей информацией
            card.pool_id = row.get('Пул') or card.pool_id
            card.direction = row.get('Направление') or card.direction
            card.balance = row.get('Баланс') if row.get('Баланс') is not None else card.balance
            card.replenishment_method = row.get('Метод пополнения') or card.replenishment_method
            card.first_name = row.get('Имя') or card.first_name
            card.last_name = row.get('Фамилия') or card.last_name
            card.bakai_customer_id = row.get('Bakai customer_id') or card.bakai_customer_id

    session.commit()

    # 2️⃣ Добавление новых событий
    for _, row in conversion_df.iterrows():
        partner_norm = normalize_partner_name(row.get('Партнер'))
        created_at_raw = pd.to_datetime(
            row.get('Дата/Время создания'),
            format="%d.%m.%Y %H:%M:%S",
            errors="coerce"
        )

        if pd.isna(created_at_raw):
            continue

        created_at = (
            created_at_raw.to_pydatetime()
            if hasattr(created_at_raw, "to_pydatetime")
            else created_at_raw
        )

        exclude_periods = partner_exclusions.get(partner_norm, [])
        should_skip = any(start <= created_at <= end for start, end in exclude_periods)
        if should_skip:
            continue

        status = str(row.get('Статус', '')).lower()

        card = session.query(Card).filter_by(card_number=row['Карта']).first()
        if not card:
            # На всякий случай создаём карту минимально
            card = Card(card_number=row['Карта'])
            session.add(card)
            session.flush()

        # Проверка дубля по operation_id
        existing_event = session.query(CardEvent).filter_by(operation_id=row['ID операции']).first()
        if existing_event:
            continue

        # Ошибка
        error_id = None
        if status == 'error' and row.get('Инфо'):
            error = session.query(ErrorType).filter_by(code=row['Инфо']).first()
            if not error:
                error = ErrorType(code=row['Инфо'], description=row['Инфо'])
                session.add(error)
                session.flush()
            error_id = error.id

        event_kwargs = {
            "card_id": card.id,
            "status": status,
            "amount": row['Сумма'] if status == 'success' else None,
            "operation_id": row['ID операции'],
            "created_at": created_at,
            "error_id": error_id,
            "source_file": row.get('source_file', None),
        }

        if hasattr(CardEvent, "snapshot_data"):
            event_kwargs["snapshot_data"] = {
                "direction": card.direction,
                "balance": card.balance,
                "replenishment_method": card.replenishment_method,
                "first_name": card.first_name,
                "last_name": card.last_name,
                "bakai_customer_id": card.bakai_customer_id,
                "pool_id": card.pool_id,
            }

        # Создаём событие с snapshot, если он поддерживается моделью
        event = CardEvent(**event_kwargs)
        session.add(event)

    session.commit()

    # 3️⃣ Пересчёт агрегатов карты
    cards_to_update = session.query(Card).all()
    for card in cards_to_update:
        # Успешные операции
        success_events = session.query(CardEvent).filter_by(card_id=card.id, status='success').all()
        if success_events:
            card.first_success_at = min(e.created_at for e in success_events)
            card.last_success_at = max(e.created_at for e in success_events)
            card.total_success_amount = sum(e.amount for e in success_events if e.amount)
        else:
            card.first_success_at = None
            card.last_success_at = None
            card.total_success_amount = 0

        # Ошибки
        error_events = session.query(CardEvent).filter_by(card_id=card.id, status='error').all()
        if error_events:
            card.first_error_at = min(e.created_at for e in error_events)
            card.last_error_at = max(e.created_at for e in error_events)
        else:
            card.first_error_at = None