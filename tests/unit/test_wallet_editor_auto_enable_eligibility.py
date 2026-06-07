from __future__ import annotations

import pandas as pd
import pytest

from integrations.wallet_editor_auto_enable_eligibility import (
    CandidateRow,
    apply_run_limit,
    calculate_batch_timeout,
    is_run_limited,
    select_auto_enable_candidates,
    split_batches,
)
from integrations.wallet_editor_registry_lifecycle import (
    ACTION_REMOVE_PARTNER,
    HOLD_MARK,
    STATUS_K_VKLUCHENIYU,
    STATUS_OSHIBKA,
    STATUS_OZHIDAET,
    STATUS_PROSROCHENO,
)


def _row(
    *,
    card: str = "4111",
    partner: str = "Ostin",
    disable_at: str = "01.06.2026 10:00:00",
    enable_status: str = STATUS_K_VKLUCHENIYU,
    vklyucheno: str = "",
    action: str = ACTION_REMOVE_PARTNER,
    status: str = "OK",
    hold: str = "",
    enable_comment: str = "",
) -> dict[str, str]:
    return {
        "Дата отключения": disable_at,
        "Дата включения": "06.06.2026",
        "Статус включения": enable_status,
        "Включено": vklyucheno,
        "Комментарий включения": enable_comment,
        "card": card,
        "partner": partner,
        "action": action,
        "status": status,
        "comment": "",
        "hold": hold,
    }


def _df(*rows: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def test_select_includes_k_vklyucheniyu():
    result = select_auto_enable_candidates(_df(_row()), include_overdue=True)
    assert len(result.selected) == 1
    assert result.eligible_before_dedup == 1
    assert result.breakdown.k_vklyucheniyu == 1


def test_select_includes_prosrocheno_when_include_overdue():
    result = select_auto_enable_candidates(
        _df(_row(enable_status=STATUS_PROSROCHENO)),
        include_overdue=True,
    )
    assert len(result.selected) == 1
    assert result.breakdown.prosrocheno == 1


def test_select_excludes_prosrocheno_when_include_overdue_false():
    result = select_auto_enable_candidates(
        _df(_row(enable_status=STATUS_PROSROCHENO)),
        include_overdue=False,
    )
    assert result.selected == ()
    assert result.eligible_before_dedup == 0


def test_select_includes_empty_vklyucheno_and_fail_retry():
    empty = select_auto_enable_candidates(_df(_row(vklyucheno="")), include_overdue=True)
    fail = select_auto_enable_candidates(
        _df(
            _row(
                vklyucheno="FAIL",
                enable_status=STATUS_OSHIBKA,
                enable_comment="TECHNICAL: timeout; можно повторить",
            )
        ),
        include_overdue=True,
    )
    assert len(empty.selected) == 1
    assert empty.breakdown.empty_vklyucheno == 1
    assert len(fail.selected) == 1
    assert fail.breakdown.fail_retry == 1


@pytest.mark.parametrize("vklyucheno", ["OK", "SKIP", "ok", "skip"])
def test_select_excludes_vklyucheno_ok_and_skip(vklyucheno: str):
    result = select_auto_enable_candidates(_df(_row(vklyucheno=vklyucheno)), include_overdue=True)
    assert result.selected == ()


def test_select_excludes_hold_add_partner_bad_status_missing_partner():
    hold = select_auto_enable_candidates(
        _df(_row(hold=HOLD_MARK)),
        include_overdue=True,
    )
    add_partner = select_auto_enable_candidates(
        _df(_row(action="add_partner")),
        include_overdue=True,
    )
    bad_status = select_auto_enable_candidates(
        _df(_row(status="FAIL")),
        include_overdue=True,
    )
    missing_partner = select_auto_enable_candidates(
        _df(_row(partner="")),
        include_overdue=True,
    )
    ozhidaet = select_auto_enable_candidates(
        _df(_row(enable_status=STATUS_OZHIDAET)),
        include_overdue=True,
    )
    assert hold.selected == ()
    assert add_partner.selected == ()
    assert bad_status.selected == ()
    assert missing_partner.selected == ()
    assert ozhidaet.selected == ()


def test_dedup_latest_disable_date_per_card_partner():
    df = _df(
        _row(card="1", partner="Ostin", disable_at="01.06.2026 10:00:00"),
        _row(card="1", partner="Ostin", disable_at="05.06.2026 11:00:00"),
        _row(card="1", partner="Ostin", disable_at="03.06.2026 09:00:00"),
    )
    result = select_auto_enable_candidates(df, include_overdue=True)
    assert result.eligible_before_dedup == 3
    assert len(result.selected) == 1
    assert result.duplicates_skipped == 2
    assert result.selected[0].disable_at == "05.06.2026 11:00:00"


def test_split_batches_700_to_four_batches():
    candidates = tuple(
        CandidateRow(
            card=f"c{i}",
            partner="Ostin",
            disable_at=f"0{i}.06.2026 10:00:00",
            enable_status=STATUS_K_VKLUCHENIYU,
            vklyucheno="",
            source_row_index=i,
        )
        for i in range(700)
    )
    batches = split_batches(candidates, max_rows_per_batch=200)
    assert len(batches) == 4
    assert [len(batch) for batch in batches] == [200, 200, 200, 100]


def _make_n_selected(n: int) -> tuple[CandidateRow, ...]:
    return tuple(
        CandidateRow(
            card=f"c{i:04d}",
            partner=f"p{i:04d}",
            disable_at=f"{(i % 28) + 1:02d}.06.2026 10:00:00",
            enable_status=STATUS_K_VKLUCHENIYU,
            vklyucheno="",
            source_row_index=i,
        )
        for i in range(n)
    )


def test_apply_run_limit_zero_means_no_limit():
    candidates = _make_n_selected(297)
    limited = apply_run_limit(candidates, max_rows_per_run=0)
    assert len(limited) == 297


def test_apply_run_limit_negative_means_no_limit():
    candidates = _make_n_selected(10)
    limited = apply_run_limit(candidates, max_rows_per_run=-1)
    assert len(limited) == 10


def test_run_limit_297_to_3_single_batch():
    candidates = _make_n_selected(297)
    limited = apply_run_limit(candidates, max_rows_per_run=3)
    batches = split_batches(limited, max_rows_per_batch=3)
    assert len(limited) == 3
    assert len(batches) == 1
    assert [len(batch) for batch in batches] == [3]
    assert is_run_limited(
        selected_after_dedup=297,
        selected_for_run=3,
        max_rows_per_run=3,
    )


def test_run_limit_297_to_10_four_batches():
    candidates = _make_n_selected(297)
    limited = apply_run_limit(candidates, max_rows_per_run=10)
    batches = split_batches(limited, max_rows_per_batch=3)
    assert len(limited) == 10
    assert len(batches) == 4
    assert [len(batch) for batch in batches] == [3, 3, 3, 1]


def test_run_limit_greater_than_selected_no_truncation():
    candidates = _make_n_selected(5)
    limited = apply_run_limit(candidates, max_rows_per_run=100)
    assert len(limited) == 5
    assert not is_run_limited(
        selected_after_dedup=5,
        selected_for_run=5,
        max_rows_per_run=100,
    )


def test_calculate_batch_timeout_formula():
    assert calculate_batch_timeout(
        200,
        seconds_per_card_timeout=10,
        batch_timeout_buffer_seconds=300,
    ) == 2300
    assert calculate_batch_timeout(
        100,
        seconds_per_card_timeout=10,
        batch_timeout_buffer_seconds=300,
    ) == 1300
