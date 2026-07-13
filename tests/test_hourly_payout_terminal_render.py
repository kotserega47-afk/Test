"""Characterization tests for terminal-aware hourly payout rendering."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from analyzers.hourly_analyzer import (
    HourlyDTO,
    HourlyMethodRow,
    HourlyPayoutBlock,
    HourlyRow,
    build_hourly_dto_from_files,
)
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
from reporters.hourly_render_model import build_hourly_render_model
from reporters.hourly_reporter import render_hourly

MSK = ZoneInfo("Europe/Moscow")

_PK_AMOBILE = "amobile_out_107"
_RAW_ABH_135 = "АбхСбер OUT (135)"
_RAW_ABH_143 = "АбхСбер OUT по номеру карты (143)"
_PK_ABH_135 = build_partner_key(_RAW_ABH_135)
_PK_ABH_143 = build_partner_key(_RAW_ABH_143)

_GOLDEN_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "hourly" / "golden" / "expected_hourly_render.txt"
)


def _base_dt() -> datetime:
    return datetime(2026, 1, 15, 9, 0, tzinfo=MSK)


def _layout_lines() -> list[ReportItem]:
    return [
        ReportItem(
            item_key="term.layout.1",
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
            item_key="term.layout.2",
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
            item_key="term.layout.3",
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
            item_key="term.layout.4",
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
            item_key="term.layout.5",
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
            item_key="term.layout.6",
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


def _payout_group(group_code: str, display_name: str, sort_order: int) -> ReportItem:
    return ReportItem(
        item_key=f"term.payout.group.{group_code}",
        report_key="hourly",
        section_key="hourly.config_payouts",
        item_type="payout_group",
        source_key=group_code,
        method_key=None,
        display_name=display_name,
        sort_order=sort_order,
        enabled=True,
    )


def _payout_method(
    *,
    item_key: str,
    group_code: str,
    method_key: str,
    display_name: str,
    sort_order: int,
    comment: str = "",
) -> ReportItem:
    return ReportItem(
        item_key=item_key,
        report_key="hourly",
        section_key="hourly.config_payout_methods",
        item_type="payout_method",
        source_key=group_code,
        method_key=method_key,
        display_name=display_name,
        sort_order=sort_order,
        enabled=True,
        comment=comment,
    )


def _source_partner(item_key: str, partner_key: str, sort_order: int = 10) -> ReportItemMember:
    return ReportItemMember(
        item_key=item_key,
        member_type="source_partner",
        member_key=partner_key,
        sort_order=sort_order,
        enabled=True,
    )


def _snapshot_amobile() -> RulesSnapshotV2:
    meta = MetaInfo(
        ruleset_version="terminal-amobile",
        updated_at=_base_dt(),
        updated_by="test",
    )
    uni = _payout_method(
        item_key="term.amobile.uni",
        group_code="amobile",
        method_key="uni",
        display_name="UNI",
        sort_order=10,
    )
    mts = _payout_method(
        item_key="term.amobile.mts",
        group_code="amobile",
        method_key="mts",
        display_name="МТС",
        sort_order=20,
    )
    members = [
        _source_partner("term.amobile.uni", _PK_AMOBILE, 10),
        _source_partner("term.amobile.mts", _PK_AMOBILE, 10),
    ]
    items = _layout_lines() + [
        _payout_group("amobile", "А-Мобайл", 1),
        uni,
        mts,
    ]
    return RulesSnapshotV2(meta=meta, report_items=items, report_item_members=members)


def _snapshot_abhsber_two_terminals_uni() -> RulesSnapshotV2:
    meta = MetaInfo(
        ruleset_version="terminal-abhsber-uni",
        updated_at=_base_dt(),
        updated_by="test",
    )
    t135 = _payout_method(
        item_key="term.abh.135.uni",
        group_code="abhsber",
        method_key="uni",
        display_name="Терминал 135",
        sort_order=10,
    )
    t143 = _payout_method(
        item_key="term.abh.143.uni",
        group_code="abhsber",
        method_key="uni",
        display_name="Терминал 143",
        sort_order=20,
    )
    members = [
        _source_partner("term.abh.135.uni", _PK_ABH_135),
        _source_partner("term.abh.143.uni", _PK_ABH_143),
    ]
    items = _layout_lines() + [
        _payout_group("abhsber", "АбхСбер", 1),
        t135,
        t143,
    ]
    return RulesSnapshotV2(meta=meta, report_items=items, report_item_members=members)


def _snapshot_legacy_no_source_partners() -> RulesSnapshotV2:
    meta = MetaInfo(
        ruleset_version="terminal-legacy",
        updated_at=_base_dt(),
        updated_by="test",
    )
    items = _layout_lines() + [
        _payout_group("PG1", "Alpha Group", 1),
        _payout_method(
            item_key="term.legacy.uni",
            group_code="PG1",
            method_key="uni",
            display_name="Uni Method",
            sort_order=10,
        ),
        _payout_method(
            item_key="term.legacy.card",
            group_code="PG1",
            method_key="card",
            display_name="Card Method",
            sort_order=20,
        ),
    ]
    return RulesSnapshotV2(meta=meta, report_items=items, report_item_members=[])


def _snapshot_method_name_display() -> RulesSnapshotV2:
    """method_code=uni, method_name=СБП — display must be СБП."""
    meta = MetaInfo(
        ruleset_version="terminal-method-name",
        updated_at=_base_dt(),
        updated_by="test",
    )
    row = _payout_method(
        item_key="term.abh.sbp",
        group_code="abhsber",
        method_key="uni",
        display_name="СБП",
        sort_order=10,
    )
    members = [_source_partner("term.abh.sbp", _PK_ABH_135)]
    items = _layout_lines() + [
        _payout_group("abhsber", "АбхСбер", 1),
        row,
    ]
    return RulesSnapshotV2(meta=meta, report_items=items, report_item_members=members)


def _render_payout_lines(dto: HourlyDTO, snap: RulesSnapshotV2, *, hide_inactive: bool = False) -> list[str]:
    params = {"hide_inactive_rows": hide_inactive}
    with (
        patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap),
        patch("reporters.hourly_render_model.get_job_params", return_value=params),
    ):
        return build_hourly_render_model(dto).model["payouts.items"]


def test_two_terminals_same_method_independent_lines() -> None:
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[
            HourlyPayoutBlock(
                group_code="abhsber",
                title="АбхСбер",
                methods=[
                    HourlyMethodRow(
                        method_code="UNI",
                        title="UNI",
                        amount=3000.0,
                        partner_key=_PK_ABH_135,
                    ),
                    HourlyMethodRow(
                        method_code="UNI",
                        title="UNI",
                        amount=1500.0,
                        partner_key=_PK_ABH_143,
                    ),
                ],
            )
        ],
        payin=[],
    )
    lines = _render_payout_lines(dto, _snapshot_abhsber_two_terminals_uni())
    text = "\n".join(lines)
    assert " - Терминал 135 – 3 000" in text
    assert " - Терминал 143 – 1 500" in text
    assert "UNI" not in text


def test_single_terminal_two_methods_amobile_regression() -> None:
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[
            HourlyPayoutBlock(
                group_code="amobile",
                title="А-Мобайл",
                methods=[
                    HourlyMethodRow(
                        method_code="UNI",
                        title="UNI",
                        amount=1000.0,
                        partner_key=_PK_AMOBILE,
                    ),
                    HourlyMethodRow(
                        method_code="MTS",
                        title="MTS",
                        amount=200.0,
                        partner_key=_PK_AMOBILE,
                    ),
                ],
            )
        ],
        payin=[],
    )
    lines = _render_payout_lines(dto, _snapshot_amobile())
    text = "\n".join(lines)
    assert "1) А-Мобайл:" in text
    assert " - UNI – 1 000" in text
    assert " - МТС – 200" in text


def test_method_name_is_display_label_not_method_code() -> None:
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[
            HourlyPayoutBlock(
                group_code="abhsber",
                title="АбхСбер",
                methods=[
                    HourlyMethodRow(
                        method_code="UNI",
                        title="UNI",
                        amount=500.0,
                        partner_key=_PK_ABH_135,
                    ),
                ],
            )
        ],
        payin=[],
    )
    lines = _render_payout_lines(dto, _snapshot_method_name_display())
    text = "\n".join(lines)
    assert " - СБП – 500" in text
    assert "UNI" not in text


def test_filled_enum_method_used_over_default_method(tmp_path: Path) -> None:
    snap = RulesSnapshotV2(
        meta=MetaInfo(
            ruleset_version="enum-method",
            updated_at=_base_dt(),
            updated_by="test",
        ),
        partners={
            _PK_ABH_135: PartnerDef(
                partner_key=_PK_ABH_135,
                partner_code="135",
                source_name=_RAW_ABH_135,
                display_name=_RAW_ABH_135,
                enabled=True,
            ),
        },
        partner_groups={
            "abhsber": PartnerGroupDef(
                group_key="abhsber",
                display_name="АбхСбер",
                enabled=True,
            ),
        },
        partner_group_members=[
            PartnerGroupMember(
                group_key="abhsber",
                partner_key=_PK_ABH_135,
                job_key="hourly",
                is_primary=True,
                default_method_key="uni",
            ),
        ],
        report_items=_layout_lines()
        + [
            _payout_group("abhsber", "АбхСбер", 1),
            _payout_method(
                item_key="term.abh.karty",
                group_code="abhsber",
                method_key="karty",
                display_name="Карты",
                sort_order=10,
            ),
        ],
        report_item_members=[_source_partner("term.abh.karty", _PK_ABH_135)],
    )
    payout = pd.DataFrame(
        [
            {
                "Дата/Время создания": "15.01.2026 12:00:00",
                "Партнер": _RAW_ABH_135,
                "Статус": "оплачен",
                "Сумма": "700",
                "enum метод": "Карты",
            },
        ]
    )
    payin = pd.DataFrame(
        columns=["Дата/Время создания", "Партнер", "Статус", "Сумма"]
    )
    pin = tmp_path / "payin.xlsx"
    pout = tmp_path / "payout.xlsx"
    payin.to_excel(pin, index=False)
    payout.to_excel(pout, index=False)

    with (
        patch("analyzers.hourly_analyzer.get_snapshot_v2", return_value=snap),
        patch("analyzers.hourly_analyzer.get_indexes_v2", return_value=build_indexes(snap)),
        patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap),
        patch("reporters.hourly_render_model.get_job_params", return_value={"hide_inactive_rows": False}),
    ):
        dto = build_hourly_dto_from_files(
            payin_path=str(pin),
            payout_path=str(pout),
            start_dt=_base_dt(),
            end_dt=datetime(2026, 1, 15, 18, 0, tzinfo=MSK),
            header_date=_base_dt(),
        )
        lines = build_hourly_render_model(dto).model["payouts.items"]

    text = "\n".join(lines)
    assert " - Карты – 700" in text
    assert "UNI" not in text


def test_same_terminal_and_method_amounts_are_summed(tmp_path: Path) -> None:
    snap = _snapshot_abhsber_two_terminals_uni()
    payout = pd.DataFrame(
        [
            {
                "Дата/Время создания": "15.01.2026 12:00:00",
                "Партнер": _RAW_ABH_135,
                "Статус": "оплачен",
                "Сумма": "1000",
                "enum метод": "",
            },
            {
                "Дата/Время создания": "15.01.2026 12:30:00",
                "Партнер": _RAW_ABH_135,
                "Статус": "оплачен",
                "Сумма": "2000",
                "enum метод": "",
            },
        ]
    )
    payin = pd.DataFrame(
        columns=["Дата/Время создания", "Партнер", "Статус", "Сумма"]
    )
    pin = tmp_path / "payin.xlsx"
    pout = tmp_path / "payout.xlsx"
    payin.to_excel(pin, index=False)
    payout.to_excel(pout, index=False)

    partners = {
        _PK_ABH_135: PartnerDef(
            partner_key=_PK_ABH_135,
            partner_code="135",
            source_name=_RAW_ABH_135,
            display_name=_RAW_ABH_135,
            enabled=True,
        ),
        _PK_ABH_143: PartnerDef(
            partner_key=_PK_ABH_143,
            partner_code="143",
            source_name=_RAW_ABH_143,
            display_name=_RAW_ABH_143,
            enabled=True,
        ),
    }
    snap = RulesSnapshotV2(
        meta=snap.meta,
        partners=partners,
        partner_groups={
            "abhsber": PartnerGroupDef(group_key="abhsber", display_name="АбхСбер", enabled=True),
        },
        partner_group_members=[
            PartnerGroupMember(
                group_key="abhsber",
                partner_key=_PK_ABH_135,
                job_key="hourly",
                is_primary=True,
                default_method_key="uni",
            ),
            PartnerGroupMember(
                group_key="abhsber",
                partner_key=_PK_ABH_143,
                job_key="hourly",
                is_primary=True,
                default_method_key="uni",
            ),
        ],
        report_items=snap.report_items,
        report_item_members=snap.report_item_members,
    )

    with (
        patch("analyzers.hourly_analyzer.get_snapshot_v2", return_value=snap),
        patch("analyzers.hourly_analyzer.get_indexes_v2", return_value=build_indexes(snap)),
        patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap),
        patch("reporters.hourly_render_model.get_job_params", return_value={"hide_inactive_rows": False}),
    ):
        dto = build_hourly_dto_from_files(
            payin_path=str(pin),
            payout_path=str(pout),
            start_dt=_base_dt(),
            end_dt=datetime(2026, 1, 15, 18, 0, tzinfo=MSK),
            header_date=_base_dt(),
        )
        lines = build_hourly_render_model(dto).model["payouts.items"]

    text = "\n".join(lines)
    assert " - Терминал 135 – 3 000" in text


def test_legacy_group_without_source_partners() -> None:
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[
            HourlyPayoutBlock(
                group_code="PG1",
                title="Alpha Group",
                methods=[
                    HourlyMethodRow(method_code="uni", title="uni", amount=12345.0),
                    HourlyMethodRow(method_code="card", title="card", amount=0.0),
                ],
            )
        ],
        payin=[],
    )
    lines = _render_payout_lines(dto, _snapshot_legacy_no_source_partners(), hide_inactive=True)
    text = "\n".join(lines)
    assert "1) Alpha Group:" in text
    assert " - Uni Method – 12 345" in text
    assert "Card Method" not in text


def test_hide_inactive_rows_with_source_partners() -> None:
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[
            HourlyPayoutBlock(
                group_code="abhsber",
                title="АбхСбер",
                methods=[
                    HourlyMethodRow(
                        method_code="UNI",
                        title="UNI",
                        amount=3000.0,
                        partner_key=_PK_ABH_135,
                    ),
                    HourlyMethodRow(
                        method_code="UNI",
                        title="UNI",
                        amount=0.0,
                        partner_key=_PK_ABH_143,
                    ),
                ],
            )
        ],
        payin=[],
    )
    lines = _render_payout_lines(dto, _snapshot_abhsber_two_terminals_uni(), hide_inactive=True)
    text = "\n".join(lines)
    assert "Терминал 135" in text
    assert "Терминал 143" not in text


def test_golden_report_unchanged() -> None:
    from tests.test_hourly_render_golden import _fixed_dto, _snapshot

    dto = _fixed_dto()
    snap = _snapshot()
    params = {"hide_inactive_rows": True}
    with (
        patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap),
        patch("reporters.hourly_reporter.get_snapshot_v2", return_value=snap),
        patch("reporters.hourly_render_model.get_job_params", return_value=params),
    ):
        actual = render_hourly(dto).text.replace("\r\n", "\n")
    expected = _GOLDEN_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert actual == expected
