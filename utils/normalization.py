# utils/normalization.py
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Any
import pandas as pd
from zoneinfo import ZoneInfo

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
        parsed = pd.to_datetime(
            value,
            format="%d.%m.%Y %H:%M:%S",
            errors="raise"
        )
    except Exception:
        # fallback — без жёсткого формата
        parsed = pd.to_datetime(
            value,
            dayfirst=True,
            errors="coerce"
        )

    if pd.isna(parsed):
        return None

    dt = parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed

    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)

    return dt.astimezone(MSK)

def parse_dt_series_msk(series: pd.Series) -> pd.Series:
    """
    Каноническое приведение столбца к tz-aware MSK.

    Базовый контракт:
    - основной входной формат: "ДД.ММ.ГГГГ чч:мм:сс"
    - timezone: Europe/Moscow

    Дополнительно поддерживает:
    - строки без секунд: "ДД.ММ.ГГГГ чч:мм"
    - Excel datetime / pandas Timestamp
    - уже tz-aware значения
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        dt = series
    else:
        cleaned = (
            series.astype("string")
            .fillna("")
            .str.strip()
            .replace({"": pd.NA, "nan": pd.NA, "NaT": pd.NA, "None": pd.NA})
        )

        # 1. основной строгий формат
        dt = pd.to_datetime(
            cleaned,
            format="%d.%m.%Y %H:%M:%S",
            errors="coerce",
        )

        # 2. fallback для строк без секунд
        missing_mask = cleaned.notna() & dt.isna()
        if missing_mask.any():
            dt_fallback = pd.to_datetime(
                cleaned[missing_mask],
                format="%d.%m.%Y %H:%M",
                errors="coerce",
            )
            dt.loc[missing_mask] = dt_fallback

        # 3. fallback для уже datetime-like значений Excel/Timestamp
        missing_mask = cleaned.notna() & dt.isna()
        if missing_mask.any():
            dt_fallback = pd.to_datetime(
                cleaned[missing_mask],
                dayfirst=True,
                errors="coerce",
            )
            dt.loc[missing_mask] = dt_fallback

    if dt.dt.tz is None:
        return dt.dt.tz_localize(MSK, nonexistent="shift_forward", ambiguous="NaT")

    return dt.dt.tz_convert(MSK)