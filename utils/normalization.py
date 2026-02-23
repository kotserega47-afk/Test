# utils/normalization.py
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
import pandas as pd


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
def parse_datetime(value) -> Optional[datetime]:
    if value is None or pd.isna(value):
        return None

    parsed = pd.to_datetime(
        value,
        format="%d.%m.%Y %H:%M:%S",
        errors="coerce"
    )

    if pd.isna(parsed):
        return None

    return parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed