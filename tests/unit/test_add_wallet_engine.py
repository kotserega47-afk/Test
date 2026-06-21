from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from automation.add_wallet_contract import (
    AddWalletRow,
    RESULT_DRY_RUN,
    RESULT_FAIL_FILL,
    RESULT_FAIL_NOT_FOUND,
    RESULT_FAIL_SAVE_TIMEOUT,
    RESULT_FAIL_VALIDATION,
    RESULT_OK,
    RESULT_SKIP_DUP_CARD,
)
from automation.add_wallet_engine import (
    ACCOUNT_NUMBER_LABELS,
    SaveWaitOutcome,
    _process_row,
    card_exists_strict,
    checkbox_label_matches,
    detect_add_wallet_validation_error,
    fill_add_wallet_form,
    fill_aggregate_block_fields,
    fill_text_by_label_if_present,
    get_selected_aggregate_block,
    select_single_aggregate_checkbox,
    wait_modal_closed_or_error,
)
from automation.audit import row_matches_card_strict
from automation.runtime import RunConfig


def _row(**kwargs) -> AddWalletRow:
    defaults = {
        "row_number": 2,
        "card": "9990110810347534",
        "phone": "79491103311",
    }
    defaults.update(kwargs)
    return AddWalletRow(**defaults)


def test_row_matches_card_strict_equality_only():
    digits = "9990110810347534"
    assert row_matches_card_strict("9990110810347534", digits)
    assert row_matches_card_strict("9990 1108 1034 7534", digits)
    assert row_matches_card_strict("99901108103475349990110810347534", digits) is False
    assert row_matches_card_strict("999011081034753", digits) is False
    from automation.audit import row_matches_card

    assert row_matches_card("99901108103475349990110810347534", digits) is True


def test_card_exists_strict_match():
    page = MagicMock()
    row = MagicMock()
    row.inner_text.return_value = "9990110810347534"
    rows = MagicMock()
    rows.count.return_value = 1
    rows.nth.return_value = row

    def locator(selector: str):
        if selector == "tr.pointer":
            return rows
        if selector == 'input[placeholder="Карта"]':
            return MagicMock()
        if selector == 'button:has-text("Применить")':
            return MagicMock()
        return MagicMock()

    page.locator.side_effect = locator
    page.wait_for_timeout = MagicMock()
    body = MagicMock()
    body.inner_text.return_value = "Всего: 1"
    page.locator.return_value = body

    with patch("automation.add_wallet_engine._submit_card_filter"):
        with patch("automation.add_wallet_engine._page_shows_empty_results", return_value=False):
            page.locator.side_effect = locator
            assert card_exists_strict(page, "9990110810347534") is True


@patch("automation.add_wallet_engine.card_exists_strict", side_effect=[True])
def test_duplicate_before_create(mock_exists):
    page = MagicMock()
    cfg = RunConfig(login="u", password="p", dry_run=False)
    result = _process_row(page, _row(), cfg=cfg, operator_profile="DENIS")
    assert result.result == RESULT_SKIP_DUP_CARD


@patch("automation.add_wallet_engine.card_exists_strict", side_effect=[False])
def test_dry_run_would_create(mock_exists):
    page = MagicMock()
    cfg = RunConfig(login="u", password="p", dry_run=True)
    result = _process_row(page, _row(), cfg=cfg, operator_profile="DENIS")
    assert result.result == RESULT_DRY_RUN


@patch("automation.add_wallet_engine.save_add_wallet_modal")
@patch("automation.add_wallet_engine.fill_add_wallet_form")
@patch("automation.add_wallet_engine.open_add_wallet_modal")
@patch("automation.add_wallet_engine._assert_create_modal")
@patch("automation.add_wallet_engine.card_exists_strict", side_effect=[False, True])
def test_ok_after_post_search(
    mock_exists,
    mock_assert,
    mock_open,
    mock_fill,
    mock_save,
):
    mock_save.return_value = SaveWaitOutcome(status="closed")
    page = MagicMock()
    cfg = RunConfig(login="u", password="p", dry_run=False)
    result = _process_row(page, _row(), cfg=cfg, operator_profile="DENIS")
    assert result.result == RESULT_OK


@patch("automation.add_wallet_engine.save_add_wallet_modal")
@patch("automation.add_wallet_engine.fill_add_wallet_form")
@patch("automation.add_wallet_engine.open_add_wallet_modal")
@patch("automation.add_wallet_engine._assert_create_modal")
@patch("automation.add_wallet_engine.card_exists_strict", side_effect=[False, False])
def test_fail_not_found_after_save(
    mock_exists,
    mock_assert,
    mock_open,
    mock_fill,
    mock_save,
):
    mock_save.return_value = SaveWaitOutcome(status="closed")
    page = MagicMock()
    cfg = RunConfig(login="u", password="p", dry_run=False)
    result = _process_row(page, _row(), cfg=cfg, operator_profile="DENIS")
    assert result.result == RESULT_FAIL_NOT_FOUND


@patch("automation.add_wallet_engine.save_add_wallet_modal")
@patch("automation.add_wallet_engine.fill_add_wallet_form")
@patch("automation.add_wallet_engine.open_add_wallet_modal")
@patch("automation.add_wallet_engine._assert_create_modal")
@patch("automation.add_wallet_engine.card_exists_strict", return_value=False)
def test_fail_save_validation(
    mock_exists,
    mock_assert,
    mock_open,
    mock_fill,
    mock_save,
):
    mock_save.return_value = SaveWaitOutcome(status="validation", detail="required field")
    page = MagicMock()
    cfg = RunConfig(login="u", password="p", dry_run=False)
    result = _process_row(page, _row(), cfg=cfg, operator_profile="DENIS")
    assert result.result == RESULT_FAIL_VALIDATION


@patch("automation.add_wallet_engine.save_add_wallet_modal")
@patch("automation.add_wallet_engine.fill_add_wallet_form")
@patch("automation.add_wallet_engine.open_add_wallet_modal")
@patch("automation.add_wallet_engine._assert_create_modal")
@patch("automation.add_wallet_engine.card_exists_strict", return_value=False)
def test_fail_save_timeout(
    mock_exists,
    mock_assert,
    mock_open,
    mock_fill,
    mock_save,
):
    mock_save.return_value = SaveWaitOutcome(status="timeout")
    page = MagicMock()
    cfg = RunConfig(login="u", password="p", dry_run=False)
    result = _process_row(page, _row(), cfg=cfg, operator_profile="DENIS")
    assert result.result == RESULT_FAIL_SAVE_TIMEOUT


def test_wait_modal_closed_or_error_validation():
    page = MagicMock()
    modal = MagicMock()
    modal.is_visible.return_value = True

    def locator(selector: str):
        if selector == "#wallet-add-modal___BV_modal_body_":
            return modal
        loc = MagicMock()
        loc.count.return_value = 1
        item = MagicMock()
        item.is_visible.return_value = True
        item.inner_text.return_value = "Ошибка валидации"
        loc.nth.return_value = item
        loc.first = item
        return loc

    page.locator.side_effect = locator
    page.wait_for_timeout = MagicMock()

    with patch(
        "automation.add_wallet_engine.detect_add_wallet_validation_error",
        return_value="Ошибка валидации",
    ):
        outcome = wait_modal_closed_or_error(page, timeout_ms=500)
    assert outcome.status == "validation"


def test_detect_validation_error_in_modal():
    page = MagicMock()
    modal = MagicMock()
    modal.is_visible.return_value = True
    feedback = MagicMock()
    feedback.count.return_value = 1
    feedback.nth.return_value = feedback
    feedback.is_visible.return_value = True
    feedback.inner_text.return_value = "Поле обязательно"
    modal.locator.return_value = feedback

    page.locator.return_value = modal
    assert detect_add_wallet_validation_error(page) == "Поле обязательно"


def test_checkbox_label_matches_exact():
    assert checkbox_label_matches("ЧБР", "ЧБР")
    assert checkbox_label_matches("  ЧБР ", "ЧБР")
    assert not checkbox_label_matches("Тинькофф АПК", "ЧБР")


class _FakeInput:
    def __init__(self):
        self.values: list[str] = []

    def fill(self, value: str) -> None:
        self.values.append(value)


class _FakeRow:
    def __init__(self, label: str, visible: bool = True):
        self.label = label
        self.visible = visible
        self._input = _FakeInput()

    def is_visible(self) -> bool:
        return self.visible

    def locator(self, selector: str):
        child = MagicMock()
        if selector == "label":
            labels = MagicMock()
            labels.count.return_value = 1
            label = MagicMock()
            label.inner_text.return_value = self.label
            labels.first = label
            child = labels
        elif "input" in selector:
            inputs = MagicMock()
            inputs.count.return_value = 1
            inputs.first = self._input
            child = inputs
        return child


class _FakeBlock:
    def __init__(self, rows: list[_FakeRow]):
        self.rows = rows

    def locator(self, selector: str):
        if selector == "div.row":
            rows = MagicMock()
            rows.count.return_value = len(self.rows)
            rows.nth.side_effect = lambda i: self.rows[i]
            return rows
        return MagicMock()


def test_fill_aggregate_block_fields_nested_values():
    block = _FakeBlock(
        [
            _FakeRow("Карта"),
            _FakeRow("Телефон"),
            _FakeRow("Аккаунт"),
            _FakeRow("MerchantId СБП"),
            _FakeRow("Номер счёта"),
        ]
    )
    row = _row(
        account="acc-99",
        merchant_id_sbp="mid-7",
        account_number="40817810",
    )
    fill_aggregate_block_fields(block, row)

    assert block.rows[0]._input.values[-1] == row.card
    assert block.rows[1]._input.values[-1] == row.phone
    assert block.rows[2]._input.values[-1] == "acc-99"
    assert block.rows[3]._input.values[-1] == "mid-7"
    assert block.rows[4]._input.values[-1] == "40817810"


def test_fill_aggregate_block_skips_missing_optional_field():
    block = _FakeBlock([_FakeRow("Карта"), _FakeRow("Телефон")])
    fill_aggregate_block_fields(block, _row(account="unused"))


def test_fill_text_by_label_if_present_required_missing_value():
    block = _FakeBlock([_FakeRow("Карта")])
    with pytest.raises(RuntimeError, match="требует значение"):
        fill_text_by_label_if_present(block, ("Карта",), "", required=True)


@patch("automation.add_wallet_engine.fill_aggregate_block_fields")
@patch("automation.add_wallet_engine.get_selected_aggregate_block")
@patch("automation.add_wallet_engine.select_single_aggregate_checkbox")
@patch("automation.add_wallet_engine._fill_multiselect_list")
@patch("automation.add_wallet_engine._select_by_label")
@patch("automation.add_wallet_engine._fill_text_by_label")
def test_fill_add_wallet_form_selects_single_aggregate(
    mock_text,
    mock_select,
    mock_multi,
    mock_select_agg,
    mock_block,
    mock_fill_block,
):
    page = MagicMock()
    block = MagicMock()
    mock_block.return_value = block
    row = _row(aggregate="ЧБР")

    fill_add_wallet_form(page, row)

    mock_select_agg.assert_called_once_with(page, "ЧБР")
    mock_block.assert_called_once_with(page, "ЧБР")
    mock_fill_block.assert_called_once_with(block, row)


@patch("automation.add_wallet_engine.fill_aggregate_block_fields")
@patch("automation.add_wallet_engine.get_selected_aggregate_block")
@patch("automation.add_wallet_engine.select_single_aggregate_checkbox")
@patch("automation.add_wallet_engine._fill_multiselect_list")
@patch("automation.add_wallet_engine._select_by_label")
@patch("automation.add_wallet_engine._fill_text_by_label")
def test_fill_add_wallet_form_tinkoff_aggregate(
    mock_text,
    mock_select,
    mock_multi,
    mock_select_agg,
    mock_block,
    mock_fill_block,
):
    page = MagicMock()
    row = _row(aggregate="Тинькофф АПК")
    fill_add_wallet_form(page, row)
    mock_select_agg.assert_called_once_with(page, "Тинькофф АПК")


def test_select_single_aggregate_checkbox_checks_matching_label():
    page = MagicMock()
    modal = MagicMock()
    page.locator.return_value = modal

    chbr_label = MagicMock()
    chbr_label.inner_text.return_value = "ЧБР"
    tinkoff_label = MagicMock()
    tinkoff_label.inner_text.return_value = "Тинькофф АПК"

    labels = MagicMock()
    labels.count.return_value = 2
    labels.nth.side_effect = lambda i: (chbr_label, tinkoff_label)[i]
    modal.locator.return_value = labels

    checkbox = MagicMock()
    checkbox.count.return_value = 1
    checkbox.first.is_checked.return_value = False

    with patch("automation.add_wallet_engine._checkbox_for_label", return_value=checkbox):
        select_single_aggregate_checkbox(page, "ЧБР")

    checkbox.first.check.assert_called_once_with(force=True)


def test_select_single_aggregate_unknown_raises():
    page = MagicMock()
    modal = MagicMock()
    page.locator.return_value = modal
    labels = MagicMock()
    labels.count.return_value = 0
    modal.locator.return_value = labels

    with pytest.raises(RuntimeError, match="aggregate checkbox not found"):
        select_single_aggregate_checkbox(page, "UnknownAgg")


@patch("automation.add_wallet_engine.time.monotonic", side_effect=[0, 0, 10])
def test_get_selected_aggregate_block_not_visible_raises(mock_mono):
    page = MagicMock()
    modal = MagicMock()
    page.locator.return_value = modal
    page.wait_for_timeout = MagicMock()

    label_el = MagicMock()
    labels = MagicMock()
    labels.count.return_value = 1
    labels.nth.return_value = label_el
    modal.locator.return_value = labels

    container = MagicMock()
    label_el.locator.return_value = container
    container.count.return_value = 1

    hidden = MagicMock()
    hidden.count.return_value = 1
    hidden.first.is_visible.return_value = False
    container.locator.return_value = hidden

    with patch("automation.add_wallet_engine._find_aggregate_checkbox_label", return_value=label_el):
        with pytest.raises(RuntimeError, match="aggregate block not visible"):
            get_selected_aggregate_block(page, "ЧБР")


@patch("automation.add_wallet_engine.save_add_wallet_modal")
@patch("automation.add_wallet_engine.open_add_wallet_modal")
@patch("automation.add_wallet_engine._assert_create_modal")
@patch("automation.add_wallet_engine.card_exists_strict", side_effect=[False, True])
def test_dry_run_does_not_open_modal(mock_exists, mock_assert, mock_open, mock_save):
    page = MagicMock()
    cfg = RunConfig(login="u", password="p", dry_run=True)
    result = _process_row(page, _row(aggregate="ЧБР"), cfg=cfg, operator_profile="DENIS")
    assert result.result == RESULT_DRY_RUN
    mock_open.assert_not_called()
    mock_save.assert_not_called()


@patch("automation.add_wallet_engine.save_add_wallet_modal")
@patch("automation.add_wallet_engine.fill_add_wallet_form", side_effect=RuntimeError("aggregate block not visible: ЧБР"))
@patch("automation.add_wallet_engine.open_add_wallet_modal")
@patch("automation.add_wallet_engine._assert_create_modal")
@patch("automation.add_wallet_engine.card_exists_strict", return_value=False)
def test_missing_aggregate_block_fail_fill(
    mock_exists,
    mock_assert,
    mock_open,
    mock_fill,
    mock_save,
):
    page = MagicMock()
    cfg = RunConfig(login="u", password="p", dry_run=False)
    result = _process_row(page, _row(aggregate="ЧБР"), cfg=cfg, operator_profile="DENIS")
    assert result.result == RESULT_FAIL_FILL
    assert "aggregate block not visible" in result.comment
