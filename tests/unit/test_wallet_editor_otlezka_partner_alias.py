"""Отлёжка partner resolution: Rules display→source aliases and unique terminal-id fallback."""

from __future__ import annotations

from datetime import date

import pandas as pd

from integrations.wallet_editor_partner_resolve import (
    FAIL_AMBIGUOUS_ALIAS,
    FAIL_AMBIGUOUS_ID,
    FAIL_ID_MISMATCH,
    FAIL_MISSING,
    PartnerAliasMap,
    VIA_ALIAS,
    VIA_EXACT,
    build_partner_alias_map_from_hourly_payins,
    resolve_otlezka_days,
    runtime_partner_aliases,
)
from integrations.wallet_editor_registry_lifecycle import (
    MISSING_OTLEZKA_DATE_TEXT,
    MISSING_OTLEZKA_STATUS,
    partners_to_warn,
    recalculate_all_results,
)

TODAY = date(2026, 6, 4)

DISPLAY_152 = "Affor Тбанк abhsber (152)"
SOURCE_152 = "HH Affor Тбанк abhsber (152)"
DISPLAY_153 = "Affor Юмани abhsber (153)"
SOURCE_153 = "HH Affor Юмани abhsber (153)"
DISPLAY_154 = "Affor ГПБ abhsber (154)"
SOURCE_154 = "HH Affor ГПБ abhsber (154)"

AFFOR_ALIASES = PartnerAliasMap.from_pairs(
    [
        (DISPLAY_152, SOURCE_152),
        (DISPLAY_153, SOURCE_153),
        (DISPLAY_154, SOURCE_154),
    ]
)


def _otlezka(*rows: tuple[str, int]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"partner": partner, "Полные дни": days, "comment": ""} for partner, days in rows]
    )


def _remove_row(partner: str, *, card: str = "4111") -> dict[str, str]:
    return {
        "Дата операции": "01.06.2026",
        "Дата отключения": "01.06.2026 10:00:00",
        "Дата включения": "",
        "Статус включения": "",
        "Включено": "",
        "Комментарий включения": "",
        "card": card,
        "partner": partner,
        "action": "remove_partner",
        "status": "OK",
        "comment": "",
        "hold": "",
    }


def _recalc(partner: str, otlezka: pd.DataFrame, aliases: PartnerAliasMap | None = None):
    details: dict[str, str] = {}
    out, missing = recalculate_all_results(
        pd.DataFrame([_remove_row(partner)]),
        pd.DataFrame(),
        otlezka,
        today=TODAY,
        partner_aliases=aliases or PartnerAliasMap.empty(),
        missing_details=details,
    )
    return out.iloc[0], missing, details


def test_hourly_payins_builds_display_to_source_aliases():
    payins = pd.DataFrame(
        [
            {"display_name": DISPLAY_152, "source_partners": SOURCE_152},
            {"display_name": DISPLAY_153, "source_partners": SOURCE_153},
            {"display_name": DISPLAY_154, "source_partners": SOURCE_154},
        ]
    )
    aliases = build_partner_alias_map_from_hourly_payins(payins)
    assert aliases.sources_for(DISPLAY_152) == (SOURCE_152,)
    assert aliases.sources_for(DISPLAY_153) == (SOURCE_153,)
    assert aliases.sources_for(DISPLAY_154) == (SOURCE_154,)


def test_display_152_finds_source_otlezka():
    row, missing, _ = _recalc(
        DISPLAY_152,
        _otlezka((SOURCE_152, 3)),
        AFFOR_ALIASES,
    )
    assert missing == set()
    assert row["partner"] == DISPLAY_152
    assert row["Дата включения"] == "04.06.2026"


def test_display_153_finds_source_otlezka():
    row, missing, _ = _recalc(
        DISPLAY_153,
        _otlezka((SOURCE_153, 3)),
        AFFOR_ALIASES,
    )
    assert missing == set()
    assert row["Дата включения"] == "04.06.2026"


def test_display_154_finds_source_otlezka():
    row, missing, _ = _recalc(
        DISPLAY_154,
        _otlezka((SOURCE_154, 3)),
        AFFOR_ALIASES,
    )
    assert missing == set()
    assert row["Дата включения"] == "04.06.2026"


def test_historical_hh_source_name_still_matches_exactly():
    row, missing, _ = _recalc(
        SOURCE_152,
        _otlezka((SOURCE_152, 3), (SOURCE_153, 3), (SOURCE_154, 3)),
        AFFOR_ALIASES,
    )
    assert missing == set()
    assert row["Дата включения"] == "04.06.2026"


def test_old_and_new_names_share_one_otlezka_setting():
    otlezka = _otlezka((SOURCE_152, 3))
    display_row, display_missing, _ = _recalc(DISPLAY_152, otlezka, AFFOR_ALIASES)
    source_row, source_missing, _ = _recalc(SOURCE_152, otlezka, AFFOR_ALIASES)
    assert display_missing == source_missing == set()
    assert display_row["Дата включения"] == source_row["Дата включения"] == "04.06.2026"


def test_ambiguous_same_terminal_id_is_not_chosen_silently():
    otlezka = _otlezka((SOURCE_152, 3), ("Other Affor clone (152)", 9))
    row, missing, details = _recalc(DISPLAY_152, otlezka, PartnerAliasMap.empty())
    assert DISPLAY_152 in missing
    assert row["Дата включения"] == MISSING_OTLEZKA_DATE_TEXT
    assert row["Статус включения"] == MISSING_OTLEZKA_STATUS
    assert "152" in details[DISPLAY_152]
    assert "неоднозначн" in details[DISPLAY_152].casefold()


def test_alias_still_wins_when_terminal_id_is_globally_duplicated():
    otlezka = _otlezka((SOURCE_152, 3), ("Other Affor clone (152)", 9))
    row, missing, _ = _recalc(DISPLAY_152, otlezka, AFFOR_ALIASES)
    assert missing == set()
    assert row["Дата включения"] == "04.06.2026"


def test_different_terminal_ids_are_not_mapped():
    row, missing, _ = _recalc(
        DISPLAY_152,
        _otlezka((SOURCE_154, 3)),
        AFFOR_ALIASES,
    )
    assert DISPLAY_152 in missing
    assert row["Дата включения"] == MISSING_OTLEZKA_DATE_TEXT


def test_alias_id_mismatch_fails_closed():
    aliases = PartnerAliasMap.from_pairs([(DISPLAY_152, SOURCE_154)])
    row, missing, details = _recalc(DISPLAY_152, _otlezka((SOURCE_154, 3)), aliases)
    assert DISPLAY_152 in missing
    assert row["Дата включения"] == MISSING_OTLEZKA_DATE_TEXT
    assert "не совпадает" in details[DISPLAY_152]


def test_unique_terminal_id_fallback_without_alias():
    row, missing, _ = _recalc(
        DISPLAY_152,
        _otlezka((SOURCE_152, 3)),
        PartnerAliasMap.empty(),
    )
    assert missing == set()
    assert row["Дата включения"] == "04.06.2026"


def test_no_hh_prefix_strip_without_alias_or_id():
    row, missing, _ = _recalc(
        "Affor Тбанк abhsber",
        _otlezka((SOURCE_152, 3)),
        PartnerAliasMap.empty(),
    )
    assert "Affor Тбанк abhsber" in missing
    assert row["Дата включения"] == MISSING_OTLEZKA_DATE_TEXT


def test_correct_alias_mapping_does_not_produce_warning():
    row, missing, _details = _recalc(
        DISPLAY_152,
        _otlezka((SOURCE_152, 3), (SOURCE_153, 3), (SOURCE_154, 3)),
        AFFOR_ALIASES,
    )
    assert missing == set()
    assert partners_to_warn(missing, set()) == []
    assert row["Дата включения"] == "04.06.2026"
    assert row["Статус включения"] != MISSING_OTLEZKA_STATUS


def test_resolve_exact_and_alias_and_ambiguous_id():
    from integrations.wallet_editor_partner_resolve import OtlezkaIndex

    index = OtlezkaIndex(
        days_by_norm={
            SOURCE_152.casefold(): 3,
            "other affor clone (152)": 9,
        },
        original_by_norm={
            SOURCE_152.casefold(): SOURCE_152,
            "other affor clone (152)": "Other Affor clone (152)",
        },
    )
    exact = resolve_otlezka_days(SOURCE_152, index, AFFOR_ALIASES)
    assert exact.ok and exact.via == VIA_EXACT and exact.days == 3

    aliased = resolve_otlezka_days(DISPLAY_152, index, AFFOR_ALIASES)
    assert aliased.ok and aliased.via == VIA_ALIAS and aliased.days == 3

    ambiguous = resolve_otlezka_days(DISPLAY_152, index, PartnerAliasMap.empty())
    assert not ambiguous.ok
    assert ambiguous.via == FAIL_AMBIGUOUS_ID

    mismatch = resolve_otlezka_days(
        DISPLAY_152,
        OtlezkaIndex(
            days_by_norm={SOURCE_154.casefold(): 3},
            original_by_norm={SOURCE_154.casefold(): SOURCE_154},
        ),
        PartnerAliasMap.from_pairs([(DISPLAY_152, SOURCE_154)]),
    )
    assert not mismatch.ok
    assert mismatch.via == FAIL_ID_MISMATCH

    missing = resolve_otlezka_days(
        "UnknownPartner",
        OtlezkaIndex(days_by_norm={}, original_by_norm={}),
        PartnerAliasMap.empty(),
    )
    assert not missing.ok
    assert missing.via == FAIL_MISSING


def test_canonicalization_does_not_rewrite_stored_partner_name():
    row, missing, _ = _recalc(DISPLAY_152, _otlezka((SOURCE_152, 3)), AFFOR_ALIASES)
    assert missing == set()
    assert row["partner"] == DISPLAY_152
    assert row["partner"] != SOURCE_152


def test_alias_to_missing_active_source_does_not_use_inactive_row():
    otlezka = pd.DataFrame(
        [
            {"partner": SOURCE_152, "Полные дни": 3, "comment": "", "active": False},
            {"partner": SOURCE_154, "Полные дни": 9, "comment": "", "active": True},
        ]
    )
    row, missing, _ = _recalc(DISPLAY_152, otlezka, AFFOR_ALIASES)
    assert DISPLAY_152 in missing
    assert row["Дата включения"] == MISSING_OTLEZKA_DATE_TEXT


def test_inactive_row_is_ignored_when_active_source_exists():
    otlezka = pd.DataFrame(
        [
            {"partner": DISPLAY_152, "Полные дни": 99, "comment": "", "active": False},
            {"partner": SOURCE_152, "Полные дни": 3, "comment": "", "active": True},
        ]
    )
    row, missing, _ = _recalc(DISPLAY_152, otlezka, AFFOR_ALIASES)
    assert missing == set()
    assert row["Дата включения"] == "04.06.2026"


def test_multiple_source_partners_fail_closed():
    aliases = PartnerAliasMap.from_pairs(
        [
            (DISPLAY_152, SOURCE_152),
            (DISPLAY_152, SOURCE_154),
        ]
    )
    row, missing, details = _recalc(
        DISPLAY_152,
        _otlezka((SOURCE_152, 3), (SOURCE_154, 3)),
        aliases,
    )
    assert DISPLAY_152 in missing
    assert row["Дата включения"] == MISSING_OTLEZKA_DATE_TEXT
    assert "неоднозначн" in details[DISPLAY_152].casefold()
    from integrations.wallet_editor_partner_resolve import OtlezkaIndex

    result = resolve_otlezka_days(
        DISPLAY_152,
        OtlezkaIndex(
            days_by_norm={SOURCE_152.casefold(): 3, SOURCE_154.casefold(): 3},
            original_by_norm={SOURCE_152.casefold(): SOURCE_152, SOURCE_154.casefold(): SOURCE_154},
        ),
        aliases,
    )
    assert result.via == FAIL_AMBIGUOUS_ALIAS


def test_corrupted_rules_workbook_does_not_pick_a_partner(monkeypatch, tmp_path):
    junk = tmp_path / "rules.xlsx"
    junk.write_text("not an excel workbook", encoding="utf-8")
    import integrations.wallet_editor_partner_resolve as resolve_mod

    resolve_mod._aliases_cache = None
    monkeypatch.setattr(resolve_mod, "_alias_workbook_candidates", lambda: [junk])
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    aliases = runtime_partner_aliases()
    assert aliases.display_to_sources == {}
    row, missing, _ = _recalc(
        DISPLAY_152,
        _otlezka((SOURCE_152, 3), ("Other Affor clone (152)", 9)),
        aliases,
    )
    assert DISPLAY_152 in missing
    assert row["Дата включения"] == MISSING_OTLEZKA_DATE_TEXT
