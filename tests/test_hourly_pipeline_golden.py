"""Golden: payin/payout xlsx → build_hourly_dto_from_files → render_hourly (narrow integration)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from analyzers.hourly_analyzer import build_hourly_dto_from_files
from core.rules_v2.indexes import build_indexes
from core.rules_v2.models import (
    MetaInfo,
    PartnerDef,
    PartnerGroupDef,
    PartnerGroupMember,
    ReportItem,
    ReportItemMember,
    RulesSnapshotV2,
)
from core.rules_v2.normalizers import build_partner_key
from reporters.hourly_reporter import render_hourly

MSK = ZoneInfo("Europe/Moscow")

_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "hourly" / "golden" / "expected_hourly_pipeline.txt"

_RAW_ALPHA = "Hourly Alpha (8001)"
_RAW_BETA = "Hourly Beta (8002)"
_PK_ALPHA = build_partner_key(_RAW_ALPHA)
_PK_BETA = build_partner_key(_RAW_BETA)


def _dt(ts: str) -> str:
    """MSK datetime string for hourly Excel (ДД.ММ.ГГГГ ЧЧ:ММ:СС)."""
    return ts


def _write_pipeline_xlsx(tmp: Path) -> tuple[str, str]:
    payin = pd.DataFrame(
        [
            {
                "Дата/Время создания": _dt("15.01.2026 11:00:00"),
                "Партнер": _RAW_ALPHA,
                "Статус": "оплачен",
                "Сумма": "1000",
            },
            {
                "Дата/Время создания": _dt("15.01.2026 11:30:00"),
                "Партнер": _RAW_BETA,
                "Статус": "оплачен",
                "Сумма": "0",
            },
            {
                "Дата/Время создания": _dt("16.01.2026 11:00:00"),
                "Партнер": _RAW_ALPHA,
                "Статус": "оплачен",
                "Сумма": "99999",
            },
        ]
    )
    payout = pd.DataFrame(
        [
            {
                "Дата/Время создания": _dt("15.01.2026 12:00:00"),
                "Партнер": _RAW_ALPHA,
                "Статус": "оплачен",
                "Сумма": "5000",
                "метод": "UNI",
            },
            {
                "Дата/Время создания": _dt("15.01.2026 12:30:00"),
                "Партнер": _RAW_ALPHA,
                "Статус": "оплачен",
                "Сумма": "0",
                "метод": "CARD",
            },
            {
                "Дата/Время создания": _dt("16.01.2026 12:00:00"),
                "Партнер": _RAW_ALPHA,
                "Статус": "оплачен",
                "Сумма": "77777",
                "метод": "UNI",
            },
        ]
    )
    pin = tmp / "payin_min.xlsx"
    pout = tmp / "payout_min.xlsx"
    payin.to_excel(pin, index=False)
    payout.to_excel(pout, index=False)
    return str(pin), str(pout)


def _layout_lines() -> list[ReportItem]:
    return [
        ReportItem(
            item_key="pipe.layout.1",
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
            item_key="pipe.layout.2",
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
            item_key="pipe.layout.3",
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
            item_key="pipe.layout.4",
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
            item_key="pipe.layout.5",
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
            item_key="pipe.layout.6",
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


def _snapshot() -> RulesSnapshotV2:
    meta = MetaInfo(
        ruleset_version="golden-hourly-pipeline",
        updated_at=datetime(2026, 1, 15, 8, 0, 0, tzinfo=MSK),
        updated_by="golden-pipeline-test",
    )
    partners = {
        _PK_ALPHA: PartnerDef(
            partner_key=_PK_ALPHA,
            partner_code="8001",
            source_name=_RAW_ALPHA,
            display_name=_RAW_ALPHA,
        ),
        _PK_BETA: PartnerDef(
            partner_key=_PK_BETA,
            partner_code="8002",
            source_name=_RAW_BETA,
            display_name=_RAW_BETA,
        ),
    }
    groups = {
        "aurora": PartnerGroupDef(group_key="aurora", display_name="Касса Aurora"),
        "beta_br": PartnerGroupDef(group_key="beta_br", display_name="Касса Beta"),
    }
    members = [
        PartnerGroupMember(
            group_key="aurora",
            partner_key=_PK_ALPHA,
            job_key="hourly",
            is_primary=True,
        ),
        PartnerGroupMember(
            group_key="beta_br",
            partner_key=_PK_BETA,
            job_key="hourly",
            is_primary=True,
        ),
    ]
    payin_rows = [
        ReportItem(
            item_key="pipe.payin.alpha",
            report_key="hourly",
            section_key="hourly.config_payins",
            item_type="payin_row",
            source_key="src_alpha",
            method_key=None,
            display_name="Вход Alpha",
            sort_order=10,
            enabled=True,
        ),
        ReportItem(
            item_key="pipe.payin.beta",
            report_key="hourly",
            section_key="hourly.config_payins",
            item_type="payin_row",
            source_key="src_beta",
            method_key=None,
            display_name="Вход Beta",
            sort_order=20,
            enabled=True,
        ),
    ]
    payout_catalog = [
        ReportItem(
            item_key="pipe.payout.group.aurora",
            report_key="hourly",
            section_key="hourly.config_payouts",
            item_type="payout_group",
            source_key="aurora",
            method_key=None,
            display_name="Касса Aurora",
            sort_order=1,
            enabled=True,
        ),
        ReportItem(
            item_key="pipe.payout.m.aurora.uni",
            report_key="hourly",
            section_key="hourly.config_payout_methods",
            item_type="payout_method",
            source_key="aurora",
            method_key="uni",
            display_name="UNI",
            sort_order=10,
            enabled=True,
        ),
        ReportItem(
            item_key="pipe.payout.m.aurora.card",
            report_key="hourly",
            section_key="hourly.config_payout_methods",
            item_type="payout_method",
            source_key="aurora",
            method_key="card",
            display_name="CARD",
            sort_order=20,
            enabled=True,
        ),
    ]
    rim = [
        ReportItemMember(
            item_key="pipe.payin.alpha",
            member_type="source_partner",
            member_key=_PK_ALPHA,
            sort_order=1,
            enabled=True,
        ),
        ReportItemMember(
            item_key="pipe.payin.beta",
            member_type="source_partner",
            member_key=_PK_BETA,
            sort_order=1,
            enabled=True,
        ),
    ]
    items = _layout_lines() + payin_rows + payout_catalog
    return RulesSnapshotV2(
        meta=meta,
        partners=partners,
        partner_groups=groups,
        partner_group_members=members,
        report_items=items,
        report_item_members=rim,
    )


def _norm_crlf(s: str) -> str:
    return s.replace("\r\n", "\n")


@pytest.fixture
def pipeline_ctx(tmp_path: Path):
    snap = _snapshot()
    idx = build_indexes(snap)
    payin_path, payout_path = _write_pipeline_xlsx(tmp_path)

    def _job_params(
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

    start = datetime(2026, 1, 15, 10, 0, tzinfo=MSK)
    end = datetime(2026, 1, 15, 18, 0, tzinfo=MSK)
    header = datetime(2026, 1, 15, 0, 0, tzinfo=MSK)

    with (
        patch("analyzers.hourly_analyzer.get_snapshot_v2", return_value=snap),
        patch("analyzers.hourly_analyzer.get_indexes_v2", return_value=idx),
        patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap),
        patch("reporters.hourly_reporter.get_snapshot_v2", return_value=snap),
        patch("reporters.hourly_render_model.get_job_params", side_effect=_job_params),
    ):
        dto = build_hourly_dto_from_files(
            payin_path=payin_path,
            payout_path=payout_path,
            start_dt=start,
            end_dt=end,
            header_date=header,
        )
        text = render_hourly(dto).text
        yield text


def test_hourly_pipeline_golden_matches_expected_file(pipeline_ctx: str) -> None:
    actual_n = _norm_crlf(pipeline_ctx)
    expected = _norm_crlf(_GOLDEN.read_text(encoding="utf-8"))
    assert actual_n == expected
