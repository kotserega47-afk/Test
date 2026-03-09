from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from core.rules_v2.constants import (
    DEFAULT_TIMEZONE,
    EXCEL_DATE_FORMAT,
    EXCEL_DATETIME_FORMAT,
    EXCEL_TIME_FORMAT,
)

MSK_TZ = ZoneInfo(DEFAULT_TIMEZONE)


def now_msk() -> datetime:
    """Текущее время в московской TZ."""
    return datetime.now(MSK_TZ)


def ensure_aware_msk(dt: datetime) -> datetime:
    """
    Приводит datetime к aware Europe/Moscow.
    Если datetime naive — считаем, что он уже в московском времени.
    Если aware — конвертируем в Europe/Moscow.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK_TZ)
    return dt.astimezone(MSK_TZ)


def parse_msk_datetime(value: object) -> datetime | None:
    """
    Парсит строку формата 'ДД.ММ.ГГГГ чч:мм:сс' в aware datetime Europe/Moscow.
    Возвращает None для пустых значений.
    """
    if value is None:
        return None

    s = str(value).strip()
    if not s or s.lower() in {"nan", "nat", "none"}:
        return None

    dt = datetime.strptime(s, EXCEL_DATETIME_FORMAT)
    return dt.replace(tzinfo=MSK_TZ)


def parse_msk_date(value: object) -> date | None:
    """Парсит строку формата 'ДД.ММ.ГГГГ'."""
    if value is None:
        return None

    s = str(value).strip()
    if not s or s.lower() in {"nan", "nat", "none"}:
        return None

    return datetime.strptime(s, EXCEL_DATE_FORMAT).date()


def parse_time_value(value: object) -> time | None:
    """
    Парсит time из:
    - datetime.time
    - datetime.datetime
    - строки 'HH:MM'
    - строки 'HH:MM:SS'
    """
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


def parse_msk_series(series: pd.Series) -> pd.Series:
    """
    Парсит Series со строками формата 'ДД.ММ.ГГГГ чч:мм:сс'
    в timezone-aware Series Europe/Moscow.
    """
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

    return dt.dt.tz_localize(DEFAULT_TIMEZONE)


def format_msk_datetime(dt: datetime | None) -> str:
    """Форматирует datetime в 'ДД.ММ.ГГГГ чч:мм:сс' по Москве."""
    if dt is None:
        return ""
    return ensure_aware_msk(dt).strftime(EXCEL_DATETIME_FORMAT)


def format_msk_date(dt: datetime | date | None) -> str:
    """Форматирует date/datetime в 'ДД.ММ.ГГГГ'."""
    if dt is None:
        return ""

    if isinstance(dt, datetime):
        dt = ensure_aware_msk(dt).date()

    return dt.strftime(EXCEL_DATE_FORMAT)


def start_of_day_msk(dt: datetime) -> datetime:
    """Начало суток в московской TZ."""
    dt = ensure_aware_msk(dt)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def end_of_day_msk(dt: datetime) -> datetime:
    """Конец суток в московской TZ."""
    return start_of_day_msk(dt) + timedelta(days=1) - timedelta(microseconds=1)


def combine_msk(d: date, t: time) -> datetime:
    """Собирает aware datetime Europe/Moscow из date + time."""
    return datetime.combine(d, t, tzinfo=MSK_TZ)


def is_within_interval_msk(
    target_dt: datetime,
    start_dt: datetime,
    end_dt: datetime,
) -> bool:
    """
    Проверка принадлежности интервалу [start_dt, end_dt].
    Все значения приводятся к Europe/Moscow.
    """
    target_dt = ensure_aware_msk(target_dt)
    start_dt = ensure_aware_msk(start_dt)
    end_dt = ensure_aware_msk(end_dt)

    return start_dt <= target_dt <= end_dt


def minutes_ago_from_now_msk(dt: datetime | None) -> int | None:
    """Сколько минут прошло до текущего момента по Москве."""
    if dt is None:
        return None

    dt = ensure_aware_msk(dt)
    delta = now_msk() - dt
    return int(delta.total_seconds() // 60)