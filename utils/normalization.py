# utils/normalization.py
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Any
import pandas as pd
from zoneinfo import ZoneInfo
from core.datetime_utils import parse_msk_series

MSK = ZoneInfo("Europe/Moscow")
# -----------------------------
# Партнёры
# -----------------------------
def normalize_partner_name(name: str) -> str:
    if not isinstance(name, str):
        return ""

    normalized = name.lower().strip()
    normalized = normalized.replace("ё", "е")
    normalized = normalized.replace("амобайл", "а-мобайл")
    normalized = re.sub(r"\(\d+\)$", "", normalized)   # убираем "(107)" в конце
    normalized = re.sub(r"\s+", " ", normalized)

    return normalized.strip(", ")


# -----------------------------
# Карты
# -----------------------------
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


# -----------------------------
# Даты
# -----------------------------
def parse_datetime(value: Any) -> Optional[datetime]:
    """
    Канонический парсер для проекта:
    - вход: строка dd.mm.yyyy HH:MM[:SS], Excel datetime, Timestamp, datetime
    - выход: tz-aware datetime в MSK
    """

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    # Уже datetime
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=MSK)
        return value.astimezone(MSK)

    # Попытка строгого формата (быстрее)
    try:
        parsed = parse_dt_series_msk(
            value)
    except Exception:
        # fallback — без жёсткого формата
        parsed = parse_dt_series_msk(
            value)

    if pd.isna(parsed):
        return None

    dt = parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed

    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)

    return dt.astimezone(MSK)

def parse_dt_series_msk(series: pd.Series) -> pd.Series:
    """
    Backward-compatible wrapper.
    Единый источник истины по datetime parsing находится в core.datetime_utils.parse_msk_series.
    """
    return parse_msk_series(series)