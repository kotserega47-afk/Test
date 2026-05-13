"""Hourly reporter: layout rendering (leading blank before payouts/payins sections)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from core.rules_v2.models import MetaInfo, ReportItem, RulesSnapshotV2
from reporters.hourly_reporter import _render_by_snapshot_layout


def _meta() -> MetaInfo:
    return MetaInfo(
        ruleset_version="test",
        updated_at=datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc),
        updated_by="test",
    )


def _layout_hourly_full() -> list[ReportItem]:
    """header.period → payouts.title → payouts.items → hr → payins.title → payins.items"""
    return [
        ReportItem(
            item_key="hourly.layout.1",
            report_key="hourly",
            section_key="hourly.ui",
            item_type="layout_line",
            source_key="header.period",
            method_key=None,
            display_name="",
            sort_order=1,
            enabled=True,
            comment="text",
        ),
        ReportItem(
            item_key="hourly.layout.2",
            report_key="hourly",
            section_key="hourly.ui",
            item_type="layout_line",
            source_key="payouts.title",
            method_key=None,
            display_name="",
            sort_order=2,
            enabled=True,
            comment="text",
        ),
        ReportItem(
            item_key="hourly.layout.3",
            report_key="hourly",
            section_key="hourly.ui",
            item_type="layout_line",
            source_key="payouts.items",
            method_key=None,
            display_name="",
            sort_order=3,
            enabled=True,
            comment="text",
        ),
        ReportItem(
            item_key="hourly.layout.4",
            report_key="hourly",
            section_key="hourly.ui",
            item_type="layout_line",
            source_key="separator.line",
            method_key=None,
            display_name="",
            sort_order=4,
            enabled=True,
            comment="hr",
        ),
        ReportItem(
            item_key="hourly.layout.5",
            report_key="hourly",
            section_key="hourly.ui",
            item_type="layout_line",
            source_key="payins.title",
            method_key=None,
            display_name="",
            sort_order=5,
            enabled=True,
            comment="text",
        ),
        ReportItem(
            item_key="hourly.layout.6",
            report_key="hourly",
            section_key="hourly.ui",
            item_type="layout_line",
            source_key="payins.items",
            method_key=None,
            display_name="",
            sort_order=6,
            enabled=True,
            comment="text",
        ),
    ]


def _snap_hourly(items: list[ReportItem]) -> RulesSnapshotV2:
    return RulesSnapshotV2(meta=_meta(), report_items=items)


@pytest.fixture
def render_model_sample() -> dict:
    return {
        "header.period": "Данные на 13.05 с 00:00 по 22:00",
        "payouts.title": "Выплаты:",
        "payouts.items": ["1) G: ", " - UNI – 10"],
        "separator.line": "",
        "payins.title": "Поступления:",
        "payins.items": ["1) A – 5"],
    }


def test_hourly_one_blank_before_payouts_and_payins_sections(render_model_sample: dict) -> None:
    snap = _snap_hourly(_layout_hourly_full())
    with patch("reporters.hourly_reporter.get_snapshot_v2", return_value=snap):
        text = _render_by_snapshot_layout(view="hourly", render_model=render_model_sample)
    lines = text.split("\n")
    period_idx = lines.index("Данные на 13.05 с 00:00 по 22:00")
    payout_idx = lines.index("Выплаты:")
    payin_idx = lines.index("Поступления:")
    hr_idx = lines.index("_______________________")

    assert payout_idx == period_idx + 2, "expected one blank line between period and Выплаты:"
    assert lines[period_idx + 1] == ""

    assert payin_idx == hr_idx + 2, "expected one blank line between hr and Поступления:"
    assert lines[hr_idx + 1] == ""


def test_hourly_no_triple_newlines(render_model_sample: dict) -> None:
    snap = _snap_hourly(_layout_hourly_full())
    with patch("reporters.hourly_reporter.get_snapshot_v2", return_value=snap):
        text = _render_by_snapshot_layout(view="hourly", render_model=render_model_sample)
    lines = text.split("\n")
    for i in range(len(lines) - 1):
        assert not (lines[i] == "" and lines[i + 1] == "" and i + 2 < len(lines) and lines[i + 2] == "")


def test_hourly_payouts_title_then_items_single_leading_blank(render_model_sample: dict) -> None:
    """Both payouts.title and payouts.items: blank only before first emitting line of section."""
    snap = _snap_hourly(_layout_hourly_full())
    with patch("reporters.hourly_reporter.get_snapshot_v2", return_value=snap):
        text = _render_by_snapshot_layout(view="hourly", render_model=render_model_sample)
    lines = text.split("\n")
    period_i = lines.index("Данные на 13.05 с 00:00 по 22:00")
    payout_i = lines.index("Выплаты:")
    assert lines[period_i + 1] == ""
    assert lines[period_i + 2] == "Выплаты:"
    assert lines[period_i + 3].rstrip() == "1) G:"
    assert lines[period_i + 4].strip() == "- UNI – 10"


def test_hourly_empty_payout_section_emits_no_leading_blank_for_payouts() -> None:
    """No output from payouts → no leading blank for payouts (payins still normal)."""
    items = [
        ReportItem(
            item_key="hourly.layout.1",
            report_key="hourly",
            section_key="hourly.ui",
            item_type="layout_line",
            source_key="header.period",
            method_key=None,
            display_name="",
            sort_order=1,
            enabled=True,
            comment="text",
        ),
        ReportItem(
            item_key="hourly.layout.2",
            report_key="hourly",
            section_key="hourly.ui",
            item_type="layout_line",
            source_key="payouts.title",
            method_key=None,
            display_name="",
            sort_order=2,
            enabled=True,
            comment="text",
        ),
        ReportItem(
            item_key="hourly.layout.3",
            report_key="hourly",
            section_key="hourly.ui",
            item_type="layout_line",
            source_key="payouts.items",
            method_key=None,
            display_name="",
            sort_order=3,
            enabled=True,
            comment="text",
        ),
        ReportItem(
            item_key="hourly.layout.4",
            report_key="hourly",
            section_key="hourly.ui",
            item_type="layout_line",
            source_key="payins.title",
            method_key=None,
            display_name="",
            sort_order=4,
            enabled=True,
            comment="text",
        ),
    ]
    model = {
        "header.period": "Period line",
        "payouts.title": "",
        "payouts.items": [],
        "payins.title": "Поступления:",
        "payins.items": ["x"],
    }
    snap = _snap_hourly(items)
    with patch("reporters.hourly_reporter.get_snapshot_v2", return_value=snap):
        text = _render_by_snapshot_layout(view="hourly", render_model=model)
    lines = text.split("\n")
    assert "Поступления:" in lines
    period_i = lines.index("Period line")
    payin_i = lines.index("Поступления:")
    assert payin_i == period_i + 2
    assert lines[period_i + 1] == ""


def test_wallet_view_no_section_leading_blanks() -> None:
    """Non-hourly view must not insert hourly section blanks."""
    items = [
        ReportItem(
            item_key="w.layout.1",
            report_key="wallet",
            section_key="wallet.ui",
            item_type="layout_line",
            source_key="header.period",
            method_key=None,
            display_name="",
            sort_order=1,
            enabled=True,
            comment="text",
        ),
        ReportItem(
            item_key="w.layout.2",
            report_key="wallet",
            section_key="wallet.ui",
            item_type="layout_line",
            source_key="payouts.title",
            method_key=None,
            display_name="",
            sort_order=2,
            enabled=True,
            comment="text",
        ),
    ]
    model = {"header.period": "H", "payouts.title": "T"}
    snap_wallet = RulesSnapshotV2(meta=_meta(), report_items=items)
    with patch("reporters.hourly_reporter.get_snapshot_v2", return_value=snap_wallet):
        text = _render_by_snapshot_layout(view="wallet", render_model=model)
    lines = text.split("\n")
    assert lines == ["H", "T"]
