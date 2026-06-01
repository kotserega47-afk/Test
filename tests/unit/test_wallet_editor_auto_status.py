import pytest

from automation.engine import should_auto_set_no_partners_status


@pytest.mark.parametrize(
    "final_count,current_status,explicit,expected",
    [
        (0, "Готов к работе", False, True),
        (1, "Готов к работе", False, False),
        (0, "Заблокирован", False, False),
        (0, "Не готов. Плановый прозвон", False, False),
        (0, "Готов к работе", True, False),
        (0, "  Активный вход  ", False, True),
        (0, "активный выход", False, False),
        (0, "Активный выход", False, True),
    ],
)
def test_should_auto_set_no_partners_status(final_count, current_status, explicit, expected):
    assert (
        should_auto_set_no_partners_status(
            final_count, current_status, explicit
        )
        is expected
    )


def test_should_auto_set_remove_then_add_scenario():
    assert should_auto_set_no_partners_status(1, "Готов к работе", False) is False


def test_should_auto_set_empty_current_status():
    assert should_auto_set_no_partners_status(0, "", False) is False
