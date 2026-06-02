"""Synthetic conversion/card fixture definitions (no production data)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

MSK = ZoneInfo("Europe/Moscow")

# Partner labels used across conv + card + rules snapshot.
RAW_PARTNER_ALPHA = "Test Partner (101)"
RAW_PARTNER_BETA = "Test Beta (202)"

CONV_FILENAME = "conversion_test.xlsx"
CARD_FILENAME = "card_test.xlsx"

COL_MAPPING = {
    "datetime": "Дата/Время создания",
    "card": "Карта",
    "partner": "Партнёр",
    "status": "Статус",
}

FIXED_NOW = datetime(2026, 1, 15, 14, 30, 0, tzinfo=MSK)


def build_conversion_df() -> pd.DataFrame:
    """
    CARD001: 3 consecutive errors then paid -> streak 3 (threshold breach at 3).
    CARD002: paid then error -> streak 0.
    CARD003: second partner on alpha for multi-partner coverage.
    """
    return pd.DataFrame(
        [
            {
                "Дата/Время создания": "10.01.2026 12:00:00",
                "Карта": "CARD001",
                "Партнёр": RAW_PARTNER_ALPHA,
                "Статус": "Ошибка",
            },
            {
                "Дата/Время создания": "10.01.2026 11:00:00",
                "Карта": "CARD001",
                "Партнёр": RAW_PARTNER_ALPHA,
                "Статус": "Ошибка",
            },
            {
                "Дата/Время создания": "10.01.2026 10:00:00",
                "Карта": "CARD001",
                "Партнёр": RAW_PARTNER_ALPHA,
                "Статус": "Ошибка",
            },
            {
                "Дата/Время создания": "09.01.2026 15:00:00",
                "Карта": "CARD001",
                "Партнёр": RAW_PARTNER_ALPHA,
                "Статус": "Оплачен",
            },
            {
                "Дата/Время создания": "10.01.2026 12:00:00",
                "Карта": "CARD002",
                "Партнёр": RAW_PARTNER_BETA,
                "Статус": "Оплачен",
            },
            {
                "Дата/Время создания": "10.01.2026 11:00:00",
                "Карта": "CARD002",
                "Партнёр": RAW_PARTNER_BETA,
                "Статус": "Ошибка",
            },
            {
                "Дата/Время создания": "08.01.2026 09:00:00",
                "Карта": "CARD003",
                "Партнёр": "Амобайл Shop (303)",
                "Статус": "Ошибка",
            },
        ]
    )


def build_card_df() -> pd.DataFrame:
    """CARD001 must breach threshold; CARD002 stays in «Карт в работе» only."""
    return pd.DataFrame(
        [
            {
                "Карта": "CARD001",
                "Партнёр": RAW_PARTNER_ALPHA,
                "Статус": "Готов к работе",
                "Пул": "PoolAlpha",
            },
            {
                "Карта": "CARD002",
                "Партнёр": RAW_PARTNER_BETA,
                "Статус": "Активный вход",
                "Пул": "PoolBeta",
            },
        ]
    )


def build_special_cards_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Карта": "CARD003",
                "Партнер": "Амобайл Shop (303)",
                "Дата": "08.01.2026",
            },
        ]
    )


def write_conversion_fixture(path) -> str:
    build_conversion_df().to_excel(path, index=False)
    return str(path)


def write_card_fixture(path) -> str:
    build_card_df().to_excel(path, index=False)
    return str(path)


def write_special_cards_fixture(path) -> str:
    build_special_cards_df().to_excel(path, index=False)
    return str(path)
