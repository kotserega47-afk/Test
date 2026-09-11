import inspect
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from automation.engine import (
    GROUP_LABELS,
    _find_multiselect_by_label,
    ensure_group_added,
    ensure_group_set,
    ensure_groups_cleared,
    get_partner_chips,
    run,
)
from automation.runtime import RunConfig


def _run_cfg() -> RunConfig:
    return RunConfig(login="test-login", password="test-password")


def _playwright_context_mock():
    mock_p = MagicMock()
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page
    mock_sync = MagicMock()
    mock_sync.__enter__.return_value = mock_p
    mock_sync.__exit__.return_value = False
    return mock_sync, mock_page


def _write_wallet_xlsx(path, action, value):
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": [action], "value": [value]}
    ).to_excel(path, index=False)


def _mock_label_page(label_text: str):
    page = MagicMock()
    modal = MagicMock()
    page.locator.return_value = modal

    row = MagicMock()
    row.is_visible.return_value = True

    label = MagicMock()
    label.inner_text.return_value = label_text

    labels = MagicMock()
    labels.count.return_value = 1
    labels.first = label

    found_multiselect = MagicMock(name="group_multiselect")
    multiselects = MagicMock()
    multiselects.count.return_value = 1
    multiselects.first = found_multiselect

    def row_locator(name):
        if name == "label":
            return labels
        if name.startswith("xpath="):
            return multiselects
        return MagicMock()

    row.locator.side_effect = row_locator

    rows = MagicMock()
    rows.count.return_value = 1
    rows.nth.return_value = row

    modal.locator.return_value = rows
    return page, found_multiselect


def _mock_group_multiselect():
    multiselect = MagicMock()
    chips = MagicMock()
    chips.count.return_value = 0
    chips.first.wait_for.return_value = None
    multiselect.locator.return_value = chips
    return multiselect, chips


@pytest.mark.parametrize(
    "label_text,expected_label",
    [
        ("Группа", "Группа"),
        ("  ГРУППА  ", "Группа"),
        ("группы", "Группы"),
    ],
)
def test_find_multiselect_by_label_matching(label_text, expected_label):
    page, found = _mock_label_page(label_text)

    result = _find_multiselect_by_label(page, expected_label)

    assert result is found


def test_find_multiselect_by_label_accepts_group_labels_tuple():
    page, found = _mock_label_page("  группа  ")

    result = _find_multiselect_by_label(page, *GROUP_LABELS)

    assert result is found


def test_partner_field_uses_terminal_names():
    from automation.wallet_terminal_field import TERMINAL_FIELD_NAMES
    from automation import engine as engine_mod

    source = inspect.getsource(engine_mod.get_partner_chips)
    assert "resolve_terminal_field" in source
    assert "Привязан к терминалу" in TERMINAL_FIELD_NAMES
    assert "Партнеры" in TERMINAL_FIELD_NAMES


@patch("automation.engine._get_group_multiselect")
@patch("automation.engine._get_selected_multiselect_labels")
def test_add_group_skip_if_already_selected(mock_labels, mock_multiselect):
    multiselect = MagicMock()
    mock_multiselect.return_value = multiselect
    mock_labels.return_value = ["Group A"]

    result = ensure_group_added(MagicMock(), "Group A", RunConfig())

    assert result == "skip: group already selected"


@patch("automation.engine._get_group_multiselect")
@patch("automation.engine._get_selected_multiselect_labels")
@patch("automation.engine._multiselect_add_option")
def test_add_group_adds_if_absent(mock_add, mock_labels, mock_multiselect):
    multiselect = MagicMock()
    mock_multiselect.return_value = multiselect
    mock_labels.return_value = []
    mock_add.return_value = True

    result = ensure_group_added(MagicMock(), "Group A", RunConfig())

    assert result == "added: Group A"
    mock_add.assert_called_once()


@patch("automation.engine._get_group_multiselect")
@patch("automation.engine._get_selected_multiselect_labels")
@patch("automation.engine._multiselect_add_option")
def test_add_group_fail_if_option_missing(mock_add, mock_labels, mock_multiselect):
    multiselect = MagicMock()
    mock_multiselect.return_value = multiselect
    mock_labels.return_value = []
    mock_add.return_value = False

    with pytest.raises(Exception, match="Группа не найдена"):
        ensure_group_added(MagicMock(), "Missing", RunConfig())


@patch("automation.engine._get_group_multiselect")
@patch("automation.engine._get_selected_multiselect_labels")
def test_set_group_skip_if_only_target_selected(mock_labels, mock_multiselect):
    multiselect = MagicMock()
    mock_multiselect.return_value = multiselect
    mock_labels.return_value = ["Group A"]

    result = ensure_group_set(MagicMock(), "Group A", RunConfig())

    assert result == "skip: group already set"


@patch("automation.engine._get_group_multiselect")
@patch("automation.engine._get_selected_multiselect_labels")
@patch("automation.engine._multiselect_add_option")
def test_set_group_adds_target_if_absent(mock_add, mock_labels, mock_multiselect):
    multiselect = MagicMock()
    mock_multiselect.return_value = multiselect
    mock_labels.side_effect = [
        ["Group B"],
        ["Group B", "Group A"],
        ["Group A"],
        ["Group A"],
    ]
    mock_add.return_value = True

    page = MagicMock()
    with patch("automation.engine._multiselect_remove_label", return_value=True):
        result = ensure_group_set(page, "Group A", RunConfig())

    assert result == "set: Group A"
    mock_add.assert_called_once()


@patch("automation.engine._get_group_multiselect")
@patch("automation.engine._get_selected_multiselect_labels")
@patch("automation.engine._multiselect_remove_label")
def test_set_group_removes_extra_groups(mock_remove, mock_labels, mock_multiselect):
    multiselect = MagicMock()
    mock_multiselect.return_value = multiselect
    mock_labels.side_effect = [
        ["Group A", "Group B"],
        ["Group A", "Group B"],
        ["Group A"],
        ["Group A"],
    ]

    page = MagicMock()
    result = ensure_group_set(page, "Group A", RunConfig())

    assert result == "set: Group A"
    mock_remove.assert_called_once_with(multiselect, "Group B")


@patch("automation.engine._get_group_multiselect")
@patch("automation.engine._get_selected_multiselect_labels")
def test_set_group_fail_if_final_state_not_target_only(mock_labels, mock_multiselect):
    multiselect = MagicMock()
    mock_multiselect.return_value = multiselect
    mock_labels.side_effect = lambda *_args, **_kwargs: ["Group A", "Group B"]

    page = MagicMock()
    with patch("automation.engine._multiselect_remove_label", return_value=False):
        with pytest.raises(Exception, match="не совпало"):
            ensure_group_set(page, "Group A", RunConfig())


@patch("automation.engine._get_group_multiselect")
@patch("automation.engine._get_selected_multiselect_labels")
def test_clear_groups_skip_when_already_empty(mock_labels, mock_multiselect):
    multiselect = MagicMock()
    mock_multiselect.return_value = multiselect
    mock_labels.return_value = []

    result = ensure_groups_cleared(MagicMock(), "", RunConfig())

    assert result == "skip: no groups selected"


@patch("automation.engine._get_group_multiselect")
@patch("automation.engine._get_selected_multiselect_labels")
@patch("automation.engine._multiselect_remove_label")
def test_clear_groups_removes_all_groups(mock_remove, mock_labels, mock_multiselect):
    multiselect = MagicMock()
    mock_multiselect.return_value = multiselect
    mock_labels.side_effect = [
        ["Group A", "Group B"],
        ["Group A", "Group B"],
        [],
        [],
    ]

    page = MagicMock()
    result = ensure_groups_cleared(page, "", RunConfig())

    assert result == "cleared: groups"
    assert mock_remove.call_count == 2


@patch("automation.engine._get_group_multiselect")
@patch("automation.engine._get_selected_multiselect_labels")
def test_clear_groups_fail_if_chips_remain(mock_labels, mock_multiselect):
    multiselect = MagicMock()
    mock_multiselect.return_value = multiselect
    mock_labels.side_effect = lambda *_args, **_kwargs: ["Group A"]

    page = MagicMock()
    with patch("automation.engine._multiselect_remove_label", return_value=False):
        with pytest.raises(Exception, match="группы не удалены полностью"):
            ensure_groups_cleared(page, "", RunConfig())


@patch("automation.engine._write_result", return_value="/tmp/result.xlsx")
@patch("automation.engine._apply_auto_no_partners_status_after_actions", return_value=False)
@patch("automation.engine._ensure_logged_in")
@patch("automation.engine.sync_playwright")
@patch("automation.engine.save")
@patch("automation.engine.open_card")
@patch("automation.engine.ensure_groups_cleared")
def test_skip_only_card_does_not_call_save(
    mock_clear, mock_open, mock_save, mock_sync, _login, _auto, _write, tmp_path
):
    mock_clear.return_value = "skip: no groups selected"
    mock_sync.return_value = _playwright_context_mock()[0]
    path = tmp_path / "wallet.xlsx"
    _write_wallet_xlsx(path, "clear_groups", "")

    run(str(path), _run_cfg())

    mock_save.assert_not_called()


@patch("automation.engine._write_result", return_value="/tmp/result.xlsx")
@patch("automation.engine._apply_auto_no_partners_status_after_actions", return_value=False)
@patch("automation.engine._ensure_logged_in")
@patch("automation.engine.sync_playwright")
@patch("automation.engine.save")
@patch("automation.engine.open_card")
@patch("automation.engine.ensure_group_added")
def test_ok_mutation_still_calls_save(
    mock_add, mock_open, mock_save, mock_sync, _login, _auto, _write, tmp_path
):
    mock_add.return_value = "added: 2"
    mock_sync.return_value = _playwright_context_mock()[0]
    path = tmp_path / "wallet.xlsx"
    _write_wallet_xlsx(path, "add_group", 2.0)

    run(str(path), _run_cfg())

    mock_save.assert_called_once()


@patch("automation.engine._write_result", return_value="/tmp/result.xlsx")
@patch("automation.engine._apply_auto_no_partners_status_after_actions", return_value=False)
@patch("automation.engine._ensure_logged_in")
@patch("automation.engine.sync_playwright")
@patch("automation.engine.save")
@patch("automation.engine.open_card")
@patch("automation.engine.ensure_group_added")
def test_fail_only_card_does_not_call_save(
    mock_add, mock_open, mock_save, mock_sync, _login, _auto, _write, tmp_path
):
    mock_add.side_effect = Exception("Группа не найдена: 2")
    mock_sync.return_value = _playwright_context_mock()[0]
    path = tmp_path / "wallet.xlsx"
    _write_wallet_xlsx(path, "add_group", 2.0)

    run(str(path), _run_cfg())

    mock_save.assert_not_called()


@patch("automation.engine._get_group_multiselect")
@patch("automation.engine._get_selected_multiselect_labels")
@patch("automation.engine._multiselect_add_option")
def test_set_group_fail_if_target_option_missing(mock_add, mock_labels, mock_multiselect):
    multiselect = MagicMock()
    mock_multiselect.return_value = multiselect
    mock_labels.return_value = ["Group B"]
    mock_add.return_value = False

    with pytest.raises(Exception, match="Группа не найдена"):
        ensure_group_set(MagicMock(), "Group A", RunConfig())
