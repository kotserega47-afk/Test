from integrations.wallet_editor_partner_match import (
    CHIP_ABSENT,
    CHIP_PRESENT,
    CHIP_UNRESOLVED,
    chip_matches_partner,
    partner_presence_on_chips,
)
from integrations.wallet_editor_partner_resolve import PartnerAliasMap
from automation.engine import _partner_already_selected, _partner_matches_chip


AFFOR_ALIASES = PartnerAliasMap.from_pairs(
    [
        ("Affor Тбанк abhsber (152)", "HH Affor Тбанк abhsber (152)"),
        ("Affor Юмани abhsber (153)", "HH Affor Юмани abhsber (153)"),
        ("Affor ГПБ abhsber (154)", "HH Affor ГПБ abhsber (154)"),
    ]
)


def test_partner_matches_chip_exact():
    assert chip_matches_partner("HH Partner A", "HH Partner A", PartnerAliasMap.empty()) == CHIP_PRESENT
    assert chip_matches_partner("HH Partner A", "hh partner a", PartnerAliasMap.empty()) == CHIP_PRESENT
    assert chip_matches_partner("  HH Partner A  ", "HH Partner A", PartnerAliasMap.empty()) == CHIP_PRESENT
    assert chip_matches_partner("HH Partner A", "HH Partner B", PartnerAliasMap.empty()) == CHIP_ABSENT


def test_substring_match_is_rejected():
    chip = "HH (Abhsber IN) Сбер карты + выплаты (116)"
    assert chip_matches_partner(chip, "Abhsber IN", PartnerAliasMap.empty()) == CHIP_ABSENT
    assert _partner_matches_chip(chip, "Abhsber IN") is False


def test_affor_display_and_hh_source_are_one_partner():
    display = "Affor Тбанк abhsber (152)"
    source = "HH Affor Тбанк abhsber (152)"
    assert chip_matches_partner(source, display, AFFOR_ALIASES) == CHIP_PRESENT
    assert chip_matches_partner(display, source, AFFOR_ALIASES) == CHIP_PRESENT
    assert partner_presence_on_chips([source], display, AFFOR_ALIASES) == CHIP_PRESENT
    assert partner_presence_on_chips([display], source, AFFOR_ALIASES) == CHIP_PRESENT


def test_affor_other_terminal_is_distinct():
    assert (
        chip_matches_partner(
            "HH Affor Юмани abhsber (153)",
            "HH Affor Тбанк abhsber (152)",
            AFFOR_ALIASES,
        )
        == CHIP_ABSENT
    )


def test_ambiguous_alias_fails_closed():
    aliases = PartnerAliasMap.from_pairs(
        [
            ("Affor Тбанк abhsber (152)", "HH Affor Тбанк abhsber (152)"),
            ("Affor Тбанк abhsber (152)", "Other Affor Тбанк (152)"),
        ]
    )
    assert (
        chip_matches_partner(
            "HH Affor Тбанк abhsber (152)",
            "Affor Тбанк abhsber (152)",
            aliases,
        )
        == CHIP_UNRESOLVED
    )


def test_missing_alias_map_fails_closed_for_non_exact():
    unavailable = PartnerAliasMap.unavailable()
    assert (
        chip_matches_partner(
            "HH Affor Тбанк abhsber (152)",
            "Affor Тбанк abhsber (152)",
            unavailable,
        )
        == CHIP_UNRESOLVED
    )
    assert chip_matches_partner("Ostin", "Ostin", unavailable) == CHIP_PRESENT


def test_empty_partner_is_absent():
    assert chip_matches_partner("HH Partner A", "", PartnerAliasMap.empty()) == CHIP_ABSENT
    assert chip_matches_partner("HH Partner A", "   ", PartnerAliasMap.empty()) == CHIP_ABSENT
    assert _partner_already_selected(["HH Partner A"], "") is False


def test_partner_already_selected_exact():
    chips = ["HH Partner A"]
    assert _partner_already_selected(chips, "HH Partner A") is True


def test_partner_already_selected_empty_chips():
    assert _partner_already_selected([], "HH Partner A") is False
    assert partner_presence_on_chips([], "HH Partner A", PartnerAliasMap.empty()) == CHIP_ABSENT
