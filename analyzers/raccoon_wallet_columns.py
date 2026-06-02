"""Raccoon Wallet PayIn Excel column mapping (code constants)."""

from __future__ import annotations

RACCOON_PAYIN_COLUMN_MAP: dict[str, str] = {
    "dt": "Дата/Время создания",
    "partner": "Партнер",
    "status": "Статус",
    "info": "Инфо",
    "amount": "Сумма",
}

RACCOON_PAYIN_REQUIRED_COLUMNS: tuple[str, ...] = ("dt", "partner", "status", "amount")
RACCOON_PAYIN_OPTIONAL_COLUMNS: tuple[str, ...] = ("info",)


def get_raccoon_payin_column_map() -> dict[str, str]:
    """Return a copy of the PayIn internal_key → Excel header mapping."""

    return dict(RACCOON_PAYIN_COLUMN_MAP)
