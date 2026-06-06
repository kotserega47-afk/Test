"""Helpers for Antares b-form-datepicker date selection in downloader."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator

_MONTH_PREFIXES: tuple[tuple[str, int], ...] = (
    ("январ", 1),
    ("феврал", 2),
    ("март", 3),
    ("апрел", 4),
    ("май", 5),
    ("мая", 5),
    ("июн", 6),
    ("июл", 7),
    ("август", 8),
    ("сентябр", 9),
    ("октябр", 10),
    ("ноябр", 11),
    ("декабр", 12),
    ("january", 1),
    ("february", 2),
    ("march", 3),
    ("april", 4),
    ("may", 5),
    ("june", 6),
    ("july", 7),
    ("august", 8),
    ("september", 9),
    ("october", 10),
    ("november", 11),
    ("december", 12),
)


def payout_left_date(*, now: datetime, days_back: int = 7) -> date:
    """Left payout filter date = today (MSK) minus ``days_back``."""
    return (now - timedelta(days=days_back)).date()


def month_navigation_steps(
    visible_year: int,
    visible_month: int,
    target_year: int,
    target_month: int,
) -> int:
    """
    Month delta from visible calendar to target.

    Negative → go to previous month(s), positive → next, zero → already there.
    """
    return (target_year - visible_year) * 12 + (target_month - visible_month)


def parse_visible_month_year(header_text: str) -> tuple[int, int] | None:
    """Parse datepicker header like ``May 2026`` or ``май 2026``."""
    text = (header_text or "").strip().lower()
    if not text:
        return None

    year_match = re.search(r"(20\d{2})", text)
    if not year_match:
        return None
    year = int(year_match.group(1))

    for prefix, month in _MONTH_PREFIXES:
        if prefix in text:
            return year, month

    numeric = re.search(r"(\d{1,2})[\s./-]+(20\d{2})", text)
    if numeric:
        return int(numeric.group(2)), int(numeric.group(1))

    return None


def _read_visible_month_year(calendar_popup: Locator) -> tuple[int, int] | None:
    for selector in (
        "header .col",
        ".b-calendar-header .col",
        "[role='heading']",
        ".b-datepicker-header",
    ):
        loc = calendar_popup.locator(selector)
        if loc.count() == 0:
            continue
        parsed = parse_visible_month_year(loc.first.inner_text())
        if parsed:
            return parsed
    return None


def _click_month_nav(calendar_popup: Locator, *, direction: str) -> bool:
    if direction == "prev":
        labels = ("Previous month", "Предыдущий месяц")
    else:
        labels = ("Next month", "Следующий месяц")

    for label in labels:
        btn = calendar_popup.locator(f"button[aria-label='{label}']")
        if btn.count() > 0:
            btn.first.click()
            calendar_popup.page.wait_for_timeout(200)
            return True
    return False


def _click_target_day(calendar_popup: Locator, target: date) -> bool:
    target_iso = target.strftime("%Y-%m-%d")
    by_date = calendar_popup.locator(f"[data-date='{target_iso}']")
    if by_date.count() > 0:
        by_date.first.click()
        return True

    day_text = str(target.day)
    cells = calendar_popup.locator("span.btn:not(.text-muted)")
    for index in range(cells.count()):
        cell = cells.nth(index)
        if cell.inner_text().strip() == day_text:
            cell.click()
            return True
    return False


def pick_date_in_bootstrap_datepicker(
    calendar_popup: Locator,
    target: date,
    *,
    log_info,
    log_warning,
    max_month_steps: int = 24,
) -> None:
    """
    Navigate b-form-datepicker popup to ``target`` month/year and select the day.

    Uses ``data-date`` when available; falls back to non-muted day buttons.
    """
    target_iso = target.isoformat()

    for attempt in range(max_month_steps + 1):
        visible = _read_visible_month_year(calendar_popup)
        if visible:
            year, month = visible
            log_info(
                f"📅 Datepicker visible month/year: {month:02d}.{year} "
                f"(target={target.strftime('%d.%m.%Y')})"
            )
        else:
            header_bits: list[str] = []
            for selector in ("header .col", ".b-calendar-header .col", "[role='heading']"):
                loc = calendar_popup.locator(selector)
                if loc.count() > 0:
                    header_bits.append(loc.first.inner_text().strip())
            if header_bits:
                log_warning(f"📅 Datepicker header unreadable: {header_bits[0]!r}")
            else:
                log_warning("📅 Datepicker header month/year not found")

        if _click_target_day(calendar_popup, target):
            log_info(
                f"✅ В левом календаре выбрана дата: {target.strftime('%d.%m.%Y')} "
                f"(attempt={attempt + 1})"
            )
            return

        if attempt >= max_month_steps:
            break

        if visible:
            steps = month_navigation_steps(visible[0], visible[1], target.year, target.month)
            if steps == 0:
                break
            direction = "prev" if steps < 0 else "next"
            if not _click_month_nav(calendar_popup, direction=direction):
                log_warning(f"📅 Кнопка {direction} month не найдена в datepicker")
                break
            continue

        if not _click_month_nav(calendar_popup, direction="prev"):
            log_warning("📅 Кнопка Previous month не найдена в datepicker")
            break

    raise RuntimeError(
        f"Timeout waiting for day {target.day} in datepicker (target={target_iso})"
    )
