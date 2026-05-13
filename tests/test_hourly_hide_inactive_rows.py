"""Hourly render model: hide_inactive_rows job_param (post-row filtering)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from analyzers.hourly_analyzer import HourlyDTO, HourlyMethodRow, HourlyPayoutBlock, HourlyRow
from core.rules_v2.models import MetaInfo, ReportItem, ReportItemMember, RulesSnapshotV2
from reporters.hourly_render_model import build_hourly_render_model

MSK = ZoneInfo("Europe/Moscow")


def _base_dt() -> datetime:
    return datetime(2026, 5, 13, 10, 0, tzinfo=MSK)


def _empty_dto() -> HourlyDTO:
    t = _base_dt()
    return HourlyDTO(start_dt=t, end_dt=t, header_date=t, payout=[], payin=[])


def _snapshot_two_payins_two_payout_groups() -> RulesSnapshotV2:
    """Payin key_a (order 20), key_b (order 10). Payout G1 methods UNI+CARD; G2 UNI only."""
    meta = MetaInfo(ruleset_version="t", updated_at=_base_dt(), updated_by="t")
    report_items = [
        ReportItem(
            item_key="hourly.payin.a",
            report_key="hourly",
            section_key="hourly.config_payins",
            item_type="payin_row",
            source_key="key_a",
            method_key=None,
            display_name="Alpha",
            sort_order=20,
            enabled=True,
        ),
        ReportItem(
            item_key="hourly.payin.b",
            report_key="hourly",
            section_key="hourly.config_payins",
            item_type="payin_row",
            source_key="key_b",
            method_key=None,
            display_name="Beta",
            sort_order=10,
            enabled=True,
        ),
        ReportItem(
            item_key="hourly.payout.group.g1",
            report_key="hourly",
            section_key="hourly.config_payouts",
            item_type="payout_group",
            source_key="G1",
            method_key=None,
            display_name="Group One",
            sort_order=1,
            enabled=True,
        ),
        ReportItem(
            item_key="hourly.payout.group.g2",
            report_key="hourly",
            section_key="hourly.config_payouts",
            item_type="payout_group",
            source_key="G2",
            method_key=None,
            display_name="Group Two",
            sort_order=2,
            enabled=True,
        ),
        ReportItem(
            item_key="hourly.payout.m.g1.uni",
            report_key="hourly",
            section_key="hourly.config_payout_methods",
            item_type="payout_method",
            source_key="G1",
            method_key="UNI",
            display_name="UNI",
            sort_order=1,
            enabled=True,
        ),
        ReportItem(
            item_key="hourly.payout.m.g1.card",
            report_key="hourly",
            section_key="hourly.config_payout_methods",
            item_type="payout_method",
            source_key="G1",
            method_key="CARD",
            display_name="CARD",
            sort_order=2,
            enabled=True,
        ),
        ReportItem(
            item_key="hourly.payout.m.g2.uni",
            report_key="hourly",
            section_key="hourly.config_payout_methods",
            item_type="payout_method",
            source_key="G2",
            method_key="UNI",
            display_name="UNI",
            sort_order=1,
            enabled=True,
        ),
    ]
    return RulesSnapshotV2(meta=meta, report_items=report_items, report_item_members=[])


def _snapshot_payin_with_breaks() -> RulesSnapshotV2:
    """Three payin rows; first and third have group_break_after; middle inactive with hide."""
    meta = MetaInfo(ruleset_version="t", updated_at=_base_dt(), updated_by="t")
    report_items = [
        ReportItem(
            item_key="hourly.payin.1",
            report_key="hourly",
            section_key="hourly.config_payins",
            item_type="payin_row",
            source_key="k1",
            method_key=None,
            display_name="R1",
            sort_order=1,
            enabled=True,
        ),
        ReportItem(
            item_key="hourly.payin.2",
            report_key="hourly",
            section_key="hourly.config_payins",
            item_type="payin_row",
            source_key="k2",
            method_key=None,
            display_name="R2",
            sort_order=2,
            enabled=True,
        ),
        ReportItem(
            item_key="hourly.payin.3",
            report_key="hourly",
            section_key="hourly.config_payins",
            item_type="payin_row",
            source_key="k3",
            method_key=None,
            display_name="R3",
            sort_order=3,
            enabled=True,
        ),
    ]
    members = [
        ReportItemMember(
            item_key="hourly.payin.1",
            member_type="group_break_after",
            member_key="1",
            sort_order=1,
            enabled=True,
        ),
        ReportItemMember(
            item_key="hourly.payin.2",
            member_type="group_break_after",
            member_key="1",
            sort_order=1,
            enabled=True,
        ),
        ReportItemMember(
            item_key="hourly.payin.3",
            member_type="group_break_after",
            member_key="1",
            sort_order=1,
            enabled=True,
        ),
    ]
    return RulesSnapshotV2(meta=meta, report_items=report_items, report_item_members=members)


def _snapshot_payout_group_all_zero_methods() -> RulesSnapshotV2:
    """G1 has two methods; G_empty has one method (for zero-only group header test)."""
    meta = MetaInfo(ruleset_version="t", updated_at=_base_dt(), updated_by="t")
    report_items = [
        ReportItem(
            item_key="hourly.payout.ge",
            report_key="hourly",
            section_key="hourly.config_payouts",
            item_type="payout_group",
            source_key="G_EMPTY",
            method_key=None,
            display_name="Empty Group",
            sort_order=1,
            enabled=True,
        ),
        ReportItem(
            item_key="hourly.payout.g1",
            report_key="hourly",
            section_key="hourly.config_payouts",
            item_type="payout_group",
            source_key="G1",
            method_key=None,
            display_name="Has Pay",
            sort_order=2,
            enabled=True,
        ),
        ReportItem(
            item_key="hourly.payout.m.ge.uni",
            report_key="hourly",
            section_key="hourly.config_payout_methods",
            item_type="payout_method",
            source_key="G_EMPTY",
            method_key="UNI",
            display_name="UNI",
            sort_order=1,
            enabled=True,
        ),
        ReportItem(
            item_key="hourly.payout.m.g1.uni",
            report_key="hourly",
            section_key="hourly.config_payout_methods",
            item_type="payout_method",
            source_key="G1",
            method_key="UNI",
            display_name="UNI",
            sort_order=1,
            enabled=True,
        ),
    ]
    return RulesSnapshotV2(meta=meta, report_items=report_items, report_item_members=[])


@pytest.fixture
def snap_two_payins() -> RulesSnapshotV2:
    return _snapshot_two_payins_two_payout_groups()


def test_hide_false_keeps_zero_payin_and_payout_lines(snap_two_payins: RulesSnapshotV2) -> None:
    dto = _empty_dto()
    with patch("reporters.hourly_render_model.get_job_params", return_value={}), patch(
        "reporters.hourly_render_model.get_snapshot_v2", return_value=snap_two_payins
    ):
        rm = build_hourly_render_model(dto)
    payins = rm.model["payins.items"]
    assert len(payins) == 2
    assert "Beta" in payins[0] and "0" in payins[0]
    assert "Alpha" in payins[1] and "0" in payins[1]
    payouts = rm.model["payouts.items"]
    assert "1) Group One:" in payouts
    assert " - UNI – 0" in "\n".join(payouts)


def test_hide_true_hides_inactive_payin(snap_two_payins: RulesSnapshotV2) -> None:
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[],
        payin=[HourlyRow(entity_code="key_a", title="Alpha", amount=100.0, comment="")],
    )
    with patch(
        "reporters.hourly_render_model.get_job_params", return_value={"hide_inactive_rows": True}
    ), patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap_two_payins):
        rm = build_hourly_render_model(dto)
    payins = rm.model["payins.items"]
    assert len(payins) == 1
    assert "1) Alpha" in payins[0]
    assert "100" in payins[0]
    assert "Beta" not in "\n".join(payins)


def test_hide_true_hides_inactive_payout_method(snap_two_payins: RulesSnapshotV2) -> None:
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[
            HourlyPayoutBlock(
                group_code="G1",
                title="Group One",
                methods=[
                    HourlyMethodRow(method_code="UNI", title="UNI", amount=50.0, comment=""),
                    HourlyMethodRow(method_code="CARD", title="CARD", amount=0.0, comment=""),
                ],
            )
        ],
        payin=[],
    )
    with patch(
        "reporters.hourly_render_model.get_job_params", return_value={"hide_inactive_rows": True}
    ), patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap_two_payins):
        rm = build_hourly_render_model(dto)
    text = "\n".join(rm.model["payouts.items"])
    assert "1) Group One:" in text
    assert "UNI" in text and "50" in text
    assert "CARD" not in text


def test_hide_true_payin_only_no_payout_lines(snap_two_payins: RulesSnapshotV2) -> None:
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[],
        payin=[HourlyRow(entity_code="key_b", title="Beta", amount=1.0, comment="")],
    )
    with patch(
        "reporters.hourly_render_model.get_job_params", return_value={"hide_inactive_rows": True}
    ), patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap_two_payins):
        rm = build_hourly_render_model(dto)
    assert rm.model["payins.items"]
    assert rm.model["payouts.items"] == []


def test_hide_true_payout_only_no_payin_lines(snap_two_payins: RulesSnapshotV2) -> None:
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[
            HourlyPayoutBlock(
                group_code="G2",
                title="Group Two",
                methods=[HourlyMethodRow(method_code="UNI", title="UNI", amount=7.0, comment="")],
            )
        ],
        payin=[],
    )
    with patch(
        "reporters.hourly_render_model.get_job_params", return_value={"hide_inactive_rows": True}
    ), patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap_two_payins):
        rm = build_hourly_render_model(dto)
    assert rm.model["payins.items"] == []
    assert "1) Group Two:" in "\n".join(rm.model["payouts.items"])


def test_hide_true_payout_group_all_methods_inactive_no_header() -> None:
    snap = _snapshot_payout_group_all_zero_methods()
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[
            HourlyPayoutBlock(
                group_code="G1",
                title="Has Pay",
                methods=[HourlyMethodRow(method_code="UNI", title="UNI", amount=3.0, comment="")],
            )
        ],
        payin=[],
    )
    with patch(
        "reporters.hourly_render_model.get_job_params", return_value={"hide_inactive_rows": True}
    ), patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap):
        rm = build_hourly_render_model(dto)
    text = "\n".join(rm.model["payouts.items"])
    assert "Empty Group" not in text
    assert "1) Has Pay:" in text
    assert "G_EMPTY" not in text


def test_hide_true_preserves_sort_order_among_visible_payins(snap_two_payins: RulesSnapshotV2) -> None:
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[],
        payin=[
            HourlyRow(entity_code="key_a", title="Alpha", amount=10.0, comment=""),
            HourlyRow(entity_code="key_b", title="Beta", amount=20.0, comment=""),
        ],
    )
    with patch(
        "reporters.hourly_render_model.get_job_params", return_value={"hide_inactive_rows": True}
    ), patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap_two_payins):
        rm = build_hourly_render_model(dto)
    payins = rm.model["payins.items"]
    assert len(payins) == 2
    assert "Beta" in payins[0] and payins[0].startswith("1)")
    assert "Alpha" in payins[1] and payins[1].startswith("2)")


def test_hide_true_group_break_no_double_blank_no_trailing_blank() -> None:
    snap = _snapshot_payin_with_breaks()
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[],
        payin=[
            HourlyRow(entity_code="k1", title="R1", amount=1.0, comment=""),
            HourlyRow(entity_code="k3", title="R3", amount=3.0, comment=""),
        ],
    )
    with patch(
        "reporters.hourly_render_model.get_job_params", return_value={"hide_inactive_rows": True}
    ), patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap):
        rm = build_hourly_render_model(dto)
    payins = rm.model["payins.items"]
    joined = "\n".join(payins)
    assert "\n\n\n" not in joined
    assert not payins or payins[-1] != ""


def test_comment_only_zero_amount_not_active(snap_two_payins: RulesSnapshotV2) -> None:
    dto = HourlyDTO(
        start_dt=_base_dt(),
        end_dt=_base_dt(),
        header_date=_base_dt(),
        payout=[],
        payin=[
            HourlyRow(entity_code="key_b", title="Beta", amount=0.0, comment="note only"),
        ],
    )
    with patch(
        "reporters.hourly_render_model.get_job_params", return_value={"hide_inactive_rows": True}
    ), patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap_two_payins):
        rm = build_hourly_render_model(dto)
    assert rm.model["payins.items"] == []


def test_hide_inactive_rows_default_false_missing_key(snap_two_payins: RulesSnapshotV2) -> None:
    dto = _empty_dto()
    with patch(
        "reporters.hourly_render_model.get_job_params", return_value={"send_enabled": True}
    ), patch("reporters.hourly_render_model.get_snapshot_v2", return_value=snap_two_payins):
        rm = build_hourly_render_model(dto)
    assert len(rm.model["payins.items"]) == 2
