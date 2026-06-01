import pytest

from automation.engine import _partner_already_selected, _partner_matches_chip


@pytest.mark.parametrize(
    "chip_text,partner,expected",
    [
        ("HH Partner A", "HH Partner A", True),
        ("HH Partner A", "hh partner a", True),
        ("  HH Partner A  ", "HH Partner A", True),
        (
            "HH (Abhsber IN) Сбер карты + выплаты (116)",
            "Abhsber IN",
            True,
        ),
        ("HH Partner A", "HH Partner B", False),
    ],
)
def test_partner_matches_chip(chip_text, partner, expected):
    assert _partner_matches_chip(chip_text, partner) is expected


def test_partner_already_selected_exact():
    chips = ["HH Partner A"]
    assert _partner_already_selected(chips, "HH Partner A") is True


def test_partner_already_selected_empty_chips():
    assert _partner_already_selected([], "HH Partner A") is False


def test_partner_matches_chip_empty_partner():
    assert _partner_matches_chip("HH Partner A", "") is False
    assert _partner_matches_chip("HH Partner A", "   ") is False


def test_partner_already_selected_empty_partner():
    assert _partner_already_selected(["HH Partner A"], "") is False
    assert _partner_already_selected(["HH Partner A"], "   ") is False
