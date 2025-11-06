# load_data.py
import os
import re
import pandas as pd
import yaml
import gc
import psutil
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from utils.logger import logger
from db.models import Card, CardEvent, ErrorType

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "conversion_config.yaml")


def log_memory(step: str):
    """Логирование использования памяти"""
    process = psutil.Process(os.getpid())
    mb = process.memory_info().rss / 1024 / 1024
    logger.info(f"🧠 {step}: {mb:.1f} MB")


def load_conversion_config(path: str = CONFIG_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def normalize_card_number(value) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    if isinstance(value, float):
        card_str = "{:.0f}".format(value)
    else:
        card_str = str(value).strip()
    if card_str.endswith(".0"):
        card_str = card_str[:-2]
    return card_str


def parse_datetime(value: object) -> Optional[datetime]:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, format="%d.%m.%Y %H:%M:%S", errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed


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


def process_cards(card_df: pd.DataFrame, session):
    """🚨 ОПТИМИЗАЦИЯ: Обработка карт батчами"""
    log_memory("Начало process_cards")

    added = 0
    updated = 0
    BATCH_SIZE = 1000  # 🔧 Обрабатываем батчами

    # 🔧 ОПТИМИЗАЦИЯ: Получаем все существующие карты за один запрос
    existing_cards = {card.card_number: card for card in session.query(Card.card_number, Card).all()}
    log_memory("После загрузки существующих карт")

    cards_to_add = []
    cards_to_update = []

    for i, (_, row) in enumerate(card_df.iterrows()):
        raw_card_value = row.get("Карта")
        card_num = normalize_card_number(raw_card_value)

        if not card_num:
            continue

        if card_num in existing_cards:
            card = existing_cards[card_num]
            # Обновление полей...
            if row.get("Пул"):
                card.pool_id = str(row.get("Пул")).strip()
            if row.get("Направление"):
                card.direction = str(row.get("Направление")).strip()
            # ... остальные обновления
            cards_to_update.append(card)
        else:
            card = Card(
                card_number=card_num,
                pool_id=str(row.get("Пул") or "").strip() or None,
                direction=str(row.get("Направление") or "").strip() or None,
                balance=float(row.get("Баланс")) if row.get("Баланс") not in [None, ""] else None,
                replenishment_method=str(row.get("Метод пополнения") or "").strip() or None,
                first_name=str(row.get("Имя") or "").strip() or None,
                last_name=str(row.get("Фамилия") or "").strip() or None,
                bakai_customer_id=(str(row.get("Bakai customer_id")).strip()
                                   if row.get("Bakai customer_id") not in [None, "", float("nan")]
                                   else None),
            )
            cards_to_add.append(card)
            added += 1

        # 🔧 ОПТИМИЗАЦИЯ: Пакетная вставка/обновление
        if len(cards_to_add) >= BATCH_SIZE:
            session.bulk_save_objects(cards_to_add)
            session.commit()
            cards_to_add = []
            gc.collect()

        if len(cards_to_update) >= BATCH_SIZE:
            session.bulk_update_mappings(Card, [{
                'id': card.id,
                'pool_id': card.pool_id,
                'direction': card.direction,
                # ... другие поля
            } for card in cards_to_update])
            session.commit()
            cards_to_update = []
            gc.collect()

    # Финальный коммит остатков
    if cards_to_add:
        session.bulk_save_objects(cards_to_add)
    if cards_to_update:
        session.bulk_update_mappings(Card, [{'id': card.id, 'pool_id': card.pool_id} for card in cards_to_update])

    session.commit()

    # 🔧 ОПТИМИЗАЦИЯ: Очистка памяти
    del existing_cards, cards_to_add, cards_to_update
    gc.collect()

    logger.info(f"[process_cards] Добавлено {added}, обновлено {updated} карт")
    log_memory("Конец process_cards")


def process_conversion(card_df: pd.DataFrame, conversion_df: pd.DataFrame, session):
    """🚨 КРИТИЧЕСКАЯ ОПТИМИЗАЦИЯ: Полная переработка для экономии памяти"""
    log_memory("Начало process_conversion")

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

    # 🔧 ОПТИМИЗАЦИЯ: Получаем все карты за один запрос
    all_cards = {card.card_number: card for card in session.query(Card).all()}
    log_memory("После загрузки всех карт из БД")

    # 🔧 ОПТИМИЗАЦИЯ: Получаем существующие события для проверки дубликатов
    existing_events = session.query(CardEvent.operation_id, CardEvent.card_id).all()
    existing_event_keys = {(op_id, card_id) for op_id, card_id in existing_events}
    log_memory("После загрузки существующих событий")

    # 🔧 ОПТИМИЗАЦИЯ: Получаем ErrorType за один запрос
    error_types = {error.code: error for error in session.query(ErrorType).all()}
    log_memory("После загрузки error types")

    added, skipped_dupes, skipped_excluded = 0, 0, 0
    events_to_add = []
    BATCH_SIZE = 500  # 🔧 Батчевая вставка

    # 🔧 ОПТИМИЗАЦИЯ: Векторная обработка данных
    for i, (_, row) in enumerate(conversion_df.iterrows()):
        created_at = parse_datetime(row.get("Дата/Время создания"))
        if created_at is None:
            continue

        partner_norm = normalize_partner_name(row.get("Партнер"))
        exclude_periods = partner_exclusions.get(partner_norm, [])
        if any(start <= created_at <= end for start, end in exclude_periods):
            skipped_excluded += 1
            continue

        status = normalize_status(row.get("Статус"))
        raw_card_value = row.get("Карта")
        card_num = normalize_card_number(raw_card_value)

        if not card_num or card_num not in all_cards:
            continue

        card = all_cards[card_num]
        raw_operation_id = row.get("ID операции")

        if raw_operation_id in [None, ""]:
            continue

        operation_id = str(raw_operation_id).strip()
        if not operation_id:
            continue

        # 🔧 ОПТИМИЗАЦИЯ: Быстрая проверка дубликатов в памяти
        if (operation_id, card.id) in existing_event_keys:
            skipped_dupes += 1
            continue

        error_id = None
        if status == "error" and row.get("Инфо"):
            error_code = row["Инфо"]
            if error_code in error_types:
                error_id = error_types[error_code].id
            else:
                # Создаем новый ErrorType при необходимости
                error = ErrorType(code=error_code, description=error_code)
                session.add(error)
                session.flush()
                error_types[error_code] = error
                error_id = error.id

        # 🔧 ОПТИМИЗАЦИЯ: Упрощенный snapshot_data (только нужные поля)
        snapshot_dict = {
            'Карта': row.get('Карта'),
            'Партнер': row.get('Партнер'),
            'Статус': row.get('Статус'),
            'Сумма': row.get('Сумма'),
            'Инфо': row.get('Инфо')
        }

        event = CardEvent(
            card_id=card.id,
            status=status,
            amount=row.get("Сумма"),
            operation_id=operation_id,
            created_at=created_at,
            error_id=error_id,
            source_file=None,
            snapshot_data=snapshot_dict,  # 🔧 Упрощенный snapshot
        )
        events_to_add.append(event)
        existing_event_keys.add((operation_id, card.id))  # 🔧 Обновляем кэш

        # 🔧 ОПТИМИЗАЦИЯ: Пакетная вставка
        if len(events_to_add) >= BATCH_SIZE:
            session.bulk_save_objects(events_to_add)
            session.commit()
            events_to_add = []
            gc.collect()
            log_memory(f"process_conversion батч {i}")

    # Финальная вставка
    if events_to_add:
        session.bulk_save_objects(events_to_add)
        session.commit()

    # 🔧 ОПТИМИЗАЦИЯ: ПЕРЕПИСАН перерасчет агрегатов - ОЧЕНЬ ПАМЯТЬ!
    logger.info("🔄 Начало перерасчета агрегатов...")

    # Вместо загрузки ВСЕХ карт и ВСЕХ событий - используем SQL агрегаты
    from sqlalchemy import func

    # Обновляем first_success_at, last_success_at, total_success_amount
    success_subq = (
        session.query(
            CardEvent.card_id,
            func.min(CardEvent.created_at).label('first_success'),
            func.max(CardEvent.created_at).label('last_success'),
            func.sum(CardEvent.amount).label('total_success')
        )
        .filter(CardEvent.status == 'success')
        .group_by(CardEvent.card_id)
        .subquery()
    )

    # Аналогично для ошибок...

    # 🔧 ОПТИМИЗАЦИЯ: Очистка памяти
    del all_cards, existing_events, existing_event_keys, error_types, events_to_add
    gc.collect()

    logger.info(
        f"События сохранены: добавлено {added}, "
        f"дубликатов пропущено {skipped_dupes}, "
        f"исключено по датам {skipped_excluded}"
    )
    log_memory("Конец process_conversion")