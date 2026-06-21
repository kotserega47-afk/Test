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
    ADD_WALLET_LOWER_FORM_CONTROL_TYPES,
    SaveWaitOutcome,
    PHASE2_OPTIONAL_SELECT_FIELDS,
    PHASE2_OPTIONAL_TEXT_FIELDS,
    _detect_aggregate_expansion,
    _fill_gender_radio,
    _fill_kyc_checkbox,
    _find_kyc_checkbox,
    _find_kyc_search_start_row_index,
    _kyc_checkbox_for_label,
    _fill_optional_select_by_label,
    _fill_optional_text_by_label,
    _fill_optional_textarea_by_label,
    _fill_phase2_top_level_fields,
    _process_row,
    card_exists_strict,
    checkbox_label_matches,
    detect_add_wallet_validation_error,
    fill_add_wallet_form,
    fill_aggregate_field_if_present,
    fill_aggregate_modal_fields,
    is_kyc_true,
    normalize_gender_option,
    select_single_aggregate_checkbox,
    wait_for_aggregate_fields_visible,
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


def _modal_with_label_rows(label_rows: list[list[str]]):
    row_mocks = []
    for row_labels in label_rows:
        label_els = []
        for text in row_labels:
            label_el = MagicMock()
            label_el.inner_text.return_value = text
            label_els.append(label_el)
        labels_loc = MagicMock()
        labels_loc.count.return_value = len(label_els)
        labels_loc.nth.side_effect = lambda i, els=label_els: els[i]
        row = MagicMock()
        row.locator.return_value = labels_loc
        row_mocks.append(row)

    rows_loc = MagicMock()
    rows_loc.count.return_value = len(row_mocks)
    rows_loc.nth.side_effect = lambda i: row_mocks[i]

    modal = MagicMock()
    modal.locator.return_value = rows_loc
    return modal, row_mocks


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


def test_fill_aggregate_modal_fields_nested_values():
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
    fill_aggregate_modal_fields(block, row)

    assert block.rows[0]._input.values[-1] == row.card
    assert block.rows[1]._input.values[-1] == row.phone
    assert block.rows[2]._input.values[-1] == "acc-99"
    assert block.rows[3]._input.values[-1] == "mid-7"
    assert block.rows[4]._input.values[-1] == "40817810"


def test_fill_aggregate_modal_fields_uses_last_duplicate_card():
    block = _FakeBlock([_FakeRow("Карта"), _FakeRow("Карта"), _FakeRow("Аккаунт")])
    row = _row(card="9990110810347534")
    fill_aggregate_modal_fields(block, row)
    assert block.rows[0]._input.values == []
    assert block.rows[1]._input.values[-1] == row.card


def test_fill_aggregate_modal_skips_missing_optional_field():
    block = _FakeBlock([_FakeRow("Карта"), _FakeRow("Телефон")])
    fill_aggregate_modal_fields(block, _row(account="unused"))


def test_fill_aggregate_field_if_present_required_missing_value():
    block = _FakeBlock([_FakeRow("Карта")])
    with pytest.raises(RuntimeError, match="требует значение"):
        fill_aggregate_field_if_present(block, ("Карта",), "", required=True)


def test_detect_aggregate_expansion_by_unique_label():
    block = _FakeBlock([_FakeRow("Аккаунт")])
    assert _detect_aggregate_expansion(block) == "Аккаунт"


def test_detect_aggregate_expansion_by_second_card():
    block = _FakeBlock([_FakeRow("Карта"), _FakeRow("Карта")])
    assert _detect_aggregate_expansion(block) == "Карта"


def test_detect_aggregate_expansion_none():
    block = _FakeBlock([_FakeRow("Карта")])
    assert _detect_aggregate_expansion(block) is None


@patch("automation.add_wallet_engine.fill_aggregate_modal_fields")
@patch("automation.add_wallet_engine.wait_for_aggregate_fields_visible")
@patch("automation.add_wallet_engine.select_single_aggregate_checkbox")
@patch("automation.add_wallet_engine._fill_multiselect_list")
@patch("automation.add_wallet_engine._select_by_label")
@patch("automation.add_wallet_engine._fill_text_by_label")
def test_fill_add_wallet_form_selects_single_aggregate(
    mock_text,
    mock_select,
    mock_multi,
    mock_select_agg,
    mock_wait_fields,
    mock_fill_modal,
):
    page = MagicMock()
    modal = MagicMock()
    page.locator.return_value = modal
    row = _row(aggregate="ЧБР")

    fill_add_wallet_form(page, row)

    mock_select_agg.assert_called_once_with(page, "ЧБР")
    mock_wait_fields.assert_called_once_with(page, "ЧБР")
    mock_fill_modal.assert_called_once_with(modal, row)


@patch("automation.add_wallet_engine.fill_aggregate_modal_fields")
@patch("automation.add_wallet_engine.wait_for_aggregate_fields_visible")
@patch("automation.add_wallet_engine.select_single_aggregate_checkbox")
@patch("automation.add_wallet_engine._fill_multiselect_list")
@patch("automation.add_wallet_engine._select_by_label")
@patch("automation.add_wallet_engine._fill_text_by_label")
def test_fill_add_wallet_form_tinkoff_aggregate(
    mock_text,
    mock_select,
    mock_multi,
    mock_select_agg,
    mock_wait_fields,
    mock_fill_modal,
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
@patch("automation.add_wallet_engine._detect_aggregate_expansion", return_value=None)
def test_wait_for_aggregate_fields_not_visible_raises(mock_detect, mock_mono):
    page = MagicMock()
    modal = MagicMock()
    page.locator.return_value = modal
    page.wait_for_timeout = MagicMock()

    with pytest.raises(RuntimeError, match="aggregate fields not visible"):
        wait_for_aggregate_fields_visible(page, "ЧБР")


@patch("automation.add_wallet_engine.time.monotonic", side_effect=[0, 0])
@patch("automation.add_wallet_engine._detect_aggregate_expansion", return_value="Аккаунт")
def test_wait_for_aggregate_fields_visible_success(mock_detect, mock_mono):
    page = MagicMock()
    wait_for_aggregate_fields_visible(page, "ЧБР")


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
@patch("automation.add_wallet_engine.fill_add_wallet_form", side_effect=RuntimeError("aggregate fields not visible: ЧБР"))
@patch("automation.add_wallet_engine.open_add_wallet_modal")
@patch("automation.add_wallet_engine._assert_create_modal")
@patch("automation.add_wallet_engine.card_exists_strict", return_value=False)
def test_missing_aggregate_fields_fail_fill(
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
    assert "aggregate fields not visible" in result.comment


def test_normalize_gender_option_values():
    assert normalize_gender_option("M") == "М"
    assert normalize_gender_option("female") == "Ж"
    assert normalize_gender_option("unknown") is None


def test_is_kyc_true_values():
    assert is_kyc_true("1") is True
    assert is_kyc_true("да") is True
    assert is_kyc_true("false") is False
    assert is_kyc_true("") is False


@patch("automation.add_wallet_engine._fill_optional_text_by_label")
@patch("automation.add_wallet_engine._fill_single_aggregate")
@patch("automation.add_wallet_engine._fill_multiselect_list")
@patch("automation.add_wallet_engine._select_by_label")
@patch("automation.add_wallet_engine._fill_text_by_label")
def test_fill_add_wallet_form_calls_phase2(
    mock_text,
    mock_select,
    mock_multi,
    mock_aggregate,
    mock_phase2_text,
):
    page = MagicMock()
    row = _row(surname="Petrov", comment="note")
    with patch("automation.add_wallet_engine._fill_phase2_top_level_fields") as mock_phase2:
        fill_add_wallet_form(page, row)
    mock_phase2.assert_called_once_with(page, row)


def test_fill_optional_text_skips_empty_and_missing():
    page = MagicMock()
    with patch("automation.add_wallet_engine._find_text_input_by_label", return_value=None):
        _fill_optional_text_by_label(page, "Фамилия", "Ivanov")
    field = MagicMock()
    with patch("automation.add_wallet_engine._find_text_input_by_label", return_value=field):
        _fill_optional_text_by_label(page, "Фамилия", "")
        field.fill.assert_not_called()
        _fill_optional_text_by_label(page, "Фамилия", "Ivanov")
        field.fill.assert_called()


def test_fill_optional_textarea_fills():
    page = MagicMock()
    field = MagicMock()
    with patch("automation.add_wallet_engine._find_textarea_by_label", return_value=field):
        _fill_optional_textarea_by_label(page, "Комментарий", "hello")
    field.fill.assert_called()


def test_fill_optional_select_fills():
    page = MagicMock()
    select = MagicMock()
    with patch("automation.add_wallet_engine._find_select_by_label", return_value=select):
        _fill_optional_select_by_label(page, "Шлюз", "GW1")
    select.select_option.assert_called()


def test_fill_gender_radio_clicks_option():
    page = MagicMock()
    modal = MagicMock()
    page.locator.return_value = modal
    row_el = MagicMock()
    row_el.is_visible.return_value = True
    pol_label = MagicMock()
    pol_label.inner_text.return_value = "Пол"
    m_label = MagicMock()
    m_label.inner_text.return_value = "М"
    labels = MagicMock()
    labels.count.side_effect = [1, 2]
    labels.first = pol_label
    labels.nth.side_effect = lambda i: m_label if i == 1 else pol_label
    row_el.locator.return_value = labels
    rows = MagicMock()
    rows.count.return_value = 1
    rows.nth.return_value = row_el
    modal.locator.return_value = rows

    _fill_gender_radio(page, "male")
    m_label.click.assert_called_once()


def test_fill_kyc_checkbox_checks_when_true():
    page = MagicMock()
    modal = MagicMock()
    page.locator.return_value = modal
    checkbox = MagicMock()
    checkbox.is_checked.side_effect = [False, True]
    with patch("automation.add_wallet_engine._find_kyc_checkbox", return_value=checkbox):
        _fill_kyc_checkbox(page, "yes")
    checkbox.check.assert_called_once_with(force=True)


def test_fill_kyc_checkbox_da_checks_kus_label():
    page = MagicMock()
    modal = MagicMock()
    page.locator.return_value = modal
    checkbox = MagicMock()
    checkbox.is_checked.side_effect = [False, True]
    with patch("automation.add_wallet_engine._find_kyc_checkbox", return_value=checkbox) as mock_find:
        _fill_kyc_checkbox(page, "да")
    mock_find.assert_called_once_with(modal)
    checkbox.check.assert_called_once_with(force=True)


def test_fill_kyc_checkbox_skips_when_already_checked():
    page = MagicMock()
    modal = MagicMock()
    page.locator.return_value = modal
    checkbox = MagicMock()
    checkbox.is_checked.return_value = True
    with patch("automation.add_wallet_engine._find_kyc_checkbox", return_value=checkbox):
        _fill_kyc_checkbox(page, "да")
    checkbox.check.assert_not_called()


def test_fill_kyc_checkbox_skips_false():
    page = MagicMock()
    with patch("automation.add_wallet_engine._find_kyc_checkbox") as mock_find:
        _fill_kyc_checkbox(page, "no")
    mock_find.assert_not_called()


def test_find_kyc_checkbox_matches_kus_label_in_lower_form():
    modal, _ = _modal_with_label_rows(
        [
            ["ЧБР"],
            ["Приоритет для выплат"],
            ["КУС"],
        ]
    )
    kus_checkbox = MagicMock()
    with patch("automation.add_wallet_engine._kyc_checkbox_for_label", return_value=kus_checkbox):
        found = _find_kyc_checkbox(modal)
    assert found is kus_checkbox


def test_find_kyc_checkbox_matches_kyc_label():
    modal, _ = _modal_with_label_rows(
        [
            ["ЧБР"],
            ["Длина очереди"],
            ["KYC"],
        ]
    )
    kyc_checkbox = MagicMock()
    with patch("automation.add_wallet_engine._kyc_checkbox_for_label", return_value=kyc_checkbox):
        found = _find_kyc_checkbox(modal)
    assert found is kyc_checkbox


def test_find_kyc_checkbox_ignores_chbr_when_no_kyc_label():
    modal, _ = _modal_with_label_rows(
        [
            ["ЧБР"],
            ["Привязан к партнеру"],
        ]
    )
    assert _find_kyc_checkbox(modal) is None


def test_find_kyc_checkbox_clicks_only_kyc_when_both_present():
    modal, row_mocks = _modal_with_label_rows(
        [
            ["ЧБР"],
            ["Приоритет для выплат"],
            ["KYC"],
        ]
    )
    chbr_label = row_mocks[0].locator.return_value.nth(0)
    kyc_label = row_mocks[2].locator.return_value.nth(0)
    chbr_checkbox = MagicMock(name="chbr_checkbox")
    kyc_checkbox = MagicMock(name="kyc_checkbox")

    def resolve_checkbox(label_el):
        if label_el is kyc_label:
            return kyc_checkbox
        if label_el is chbr_label:
            return chbr_checkbox
        return None

    with patch("automation.add_wallet_engine._kyc_checkbox_for_label", side_effect=resolve_checkbox):
        found = _find_kyc_checkbox(modal)
    assert found is kyc_checkbox


def test_find_kyc_search_start_after_partner_section():
    modal, _ = _modal_with_label_rows(
        [
            ["ЧБР"],
            ["Привязан к партнеру"],
            ["KYC"],
        ]
    )
    assert _find_kyc_search_start_row_index(modal) == 2


def test_kyc_checkbox_for_label_does_not_use_document_preceding():
    label_el = MagicMock()
    empty = MagicMock()
    empty.count.return_value = 0
    parent = MagicMock()
    parent.locator.return_value = empty

    def locator_side_effect(selector):
        if selector == "xpath=..":
            return parent
        return empty

    label_el.locator.side_effect = locator_side_effect
    assert _kyc_checkbox_for_label(label_el) is None
    xpath_calls = [call.args[0] for call in label_el.locator.call_args_list if call.args]
    assert not any("preceding::input" in arg for arg in xpath_calls)


@patch("automation.add_wallet_engine._log")
def test_fill_kyc_checkbox_logs_not_found_without_kyc_label(mock_log):
    page = MagicMock()
    modal, _ = _modal_with_label_rows([["ЧБР"], ["Привязан к партнеру"]])
    page.locator.return_value = modal
    chbr_checkbox = MagicMock()
    with patch("automation.add_wallet_engine.select_single_aggregate_checkbox") as mock_agg:
        _fill_kyc_checkbox(page, "да")
    mock_agg.assert_not_called()
    mock_log.assert_any_call("kyc_not_found", extra="kyc_value='да' kyc_control_found=false")


@patch("automation.add_wallet_engine._fill_phase2_top_level_fields")
@patch("automation.add_wallet_engine.select_single_aggregate_checkbox")
@patch("automation.add_wallet_engine._fill_multiselect_list")
@patch("automation.add_wallet_engine._find_select_by_label")
@patch("automation.add_wallet_engine._find_text_input_by_label")
def test_fill_add_wallet_form_kyc_only_does_not_select_aggregate(
    mock_find_text,
    mock_find_select,
    mock_multi,
    mock_select_agg,
    mock_phase2,
):
    page = MagicMock()
    field = MagicMock()
    mock_find_text.return_value = field
    row = _row(kyc="да")

    fill_add_wallet_form(page, row)

    mock_select_agg.assert_not_called()


@patch("automation.add_wallet_engine._fill_phase2_top_level_fields")
@patch("automation.add_wallet_engine.select_single_aggregate_checkbox")
@patch("automation.add_wallet_engine._fill_multiselect_list")
@patch("automation.add_wallet_engine._find_select_by_label")
@patch("automation.add_wallet_engine._find_text_input_by_label")
def test_fill_add_wallet_form_omitted_fields_not_filled(
    mock_find_text,
    mock_find_select,
    mock_multi,
    mock_select_agg,
    mock_phase2,
):
    page = MagicMock()
    field = MagicMock()
    mock_find_text.return_value = field
    row = _row()

    fill_add_wallet_form(page, row)

    select_labels = [call.args[1] for call in mock_find_select.call_args_list]
    assert select_labels == ["Статус"]
    mock_select_agg.assert_not_called()


@patch("automation.add_wallet_engine._fill_phase2_top_level_fields")
@patch("automation.add_wallet_engine.fill_aggregate_modal_fields")
@patch("automation.add_wallet_engine.wait_for_aggregate_fields_visible")
@patch("automation.add_wallet_engine.select_single_aggregate_checkbox")
@patch("automation.add_wallet_engine._fill_multiselect_list")
@patch("automation.add_wallet_engine._find_select_by_label")
@patch("automation.add_wallet_engine._find_text_input_by_label")
def test_fill_add_wallet_form_explicit_fields_still_filled(
    mock_find_text,
    mock_find_select,
    mock_multi,
    mock_select_agg,
    mock_wait_fields,
    mock_fill_modal,
    mock_phase2,
):
    page = MagicMock()
    modal = MagicMock()
    page.locator.return_value = modal
    field = MagicMock()
    select = MagicMock()
    mock_find_text.return_value = field
    mock_find_select.return_value = select
    row = _row(direction="in", state="enabled", pool="ЧБР", aggregate="ЧБР")

    fill_add_wallet_form(page, row)

    select_labels = [call.args[1] for call in mock_find_select.call_args_list]
    assert select_labels == ["Направление", "Статус", "Состояние", "Пул"]
    mock_select_agg.assert_called_once_with(page, "ЧБР")


@patch("automation.add_wallet_engine._fill_kyc_checkbox")
@patch("automation.add_wallet_engine._fill_gender_radio")
@patch("automation.add_wallet_engine._fill_optional_select_by_label")
@patch("automation.add_wallet_engine._fill_optional_textarea_by_label")
@patch("automation.add_wallet_engine._fill_optional_text_by_label")
def test_fill_phase2_top_level_fields(
    mock_text,
    mock_textarea,
    mock_select,
    mock_gender,
    mock_kyc,
):
    page = MagicMock()
    row = _row(
        surname="Ivanov",
        comment="c1",
        gateway="GW",
        gender="M",
        kyc="1",
    )
    _fill_phase2_top_level_fields(page, row)
    mock_text.assert_any_call(page, "Фамилия", "Ivanov")
    mock_textarea.assert_any_call(page, "Комментарий", "c1")
    mock_select.assert_any_call(page, "Шлюз", "GW")
    mock_gender.assert_called_once_with(page, "M")
    mock_kyc.assert_called_once_with(page, "1")


def test_cluster_uses_select_strategy():
    select_attrs = {attr for attr, _ in PHASE2_OPTIONAL_SELECT_FIELDS}
    text_attrs = {attr for attr, _ in PHASE2_OPTIONAL_TEXT_FIELDS}
    assert "cluster" in select_attrs
    assert "cluster" not in text_attrs
    assert ADD_WALLET_LOWER_FORM_CONTROL_TYPES["cluster"] == ("Кластер", "select")


def test_lower_form_control_types_match_fill_strategies():
    select_attrs = {attr for attr, _ in PHASE2_OPTIONAL_SELECT_FIELDS}
    text_attrs = {attr for attr, _ in PHASE2_OPTIONAL_TEXT_FIELDS}
    for column, (label, control_type) in ADD_WALLET_LOWER_FORM_CONTROL_TYPES.items():
        if control_type == "select":
            assert column in select_attrs or column == "pool"
        elif control_type == "text":
            assert column in text_attrs
        assert label
