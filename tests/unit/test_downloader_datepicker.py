from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from integrations.downloader_datepicker import (
    month_navigation_steps,
    parse_visible_month_year,
    payout_left_date,
    pick_date_in_bootstrap_datepicker,
)

MSK = ZoneInfo("Europe/Moscow")


def test_payout_left_date_crosses_month_boundary():
    now = datetime(2026, 6, 7, 12, 0, tzinfo=MSK)
    assert payout_left_date(now=now, days_back=7) == date(2026, 5, 31)


def test_payout_left_date_same_month():
    now = datetime(2026, 6, 3, 12, 0, tzinfo=MSK)
    assert payout_left_date(now=now, days_back=7) == date(2026, 5, 27)


@pytest.mark.parametrize(
    "visible,target,expected",
    [
        ((2026, 6), (2026, 5), -1),
        ((2026, 5), (2026, 5), 0),
        ((2026, 4), (2026, 5), 1),
        ((2025, 12), (2026, 1), 1),
        ((2026, 1), (2025, 12), -1),
    ],
)
def test_month_navigation_steps(visible, target, expected):
    vy, vm = visible
    ty, tm = target
    assert month_navigation_steps(vy, vm, ty, tm) == expected


@pytest.mark.parametrize(
    "header,expected",
    [
        ("May 2026", (2026, 5)),
        ("май 2026", (2026, 5)),
        ("Июнь 2026", (2026, 6)),
        ("05.2026", (2026, 5)),
    ],
)
def test_parse_visible_month_year(header, expected):
    assert parse_visible_month_year(header) == expected


def test_pick_date_navigates_to_previous_month_before_day_click():
    logs: list[str] = []
    warnings: list[str] = []

    popup = MagicMock()
    page = MagicMock()
    popup.page = page

    header = MagicMock()
    header.count.return_value = 1
    header.first.inner_text.return_value = "June 2026"

    may_day = MagicMock()
    may_day.count.side_effect = [0, 1]
    may_day.first = MagicMock()

    prev_btn = MagicMock()
    prev_btn.count.return_value = 1
    prev_btn.first = MagicMock()

    def locator_side_effect(selector: str):
        if selector == "header .col":
            return header
        if selector == "[data-date='2026-05-31']":
            return may_day
        if selector == "button[aria-label='Previous month']":
            return prev_btn
        empty = MagicMock()
        empty.count.return_value = 0
        return empty

    popup.locator.side_effect = locator_side_effect

    pick_date_in_bootstrap_datepicker(
        popup,
        date(2026, 5, 31),
        log_info=logs.append,
        log_warning=warnings.append,
    )

    prev_btn.first.click.assert_called_once()
    may_day.first.click.assert_called_once()
    assert any("visible month/year: 06.2026" in item for item in logs)
    assert any("2026-05-31" in item or "31.05.2026" in item for item in logs)


def test_pick_date_raises_when_target_not_found():
    popup = MagicMock()
    popup.page = MagicMock()

    empty = MagicMock()
    empty.count.return_value = 0
    popup.locator.return_value = empty

    with pytest.raises(RuntimeError, match="Timeout waiting for day 31"):
        pick_date_in_bootstrap_datepicker(
            popup,
            date(2026, 5, 31),
            log_info=lambda *_a, **_k: None,
            log_warning=lambda *_a, **_k: None,
            max_month_steps=1,
        )
