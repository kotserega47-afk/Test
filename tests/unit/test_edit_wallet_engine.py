from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from automation.edit_wallet_contract import (
    EditWalletRow,
    RESULT_FAIL_AGGREGATE_NOT_ACTIVE,
    RESULT_FAIL_FILL,
    RESULT_FAIL_NOT_FOUND,
    RESULT_FAIL_OPEN_CARD,
    RESULT_FAIL_SAVE_TIMEOUT,
    RESULT_FAIL_VALIDATION,
    RESULT_OK,
    RESULT_SKIP_NOT_FOUND,
)
from automation.add_wallet_engine import FieldNotEditableError, SaveWaitOutcome
from automation.edit_wallet_engine import (
    AggregateNotActiveError,
    _process_row,
    fill_edit_wallet_form,
    open_card_strict,
)
from automation.runtime import RunConfig


def _row(**kwargs) -> EditWalletRow:
    defaults = {
        "row_number": 2,
        "card": "9990110810347534",
        "provided_columns": frozenset({"status"}),
        "cleared_columns": frozenset(),
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
@patch("automation.edit_wallet_engine.open_card_strict")
@patch("automation.edit_wallet_engine.card_exists_strict", side_effect=[True, True])
def test_open_card_strict_called_when_exists(
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


@patch("automation.edit_wallet_engine.open_card_strict", side_effect=RuntimeError("modal fail"))
@patch("automation.edit_wallet_engine.card_exists_strict", return_value=True)
def test_fail_open_card_strict(mock_exists, mock_open):
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


@patch("automation.edit_wallet_engine._set_multiselect_list")
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


@patch("automation.edit_wallet_engine._set_multiselect_list")
def test_partners_groups_use_set_helper(mock_multiselect):
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(
            provided_columns=frozenset({"partners"}),
            partners="PartnerA;PartnerB",
        ),
    )
    mock_multiselect.assert_called_once_with(page, "Привязан к партнеру", "PartnerA;PartnerB")


@patch("automation.edit_wallet_engine._set_multiselect_list")
@patch("automation.edit_wallet_engine._select_by_label")
def test_empty_partners_column_not_in_provided_skips_multiselect(mock_select, mock_multiselect):
    page = MagicMock()
    fill_edit_wallet_form(page, _row(provided_columns=frozenset({"status"}), status="Тест"))
    mock_multiselect.assert_not_called()
    mock_select.assert_called_once()


@patch("automation.edit_wallet_engine.save_add_wallet_modal")
@patch("automation.edit_wallet_engine.fill_edit_wallet_form")
@patch("automation.edit_wallet_engine._assert_edit_modal")
@patch("automation.edit_wallet_engine.open_card_strict")
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
@patch("automation.edit_wallet_engine.open_card_strict")
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
@patch("automation.edit_wallet_engine.open_card_strict")
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
@patch("automation.edit_wallet_engine.open_card_strict")
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
    assert result.comment == "card found after save; cleared=; updated=status"


@patch("automation.edit_wallet_engine._assert_aggregate_active")
def test_aggregate_provided_does_not_click_checkbox(mock_assert_active):
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(
            provided_columns=frozenset({"aggregate"}),
            aggregate="ЧБР",
        ),
    )
    mock_assert_active.assert_called_once_with(page, "ЧБР")


@patch("automation.edit_wallet_engine._fill_edit_aggregate_field")
@patch("automation.edit_wallet_engine._assert_aggregate_active")
def test_aggregate_active_fills_nested_fields(mock_assert_active, mock_fill_field):
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(
            provided_columns=frozenset({"aggregate", "account_number"}),
            aggregate="ЧБР",
            account_number="40820810000000007380",
        ),
    )
    mock_assert_active.assert_called_once_with(page, "ЧБР")
    mock_fill_field.assert_called_once()


@patch("automation.edit_wallet_engine._assert_aggregate_active")
def test_aggregate_provided_but_not_active_fails(mock_assert_active):
    mock_assert_active.side_effect = AggregateNotActiveError("aggregate not active: ЧБР")
    page = MagicMock()
    with pytest.raises(AggregateNotActiveError, match="aggregate not active"):
        fill_edit_wallet_form(
            page,
            _row(
                provided_columns=frozenset({"aggregate"}),
                aggregate="ЧБР",
            ),
        )


@patch("automation.edit_wallet_engine.open_card_strict")
@patch("automation.edit_wallet_engine._assert_edit_modal")
@patch("automation.edit_wallet_engine.card_exists_strict", return_value=True)
def test_aggregate_not_active_returns_fail_code(mock_exists, mock_assert, mock_open):
    page = MagicMock()
    with patch(
        "automation.edit_wallet_engine.fill_edit_wallet_form",
        side_effect=AggregateNotActiveError("aggregate not active: ЧБР"),
    ):
        result = _process_row(
            page,
            _row(provided_columns=frozenset({"aggregate"}), aggregate="ЧБР"),
            operator_profile="DENIS",
        )
    assert result.result == RESULT_FAIL_AGGREGATE_NOT_ACTIVE
    assert "aggregate not active" in result.comment


@patch("automation.edit_wallet_engine._fill_edit_aggregate_field")
@patch("automation.edit_wallet_engine._assert_aggregate_block_visible")
def test_account_number_without_visible_block_fails(mock_assert_block, mock_fill_field):
    mock_assert_block.side_effect = AggregateNotActiveError("aggregate block not visible")
    page = MagicMock()
    with pytest.raises(AggregateNotActiveError, match="aggregate block not visible"):
        fill_edit_wallet_form(
            page,
            _row(
                provided_columns=frozenset({"account_number"}),
                account_number="40820810000000007380",
            ),
        )
    mock_fill_field.assert_not_called()


@patch("automation.edit_wallet_engine.open_card_strict")
@patch("automation.edit_wallet_engine._assert_edit_modal")
@patch("automation.edit_wallet_engine.card_exists_strict", return_value=True)
def test_readonly_field_returns_fail_fill_form(mock_exists, mock_assert, mock_open):
    page = MagicMock()
    with patch(
        "automation.edit_wallet_engine.fill_edit_wallet_form",
        side_effect=FieldNotEditableError("Баланс"),
    ):
        result = _process_row(
            page,
            _row(provided_columns=frozenset({"balance"}), balance="100"),
            operator_profile="DENIS",
        )
    assert result.result == RESULT_FAIL_FILL
    assert "readonly/disabled" in result.comment


@patch("automation.edit_wallet_engine._select_by_label")
@patch("automation.edit_wallet_engine._fill_edit_aggregate_field")
@patch("automation.edit_wallet_engine._assert_aggregate_block_visible")
@patch("automation.edit_wallet_engine._assert_aggregate_active")
def test_omitted_aggregate_has_no_aggregate_interaction(
    mock_assert_active,
    mock_assert_block,
    mock_fill_field,
    mock_select,
):
    page = MagicMock()
    fill_edit_wallet_form(page, _row(provided_columns=frozenset({"status"}), status="Тест"))
    mock_select.assert_called_once()
    mock_assert_active.assert_not_called()
    mock_assert_block.assert_not_called()
    mock_fill_field.assert_not_called()


@patch("automation.edit_wallet_engine.open_card_with_row_matcher")
@patch("automation.edit_wallet_engine._close_stale_modal")
def test_open_card_strict_uses_shared_stable_open_path(mock_close, mock_open_shared):
    from automation.engine import _wait_for_strict_matching_row

    page = MagicMock()
    card = "9999999999990010"

    open_card_strict(page, card)

    mock_open_shared.assert_called_once_with(page, card, _wait_for_strict_matching_row)


@patch("automation.edit_wallet_engine.open_card_with_row_matcher")
@patch("automation.edit_wallet_engine._close_stale_modal")
def test_open_card_strict_row_not_found_never_retries(mock_close, mock_open_shared):
    from automation.engine import OpenCardStageError

    page = MagicMock()
    card = "9999999999990010"
    mock_open_shared.side_effect = OpenCardStageError(
        "row_match",
        card,
        message="no strict row",
    )

    with pytest.raises(OpenCardStageError):
        open_card_strict(page, card)

    mock_open_shared.assert_called_once()


@patch("automation.edit_wallet_engine.open_card_with_row_matcher")
@patch("automation.edit_wallet_engine._close_stale_modal")
def test_open_card_strict_modal_mismatch_retries_once(mock_close, mock_open_shared):
    from automation.engine import OpenCardStageError

    page = MagicMock()
    card = "9999999999990010"
    mock_open_shared.side_effect = [
        OpenCardStageError(
            "card_verify",
            card,
            message="Модалка не соответствует карте: 9999999999990010",
        ),
        None,
    ]

    open_card_strict(page, card)

    assert mock_close.call_count == 1
    assert mock_open_shared.call_count == 2


@patch("automation.edit_wallet_engine.open_card_with_row_matcher")
@patch("automation.edit_wallet_engine._close_stale_modal")
def test_open_card_strict_modal_mismatch_fails_after_retry(mock_close, mock_open_shared):
    from automation.engine import OpenCardStageError

    page = MagicMock()
    card = "9999999999990010"
    mock_open_shared.side_effect = OpenCardStageError(
        "card_verify",
        card,
        message="Модалка не соответствует карте: 9999999999990010",
    )

    with pytest.raises(OpenCardStageError):
        open_card_strict(page, card)

    assert mock_open_shared.call_count == 3


@patch("automation.edit_wallet_engine.open_card_strict")
@patch("automation.edit_wallet_engine.card_exists_strict", return_value=True)
def test_modal_mismatch_via_process_row_returns_fail_open_card(mock_exists, mock_open):
    from automation.engine import OpenCardStageError

    mock_open.side_effect = OpenCardStageError(
        "card_verify",
        "9999999999990010",
        message="Модалка не соответствует карте: 9999999999990010",
    )
    page = MagicMock()
    result = _process_row(
        page,
        _row(card="9999999999990010"),
        operator_profile="DENIS",
    )
    assert result.result == RESULT_FAIL_OPEN_CARD


def test_engine_open_card_row_match_remains_loose():
    from automation.audit import row_matches_card, row_matches_card_strict
    from automation.engine import _try_match_row_index, _try_match_strict_row_index

    stale_row_text = "99999999999900101"
    card = "9999999999990010"
    assert row_matches_card(stale_row_text, card) is True
    assert row_matches_card_strict(stale_row_text, card) is False

    rows = MagicMock()
    row = MagicMock()
    row.inner_text.return_value = stale_row_text
    rows.count.return_value = 1
    rows.nth.return_value = row

    loose_index, _, _ = _try_match_row_index(rows, card, card)
    assert loose_index == 0
    strict_index, _, _ = _try_match_strict_row_index(rows, card, card)
    assert strict_index is None


@patch("automation.edit_wallet_engine.open_card_with_row_matcher")
@patch("automation.edit_wallet_engine._close_stale_modal")
def test_open_card_strict_modal_container_retries_like_disable(mock_close, mock_open_shared):
    from automation.engine import OpenCardStageError

    page = MagicMock()
    card = "9999999999990010"
    mock_open_shared.side_effect = [
        OpenCardStageError("modal_container", card, message="timeout"),
        None,
    ]

    open_card_strict(page, card)

    assert mock_close.call_count == 1
    assert mock_open_shared.call_count == 2


@patch("automation.edit_wallet_engine.open_card_with_row_matcher")
@patch("automation.edit_wallet_engine._close_stale_modal")
def test_open_card_strict_modal_container_fails_after_retries(mock_close, mock_open_shared):
    from automation.engine import OpenCardStageError

    page = MagicMock()
    card = "9999999999990010"
    mock_open_shared.side_effect = OpenCardStageError(
        "modal_container",
        card,
        message="timeout",
    )

    with pytest.raises(OpenCardStageError) as exc_info:
        open_card_strict(page, card)

    assert exc_info.value.stage == "modal_container"
    assert mock_close.call_count == 2
    assert mock_open_shared.call_count == 3


@patch("automation.edit_wallet_engine._update_edit_wallet_form")
@patch("automation.edit_wallet_engine._clear_edit_wallet_fields")
def test_fill_edit_wallet_form_clear_before_update(mock_clear, mock_update):
    page = MagicMock()
    row = _row(
        provided_columns=frozenset({"phone"}),
        cleared_columns=frozenset({"comment"}),
    )
    fill_edit_wallet_form(page, row)
    mock_clear.assert_called_once_with(page, row)
    mock_update.assert_called_once_with(page, row)


@patch("automation.edit_wallet_engine._clear_text_by_label")
def test_clear_text_field_called(mock_clear_text):
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(cleared_columns=frozenset({"phone"}), provided_columns=frozenset()),
    )
    mock_clear_text.assert_called_once()


@patch("automation.edit_wallet_engine._clear_textarea_by_label")
def test_clear_textarea_field_called(mock_clear_textarea):
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(cleared_columns=frozenset({"comment"}), provided_columns=frozenset()),
    )
    mock_clear_textarea.assert_called_once()


@patch("automation.edit_wallet_engine._clear_multiselect_list")
def test_clear_partners_called(mock_clear_multi):
    mock_clear_multi.return_value = False
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(cleared_columns=frozenset({"partners"}), provided_columns=frozenset()),
    )
    mock_clear_multi.assert_called()


@patch("automation.edit_wallet_engine._uncheck_kyc_checkbox")
def test_clear_kyc_called(mock_uncheck):
    mock_uncheck.return_value = False
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(cleared_columns=frozenset({"kyc"}), provided_columns=frozenset()),
    )
    mock_uncheck.assert_called_once()


@patch("automation.edit_wallet_engine._uncheck_kyc_checkbox")
def test_clear_kyc_already_unchecked_noop(mock_uncheck):
    mock_uncheck.return_value = True
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(cleared_columns=frozenset({"kyc"}), provided_columns=frozenset()),
    )


@patch("automation.edit_wallet_engine._clear_multiselect_list")
def test_clear_multiselect_already_empty_noop(mock_clear_multi):
    mock_clear_multi.return_value = True
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(cleared_columns=frozenset({"groups"}), provided_columns=frozenset()),
    )


@patch("automation.edit_wallet_engine._fill_optional_text_by_label")
@patch("automation.edit_wallet_engine._clear_text_by_label")
def test_provided_only_row_no_clear_helpers(mock_clear_text, mock_fill_text):
    page = MagicMock()
    fill_edit_wallet_form(page, _row(provided_columns=frozenset({"phone"}), phone="79491103311"))
    mock_clear_text.assert_not_called()
    mock_fill_text.assert_called_once()


@patch("automation.edit_wallet_engine._assert_aggregate_block_visible")
def test_clear_aggregate_nested_hidden_block_fails(mock_assert_block):
    mock_assert_block.side_effect = AggregateNotActiveError("aggregate block not visible")
    page = MagicMock()
    with pytest.raises(AggregateNotActiveError, match="aggregate block not visible"):
        fill_edit_wallet_form(
            page,
            _row(cleared_columns=frozenset({"account"}), provided_columns=frozenset()),
        )


@patch("automation.edit_wallet_engine.card_exists_strict", return_value=False)
def test_skip_not_found_emits_pre_search_timing(mock_exists, caplog):
    import logging

    caplog.set_level(logging.INFO)
    page = MagicMock()
    result = _process_row(page, _row(card="9999999999990007"), operator_profile="DENIS")
    assert result.result == RESULT_SKIP_NOT_FOUND

    timing_lines = [r.message for r in caplog.records if "[timing]" in r.message]
    assert any(
        "step=pre_search" in line and "outcome=skip" in line and "0007" in line
        for line in timing_lines
    )
    assert any("step=row_total" in line and "outcome=skip" in line for line in timing_lines)
    assert not any("step=open_card_strict" in line for line in timing_lines)
    assert not any("step=post_search" in line for line in timing_lines)


@patch("automation.edit_wallet_engine.save_add_wallet_modal")
@patch("automation.edit_wallet_engine.fill_edit_wallet_form")
@patch("automation.edit_wallet_engine._assert_edit_modal")
@patch("automation.edit_wallet_engine.open_card_strict")
@patch("automation.edit_wallet_engine.card_exists_strict", side_effect=[True, True])
def test_ok_row_emits_row_timing_steps(
    mock_exists,
    mock_open,
    mock_assert,
    mock_fill,
    mock_save,
    caplog,
):
    import logging

    caplog.set_level(logging.INFO)
    mock_save.return_value = SaveWaitOutcome(status="closed")
    page = MagicMock()
    result = _process_row(
        page,
        _row(
            provided_columns=frozenset({"phone", "partners", "groups", "status"}),
            cleared_columns=frozenset({"kyc"}),
            phone="79491103311",
            partners="P1",
            groups="G1",
            status="Тест",
        ),
        operator_profile="DENIS",
    )
    assert result.result == RESULT_OK

    timing_lines = [r.message for r in caplog.records if "[timing]" in r.message]
    for step in (
        "pre_search",
        "open_card_strict",
        "fill_total",
        "save_total",
        "post_search",
        "row_total",
    ):
        assert any(f"step={step}" in line for line in timing_lines), step
    assert any("step=pre_search" in line and "outcome=ok" in line for line in timing_lines)
    assert any("step=row_total" in line and "outcome=ok" in line for line in timing_lines)


@patch("automation.edit_wallet_engine._clear_field_kyc", return_value=False)
@patch("automation.edit_wallet_engine._clear_field_multiselect", return_value=False)
@patch("automation.edit_wallet_engine._set_multiselect_list")
@patch("automation.edit_wallet_engine._fill_optional_text_by_label")
@patch("automation.edit_wallet_engine._select_by_label")
def test_fill_form_emits_field_timing(
    mock_select,
    mock_text,
    mock_set_multiselect,
    mock_clear_multiselect,
    mock_clear_kyc,
    caplog,
):
    import logging

    caplog.set_level(logging.INFO)
    page = MagicMock()
    fill_edit_wallet_form(
        page,
        _row(
            provided_columns=frozenset({"phone", "partners", "groups", "status"}),
            cleared_columns=frozenset({"partners", "groups", "kyc"}),
            phone="79491103311",
            partners="P1",
            groups="G1",
            status="Тест",
        ),
    )

    timing_lines = [r.message for r in caplog.records if "[timing]" in r.message]
    for step in ("clear_total", "update_total"):
        assert any(f"step={step}" in line for line in timing_lines), step
    for column in ("partners", "groups", "kyc", "phone", "status"):
        field_step = "field_clear" if column in {"partners", "groups", "kyc"} else "field_update"
        assert any(
            f"step={field_step}" in line and f"column={column}" in line for line in timing_lines
        ), column
