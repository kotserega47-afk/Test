"""Hourly analyzer: Excel partner labels → partner_code → report payin_row.source_key."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from analyzers.hourly_analyzer import (
    build_hourly_dto_from_files,
    extract_trailing_parenthetical_codes,
)
from core.rules_v2.indexes import build_indexes
from core.rules_v2.models import (
    MetaInfo,
    PartnerDef,
    ReportItem,
    ReportItemMember,
    RulesSnapshotV2,
)

MSK = ZoneInfo("Europe/Moscow")


def test_extract_trailing_codes_single() -> None:
    assert extract_trailing_parenthetical_codes("HH (Аврора Сбер) (109)") == ["109"]


def test_extract_trailing_codes_plus() -> None:
    assert extract_trailing_parenthetical_codes("X (108+109)") == ["108", "109"]


def _minimal_snapshot_for_hourly_payin() -> RulesSnapshotV2:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=MSK)
    meta = MetaInfo(
        ruleset_version="test",
        updated_at=now,
        updated_by="test",
    )
    composite_sk = "hourly.payin.17.avrora_sber_tin_108_109"
    item_key = "hourly.payin.item.composite"

    partners = {
        "hh_test_108": PartnerDef(
            partner_key="hh_test_108",
            partner_code="108",
            source_name=None,
            display_name="Test 108",
            enabled=True,
        ),
        "hh_test_109": PartnerDef(
            partner_key="hh_test_109",
            partner_code="109",
            source_name=None,
            display_name="Test 109",
            enabled=True,
        ),
    }

    report_items = [
        ReportItem(
            item_key=item_key,
            report_key="hourly",
            section_key="hourly.config_payins",
            item_type="payin_row",
            source_key=composite_sk,
            method_key=None,
            display_name="Аврора Сбер + Тинь (108+109)",
            sort_order=17,
            enabled=True,
        ),
    ]

    report_item_members = [
        ReportItemMember(
            item_key=item_key,
            member_type="source_partner",
            member_key="hh_test_108",
            sort_order=1,
            enabled=True,
        ),
        ReportItemMember(
            item_key=item_key,
            member_type="source_partner",
            member_key="hh_test_109",
            sort_order=2,
            enabled=True,
        ),
    ]

    return RulesSnapshotV2(
        meta=meta,
        partners=partners,
        report_items=report_items,
        report_item_members=report_item_members,
    )


def test_payin_rows_aggregate_by_report_source_key_composite(
    tmp_path: Path,
) -> None:
    snap = _minimal_snapshot_for_hourly_payin()
    idx = build_indexes(snap)

    payin = tmp_path / "payin.xlsx"
    payout = tmp_path / "payout.xlsx"

    day = datetime(2026, 5, 10, 14, 30, tzinfo=MSK)
    dt_s = "10.05.2026 14:30:00"

    df_payin = pd.DataFrame(
        {
            "Дата/Время создания": [dt_s, dt_s],
            "Партнер": [
                "HH (Аврора Сбер) (109)",
                "HH (Test Partner Eight) (108)",
            ],
            "Статус": ["Оплачен", "Оплачен"],
            "Сумма": [100, 50],
        }
    )
    df_payout = pd.DataFrame(
        {
            "Дата/Время создания": [dt_s],
            "Партнер": ["HH (Аврора Сбер) (109)"],
            "Статус": ["Оплачен"],
            "Сумма": [1],
            "метод": ["UNI"],
        }
    )

    df_payin.to_excel(payin, index=False)
    df_payout.to_excel(payout, index=False)

    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day.replace(hour=23, minute=59, second=0, microsecond=0)

    with patch("analyzers.hourly_analyzer.get_snapshot_v2", return_value=snap):
        with patch("analyzers.hourly_analyzer.get_indexes_v2", return_value=idx):
            dto = build_hourly_dto_from_files(
                payin_path=str(payin),
                payout_path=str(payout),
                start_dt=start,
                end_dt=end,
                header_date=start,
            )

    assert len(dto.payin) == 1
    row = dto.payin[0]
    assert row.entity_code == "hourly.payin.17.avrora_sber_tin_108_109"
    assert row.amount == pytest.approx(150.0)
    assert "108+109" in row.title or "Аврора" in row.title


def test_single_excel_cell_with_plus_codes_maps_to_shared_payin_row(
    tmp_path: Path,
) -> None:
    """``(108+109)`` in one label resolves both partner_keys → one report source_key."""
    snap = _minimal_snapshot_for_hourly_payin()
    idx = build_indexes(snap)

    payin = tmp_path / "payin.xlsx"
    payout = tmp_path / "payout.xlsx"
    day = datetime(2026, 5, 11, 10, 0, tzinfo=MSK)
    dt_s = "11.05.2026 10:00:00"

    df_payin = pd.DataFrame(
        {
            "Дата/Время создания": [dt_s],
            "Партнер": ["Combo Partner (108+109)"],
            "Статус": ["Оплачен"],
            "Сумма": [77],
        }
    )
    df_payout = pd.DataFrame(
        {
            "Дата/Время создания": [dt_s],
            "Партнер": ["Combo Partner (108+109)"],
            "Статус": ["Оплачен"],
            "Сумма": [1],
            "метод": ["UNI"],
        }
    )
    df_payin.to_excel(payin, index=False)
    df_payout.to_excel(payout, index=False)

    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day.replace(hour=23, minute=59, second=0, microsecond=0)

    with patch("analyzers.hourly_analyzer.get_snapshot_v2", return_value=snap):
        with patch("analyzers.hourly_analyzer.get_indexes_v2", return_value=idx):
            dto = build_hourly_dto_from_files(
                payin_path=str(payin),
                payout_path=str(payout),
                start_dt=start,
                end_dt=end,
                header_date=start,
            )

    assert len(dto.payin) == 1
    assert dto.payin[0].amount == pytest.approx(77.0)
    assert dto.payin[0].entity_code == "hourly.payin.17.avrora_sber_tin_108_109"


def test_unknown_partner_code_does_not_crash(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.WARNING)

    snap = _minimal_snapshot_for_hourly_payin()
    idx = build_indexes(snap)

    payin = tmp_path / "payin.xlsx"
    payout = tmp_path / "payout.xlsx"
    day = datetime(2026, 5, 10, 15, 0, tzinfo=MSK)
    dt_s = "10.05.2026 15:00:00"

    df_payin = pd.DataFrame(
        {
            "Дата/Время создания": [dt_s],
            "Партнер": ["Unknown Brand (99999)"],
            "Статус": ["Оплачен"],
            "Сумма": [10],
        }
    )
    df_payout = pd.DataFrame(
        {
            "Дата/Время создания": [dt_s],
            "Партнер": ["Unknown Brand (99999)"],
            "Статус": ["Оплачен"],
            "Сумма": [1],
            "метод": ["UNI"],
        }
    )
    df_payin.to_excel(payin, index=False)
    df_payout.to_excel(payout, index=False)

    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day.replace(hour=23, minute=59, second=0, microsecond=0)

    with patch("analyzers.hourly_analyzer.get_snapshot_v2", return_value=snap):
        with patch("analyzers.hourly_analyzer.get_indexes_v2", return_value=idx):
            dto = build_hourly_dto_from_files(
                payin_path=str(payin),
                payout_path=str(payout),
                start_dt=start,
                end_dt=end,
                header_date=start,
            )

    assert dto.payin == []
    assert "unknown partner_code 99999" in caplog.text
