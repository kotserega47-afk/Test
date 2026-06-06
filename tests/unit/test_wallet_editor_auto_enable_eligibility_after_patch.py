from __future__ import annotations

from datetime import date

import pandas as pd

from integrations.wallet_editor_auto_enable_eligibility import select_auto_enable_candidates
from integrations.wallet_editor_registry import (
    EnableRegistryUpdate,
    apply_enable_updates_to_all_results,
)
from integrations.wallet_editor_registry_lifecycle import (
    ACTION_REMOVE_PARTNER,
    ALL_RESULTS_COLUMNS,
    STATUS_K_VKLUCHENIYU,
    recalculate_all_results,
)


def _row(*, vklyucheno: str = "") -> dict[str, str]:
    return {
        "Дата отключения": "01.06.2026 10:00:00",
        "Дата включения": "06.06.2026",
        "Статус включения": STATUS_K_VKLUCHENIYU,
        "Включено": vklyucheno,
        "Комментарий включения": "",
        "card": "4111",
        "partner": "Ostin",
        "action": ACTION_REMOVE_PARTNER,
        "status": "OK",
        "comment": "",
        "hold": "",
    }


def _otlezka_df() -> pd.DataFrame:
    return pd.DataFrame([{"partner": "Ostin", "Полные дни": 5, "comment": ""}])


def _select_after_patch(vklyucheno: str, comment: str) -> int:
    df = pd.DataFrame([_row()], columns=ALL_RESULTS_COLUMNS)
    patched, _ = apply_enable_updates_to_all_results(
        df,
        [
            EnableRegistryUpdate(
                card="4111",
                partner="Ostin",
                disable_date="01.06.2026 10:00:00",
                vklyucheno=vklyucheno,
                comment=comment,
                source_row_index=0,
            )
        ],
    )
    recalculated, _ = recalculate_all_results(
        patched,
        pd.DataFrame(),
        _otlezka_df(),
        today=date(2026, 6, 6),
    )
    result = select_auto_enable_candidates(recalculated, include_overdue=True)
    return len(result.selected)


def test_patched_ok_excluded_from_next_selection():
    assert _select_after_patch("OK", "Партнёр добавлен") == 0


def test_patched_skip_excluded_from_next_selection():
    assert _select_after_patch("SKIP", "SERVICE_WORKS: test") == 0


def test_patched_fail_included_in_next_selection():
    assert _select_after_patch("FAIL", "TECHNICAL: timeout") == 1
