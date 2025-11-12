# load_data.py

import os
import re
import pandas as pd
import yaml
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from utils.logger import logger

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "config",
    "conversion_config.yaml"
)

# === Загрузка YAML-конфига ===
def load_conversion_config(path: str = CONFIG_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

# === Универсальные функции нормализации ===
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


# === Обработка DataFrame без базы ===
def process_cards(card_df: pd.DataFrame) -> pd.DataFrame:
    """
    Возвращает очищенный DataFrame карт с нормализованными полями.
    """
    cleaned = []

    for _, row in card_df.iterrows():
        card_num = normalize_card_number(row.get("Карта"))
        if not card_num:
            continue

        cleaned.append({
            "Карта": card_num,
            "Пул": str(row.get("Пул") or "").strip(),
            "Направление": str(row.get("Направление") or "").strip(),
            "Баланс": float(row.get("Баланс")) if row.get("Баланс") not in [None, ""] else None,
            "Метод пополнения": str(row.get("Метод пополнения") or "").strip(),
            "Имя": str(row.get("Имя") or "").strip(),
            "Фамилия": str(row.get("Фамилия") or "").strip(),
            "Bakai customer_id": str(row.get("Bakai customer_id") or "").strip(),
        })

    result = pd.DataFrame(cleaned)
    logger.info(f"[process_cards] обработано {len(result)} карт")
    return result


def process_conversion(card_df: pd.DataFrame, conversion_df: pd.DataFrame) -> pd.DataFrame:
    """
    Возвращает DataFrame по конверсиям, объединённый и очищенный без БД.
    """
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

    merged = []

    for _, row in conversion_df.iterrows():
        created_at = parse_datetime(row.get("Дата/Время создания"))
        if created_at is None:
            continue

        partner_norm = normalize_partner_name(row.get("Партнер"))
        exclude_periods = partner_exclusions.get(partner_norm, [])
        if any(start <= created_at <= end for start, end in exclude_periods):
            continue

        card_num = normalize_card_number(row.get("Карта"))
        if not card_num:
            continue

        merged.append({
            "Карта": card_num,
            "Партнер": partner_norm,
            "Статус": normalize_status(row.get("Статус")),
            "ID операции": str(row.get("ID операции") or "").strip(),
            "Дата/Время создания": created_at,
            "Сумма": row.get("Сумма"),
        })

    result = pd.DataFrame(merged)
    logger.info(f"[process_conversion] обработано {len(result)} операций")
    return result
