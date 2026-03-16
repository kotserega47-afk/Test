from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import logging
import pandas as pd

MSK_TZ_NAME = "Europe/Moscow"
MSK_TZ = ZoneInfo(MSK_TZ_NAME)

logger = logging.getLogger(__name__)

EXCEL_DATETIME_FORMAT = "%d.%m.%Y %H:%M:%S"
EXCEL_DATE_FORMAT = "%d.%m.%Y"
EXCEL_TIME_FORMAT = "%H:%M:%S"


def now_msk() -> datetime:
    return datetime.now(MSK_TZ)


def ensure_aware_msk(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK_TZ)
    return dt.astimezone(MSK_TZ)


def parse_msk_datetime(value: object) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return ensure_aware_msk(value)

    s = str(value).strip()
    if not s or s.lower() in {"nan", "nat", "none"}:
        return None

    try:
        dt = datetime.strptime(s, EXCEL_DATETIME_FORMAT)
    except ValueError as exc:
        raise ValueError(
            f"Cannot parse Moscow datetime from {value!r}. "
            f"Expected format: {EXCEL_DATETIME_FORMAT}"
        ) from exc

    return dt.replace(tzinfo=MSK_TZ)


def parse_msk_series(series: pd.Series) -> pd.Series:
    """
    Канонический путь парсинга:
    - единственный формат: ДД.ММ.ГГГГ ЧЧ:ММ:СС
    - timezone: Europe/Moscow
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

        dt = pd.to_datetime(
            cleaned,
            format=EXCEL_DATETIME_FORMAT,
            errors="coerce",
        )

        invalid_mask = cleaned.notna() & dt.isna()
        if invalid_mask.any():
            bad_examples = cleaned[invalid_mask].dropna().astype(str).unique()[:10]
            logger.warning(
                "Некорректный формат datetime. Ожидается %s. Невалидных значений: %s. Примеры: %s",
                EXCEL_DATETIME_FORMAT,
                int(invalid_mask.sum()),
                ", ".join(bad_examples),
            )

    if dt.dt.tz is None:
        return dt.dt.tz_localize(MSK_TZ_NAME, nonexistent="shift_forward", ambiguous="NaT")

    return dt.dt.tz_convert(MSK_TZ_NAME)


def parse_time_value(value: object) -> time | None:
    if value is None:
        return None

    if isinstance(value, time):
        return value

    if isinstance(value, datetime):
        return value.time()

    s = str(value).strip()
    if not s or s.lower() in {"nan", "nat", "none"}:
        return None

    for fmt in ("%H:%M", EXCEL_TIME_FORMAT):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue

    raise ValueError(f"Cannot parse time value: {value!r}")


def start_of_day_msk(dt: datetime) -> datetime:
    dt = ensure_aware_msk(dt)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def end_of_day_msk(dt: datetime) -> datetime:
    return start_of_day_msk(dt) + timedelta(days=1) - timedelta(microseconds=1)