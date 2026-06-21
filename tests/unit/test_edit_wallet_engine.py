from __future__ import annotations

from unittest.mock import MagicMock, patch

from automation.edit_wallet_contract import (
    EditWalletRow,
    RESULT_FAIL_NOT_FOUND,
    RESULT_FAIL_OPEN_CARD,
    RESULT_FAIL_SAVE_TIMEOUT,
    RESULT_FAIL_VALIDATION,
    RESULT_OK,
    RESULT_SKIP_NOT_FOUND,
)
from automation.add_wallet_engine import SaveWaitOutcome
from automation.edit_wallet_engine import (
    _process_row,
    fill_edit_wallet_form,
)
from automation.runtime import RunConfig


def _row(**kwargs) -> EditWalletRow:
    defaults = {
        "row_number": 2,
        "card": "9990110810347534",
        "provided_columns": frozenset({"status"}),
        "status": "Тест",
    }
    defaults.update(kwargs)
    return EditWalletRow(**defaults)


@patch("automation.edit_wallet_engine.card_exists_strict", return_value=False)
def test_skip_not_found_when_pre_check_false(mock_exists):
    page = MagicMock()
    result = _process_row(page, _row(), operator_profile="DENIS")
    assert result.result == RESULT_SKIP_NOT_FOUND
    mock_exists.assert_called_once()


@patch("automation.edit_wallet_engine.save_add_wallet_modal")
@patch("automation.edit_wallet_engine.fill_edit_wallet_form")
@patch("automation.edit_wallet_engine._assert_edit_modal")
@patch("automation.edit_wallet_engine.open_card")
@patch("automation.edit_wallet_engine.card_exists_strict", side_effect=[True, True])
def test_open_card_called_when_exists(
    mock_exists,
    mock_open,
    mock_assert,
    mock_fill,
    mock_save,
):
    mock_save.return_value = SaveWaitOutcome(status="closed")
    page = MagicMock()
    result = _process_row(page, _row(), operator_profile="DENIS")
    mock_open.assert_called_once_with(page, "9990110810347534")
    assert result.result == RESULT_OK


@patch("automation.edit_wallet_engine.open_card", side_effect=RuntimeError("modal fail"))
@patch("automation.edit_wallet_engine.card_exists_strict", return_value=True)
def test_fail_open_card(mock_exists, mock_open):
    page = MagicMock()
    result = _process_row(page, _row(), operator_profile="DENIS")
    assert result.result == RESULT_FAIL_OPEN_CARD


@patch("automation.edit_wallet_engine._fill_optional_text_by_label")
@patch("automation.edit_wallet_engine._select_by_label")
def test_omitted_fields_do_not_call_fillers(mock_select, mock_text):
    page = MagicMock()
    fill_edit_wallet_form(page, _row(provided_columns=frozenset({"status"}), status="Тест"))
    mock_select.assert_called_once()
    mock_text.assert_not_called()


@patch("automation.edit_wallet_engine._fill_multiselect_list")
@patch("automation.edit_wallet_engine._select_by_label")
@patch("automation.edit_wallet_engine._fill_optional_text_by_label")
def test_provided_text_and_select_fields_call_fillers(mock_text, mock_select, mock_multiselect):
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(
            provided_columns=frozenset({"phone", "status", "partners", "groups"}),
            phone="79491103311",
            status="Тест",
            partners="P1",
            groups="G1",
        ),
    )
    mock_text.assert_called_once_with(page, "Телефон", "79491103311")
    assert mock_select.call_count == 1
    assert mock_multiselect.call_count == 2


@patch("automation.edit_wallet_engine._fill_multiselect_list")
def test_partners_groups_add_only(mock_multiselect):
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(
            provided_columns=frozenset({"partners"}),
            partners="PartnerA;PartnerB",
        ),
    )
    mock_multiselect.assert_called_once_with(page, "Привязан к партнеру", "PartnerA;PartnerB")


@patch("automation.edit_wallet_engine.save_add_wallet_modal")
@patch("automation.edit_wallet_engine.fill_edit_wallet_form")
@patch("automation.edit_wallet_engine._assert_edit_modal")
@patch("automation.edit_wallet_engine.open_card")
@patch("automation.edit_wallet_engine.card_exists_strict", side_effect=[True, True])
def test_save_validation_fail(
    mock_exists,
    mock_open,
    mock_assert,
    mock_fill,
    mock_save,
):
    mock_save.return_value = SaveWaitOutcome(status="validation", detail="bad field")
    page = MagicMock()
    result = _process_row(page, _row(), operator_profile="DENIS")
    assert result.result == RESULT_FAIL_VALIDATION


@patch("automation.edit_wallet_engine.save_add_wallet_modal")
@patch("automation.edit_wallet_engine.fill_edit_wallet_form")
@patch("automation.edit_wallet_engine._assert_edit_modal")
@patch("automation.edit_wallet_engine.open_card")
@patch("automation.edit_wallet_engine.card_exists_strict", side_effect=[True, True])
def test_save_timeout_fail(
    mock_exists,
    mock_open,
    mock_assert,
    mock_fill,
    mock_save,
):
    mock_save.return_value = SaveWaitOutcome(status="timeout")
    page = MagicMock()
    result = _process_row(page, _row(), operator_profile="DENIS")
    assert result.result == RESULT_FAIL_SAVE_TIMEOUT


@patch("automation.edit_wallet_engine.save_add_wallet_modal")
@patch("automation.edit_wallet_engine.fill_edit_wallet_form")
@patch("automation.edit_wallet_engine._assert_edit_modal")
@patch("automation.edit_wallet_engine.open_card")
@patch("automation.edit_wallet_engine.card_exists_strict", side_effect=[True, False])
def test_post_save_not_found(
    mock_exists,
    mock_open,
    mock_assert,
    mock_fill,
    mock_save,
):
    mock_save.return_value = SaveWaitOutcome(status="closed")
    page = MagicMock()
    result = _process_row(page, _row(), operator_profile="DENIS")
    assert result.result == RESULT_FAIL_NOT_FOUND


@patch("automation.edit_wallet_engine.save_add_wallet_modal")
@patch("automation.edit_wallet_engine.fill_edit_wallet_form")
@patch("automation.edit_wallet_engine._assert_edit_modal")
@patch("automation.edit_wallet_engine.open_card")
@patch("automation.edit_wallet_engine.card_exists_strict", side_effect=[True, True])
def test_success_ok(
    mock_exists,
    mock_open,
    mock_assert,
    mock_fill,
    mock_save,
):
    mock_save.return_value = SaveWaitOutcome(status="closed")
    page = MagicMock()
    result = _process_row(page, _row(), operator_profile="DENIS")
    assert result.result == RESULT_OK
