"""Golden: full hourly text from synthetic DTO + snapshot (presentation contract only)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from analyzers.hourly_analyzer import HourlyDTO, HourlyMethodRow, HourlyPayoutBlock, HourlyRow
from core.rules_v2.models import MetaInfo, ReportItem, RulesSnapshotV2
from reporters.hourly_reporter import render_hourly

MSK = ZoneInfo("Europe/Moscow")

_GOLDEN_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "hourly" / "golden" / "expected_hourly_render.txt"
)


def _fixed_dto() -> HourlyDTO:
    start = datetime(2026, 1, 15, 9, 0, tzinfo=MSK)
    end = datetime(2026, 1, 15, 17, 30, tzinfo=MSK)
    header = datetime(2026, 1, 15, 0, 0, tzinfo=MSK)
    return HourlyDTO(
        start_dt=start,
        end_dt=end,
        header_date=header,
        payout=[
            HourlyPayoutBlock(
                group_code="PG1",
                title="ignored",
                methods=[
                    HourlyMethodRow(method_code="uni", title="Uni Method", amount=12345.0, comment=""),
                    HourlyMethodRow(method_code="card", title="Card Method", amount=0.0, comment="hidden"),
                ],
            )
        ],
        payin=[
            HourlyRow(entity_code="payin_quiet", title="Quiet", amount=0.0, comment="inactive"),
            HourlyRow(entity_code="payin_active", title="Active Payin", amount=5678.0, comment=""),
        ],
    )


def _layout_lines() -> list[ReportItem]:
    return [
        ReportItem(
            item_key="golden.layout.1",
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
            item_key="golden.layout.2",
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
            item_key="golden.layout.3",
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
            item_key="golden.layout.4",
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
            item_key="golden.layout.5",
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
            item_key="golden.layout.6",
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


def _catalog_items() -> list[ReportItem]:
    return [
        ReportItem(
            item_key="golden.payin.quiet",
            report_key="hourly",
            section_key="hourly.config_payins",
            item_type="payin_row",
            source_key="payin_quiet",
            method_key=None,
            display_name="Quiet Row",
            sort_order=10,
            enabled=True,
        ),
        ReportItem(
            item_key="golden.payin.active",
            report_key="hourly",
            section_key="hourly.config_payins",
            item_type="payin_row",
            source_key="payin_active",
            method_key=None,
            display_name="Active Payin",
            sort_order=20,
            enabled=True,
        ),
        ReportItem(
            item_key="golden.payout.group.pg1",
            report_key="hourly",
            section_key="hourly.config_payouts",
            item_type="payout_group",
            source_key="PG1",
            method_key=None,
            display_name="Alpha Group",
            sort_order=30,
            enabled=True,
        ),
        ReportItem(
            item_key="golden.payout.m.pg1.uni",
            report_key="hourly",
            section_key="hourly.config_payout_methods",
            item_type="payout_method",
            source_key="PG1",
            method_key="uni",
            display_name="Uni Method",
            sort_order=40,
            enabled=True,
        ),
        ReportItem(
            item_key="golden.payout.m.pg1.card",
            report_key="hourly",
            section_key="hourly.config_payout_methods",
            item_type="payout_method",
            source_key="PG1",
            method_key="card",
            display_name="Card Method",
            sort_order=50,
            enabled=True,
        ),
    ]


def _snapshot() -> RulesSnapshotV2:
    meta = MetaInfo(
        ruleset_version="golden-hourly-render",
        updated_at=datetime(2026, 1, 15, 12, 0, 0, tzinfo=MSK),
        updated_by="golden-test",
    )
    return RulesSnapshotV2(meta=meta, report_items=_layout_lines() + _catalog_items())


def _norm_crlf(s: str) -> str:
    return s.replace("\r\n", "\n")


@pytest.fixture
def hourly_render_patches():
    snap = _snapshot()
    dto = _fixed_dto()

    def _params(
        *,
        job: str,
        force_sync: bool = False,
        rules_xlsx_path: str = "",
        sheet_name: str = "job_params",
        **kwargs: object,
    ) -> dict:
        if job.strip().lower() == "hourly":
            return {"hide_inactive_rows": True}
        return {}

    with (
        patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap),
        patch("reporters.hourly_reporter.get_snapshot_v2", return_value=snap),
        patch("reporters.hourly_render_model.get_job_params", side_effect=_params),
    ):
        yield dto


def test_hourly_render_golden_matches_expected_file(hourly_render_patches: HourlyDTO) -> None:
    dto = hourly_render_patches
    actual = render_hourly(dto).text
    expected_raw = _GOLDEN_PATH.read_text(encoding="utf-8")
    expected = _norm_crlf(expected_raw)
    actual_n = _norm_crlf(actual)
    assert actual_n == expected
