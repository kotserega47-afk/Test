from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from automation.add_wallet_engine import (
    ADD_WALLET_LOWER_FORM_CONTROL_TYPES,
    FieldNotEditableError,
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
    _fill_single_aggregate,
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
from automation.add_wallet_contract import (
    AddWalletRow,
    RESULT_DRY_RUN,
    RESULT_FAIL_FILL,
    RESULT_FAIL_NOT_FOUND,
    RESULT_FAIL_SAVE_TIMEOUT,
    RESULT_FAIL_TECHNICAL,
    RESULT_FAIL_VALIDATION,
    RESULT_OK,
    RESULT_SKIP_DUP_CARD,
    RESULT_STOP_BEFORE_SAVE,
)
from automation.audit import row_matches_card_strict
from automation.engine import CardSearchUnsettledError
from automation.runtime import RunConfig
from automation.wallet_form_helpers import (
    AggregateSwitchError,
    requested_nested_fields_for_add_row,
)


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


def test_card_exists_strict_uses_engine_search():
    page = MagicMock()
    with patch(
        "automation.add_wallet_engine.engine_card_exists_strict", return_value=True
    ) as engine_search:
        assert card_exists_strict(page, "9990110810347534") is True
    engine_search.assert_called_once_with(page, "9990110810347534")


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
@patch("automation.add_wallet_engine.card_exists_strict", side_effect=[False])
def test_stop_before_save_skips_save(
    mock_exists,
    mock_assert,
    mock_open,
    mock_fill,
    mock_save,
):
    page = MagicMock()
    cfg = RunConfig(login="u", password="p", dry_run=False, stop_before_save=True)
    result = _process_row(page, _row(), cfg=cfg, operator_profile="DENIS")
    assert result.result == RESULT_STOP_BEFORE_SAVE
    mock_fill.assert_called_once()
    mock_save.assert_not_called()


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
    block = _FakeBlock(
        [_FakeRow("Карта"), _FakeRow("Карта"), _FakeRow("Телефон"), _FakeRow("Аккаунт")]
    )
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


@patch("automation.add_wallet_engine._fill_confirmed_nested_field")
@patch(
    "automation.add_wallet_engine.wait_for_requested_nested_fields",
    return_value={"phone": MagicMock(), "card": MagicMock()},
)
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
    mock_fill_nested,
):
    page = MagicMock()
    row = _row(aggregate="ЧБР")

    fill_add_wallet_form(page, row)

    mock_select_agg.assert_called_once_with(page, "ЧБР", card=row.card)
    mock_wait_fields.assert_called_once()
    assert mock_wait_fields.call_args.kwargs["needed"]["phone"] == row.phone
    assert "card" in mock_wait_fields.call_args.kwargs["needed"]


@patch("automation.add_wallet_engine._fill_confirmed_nested_field")
@patch(
    "automation.add_wallet_engine.wait_for_requested_nested_fields",
    return_value={"phone": MagicMock(), "card": MagicMock()},
)
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
    mock_fill_nested,
):
    page = MagicMock()
    row = _row(aggregate="Тинькофф АПК")
    fill_add_wallet_form(page, row)
    mock_select_agg.assert_called_once_with(page, "Тинькофф АПК", card=row.card)


def test_select_single_aggregate_checkbox_delegates_to_switch():
    page = MagicMock()
    with patch("automation.add_wallet_engine.switch_to_single_aggregate") as switch:
        select_single_aggregate_checkbox(page, "ЧБР", card="9990110810347534")
    switch.assert_called_once_with(
        page, "ЧБР", card="9990110810347534", timing_scope="add_wallet"
    )


def test_select_single_aggregate_unknown_raises():
    page = MagicMock()
    with patch(
        "automation.add_wallet_engine.switch_to_single_aggregate",
        side_effect=AggregateSwitchError("aggregate checkbox not found: UnknownAgg"),
    ):
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
    field.evaluate.return_value = True
    with patch("automation.add_wallet_engine._find_text_input_by_label", return_value=field):
        _fill_optional_text_by_label(page, "Фамилия", "")
        field.fill.assert_not_called()
        field.evaluate.assert_not_called()
        _fill_optional_text_by_label(page, "Фамилия", "Ivanov")
        assert field.fill.call_count == 2


def test_fill_optional_text_readonly_raises_without_fill():
    page = MagicMock()
    field = MagicMock()
    field.evaluate.return_value = False
    with patch("automation.add_wallet_engine._find_text_input_by_label", return_value=field):
        with pytest.raises(FieldNotEditableError, match="field is readonly/disabled: Фамилия"):
            _fill_optional_text_by_label(page, "Фамилия", "Ivanov")
    field.fill.assert_not_called()


def test_fill_optional_text_disabled_raises_without_fill():
    page = MagicMock()
    field = MagicMock()
    field.evaluate.side_effect = RuntimeError("no js")
    field.get_attribute.side_effect = lambda name: "disabled" if name == "disabled" else None
    with patch("automation.add_wallet_engine._find_text_input_by_label", return_value=field):
        with pytest.raises(FieldNotEditableError, match="field is readonly/disabled: Баланс"):
            _fill_optional_text_by_label(page, "Баланс", "100")
    field.fill.assert_not_called()


def test_fill_optional_text_editable_fills():
    page = MagicMock()
    field = MagicMock()
    field.evaluate.return_value = True
    with patch("automation.add_wallet_engine._find_text_input_by_label", return_value=field):
        _fill_optional_text_by_label(page, "Фамилия", "Ivanov")
    assert field.fill.call_count == 2


def test_fill_optional_textarea_readonly_raises():
    page = MagicMock()
    field = MagicMock()
    field.evaluate.return_value = False
    with patch("automation.add_wallet_engine._find_textarea_by_label", return_value=field):
        with pytest.raises(FieldNotEditableError):
            _fill_optional_textarea_by_label(page, "Комментарий", "hello")
    field.fill.assert_not_called()


def test_fill_optional_textarea_fills():
    page = MagicMock()
    field = MagicMock()
    field.evaluate.return_value = True
    with patch("automation.add_wallet_engine._find_textarea_by_label", return_value=field):
        _fill_optional_textarea_by_label(page, "Комментарий", "hello")
    assert field.fill.call_count == 2


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
@patch("automation.add_wallet_engine._fill_confirmed_nested_field")
@patch(
    "automation.add_wallet_engine.wait_for_requested_nested_fields",
    return_value={"phone": MagicMock(), "card": MagicMock()},
)
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
    mock_fill_nested,
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
    mock_select_agg.assert_called_once_with(page, "ЧБР", card=row.card)


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


def _mock_multiselect_page():
    page = MagicMock()
    multiselect = MagicMock()
    return page, multiselect


@patch("automation.add_wallet_engine._multiselect_add_option_for_set")
@patch("automation.add_wallet_engine._multiselect_remove_label")
@patch("automation.add_wallet_engine._get_selected_multiselect_labels")
@patch("automation.add_wallet_engine._find_multiselect_by_label")
def test_set_multiselect_removes_extra_values(
    mock_find,
    mock_labels,
    mock_remove,
    mock_add,
):
    page, multiselect = _mock_multiselect_page()
    mock_find.return_value = multiselect
    mock_labels.side_effect = [
        ["Partner A", "Partner B", "Partner C"],
        ["Partner A", "Partner B", "Partner C"],
        ["Partner A", "Partner C"],
        ["Partner A", "Partner C"],
        ["Partner A", "Partner C"],
        ["Partner A", "Partner C"],
    ]
    mock_remove.return_value = True

    from automation.add_wallet_engine import _set_multiselect_list

    _set_multiselect_list(page, "Привязан к партнеру", "Partner A;Partner C")

    mock_remove.assert_called_once_with(multiselect, "Partner B")
    mock_add.assert_not_called()


@patch("automation.add_wallet_engine._multiselect_add_option_for_set")
@patch("automation.add_wallet_engine._multiselect_remove_label")
@patch("automation.add_wallet_engine._get_selected_multiselect_labels")
@patch("automation.add_wallet_engine._find_multiselect_by_label")
def test_set_multiselect_adds_missing_values_only(
    mock_find,
    mock_labels,
    mock_remove,
    mock_add,
):
    page, multiselect = _mock_multiselect_page()
    mock_find.return_value = multiselect
    mock_labels.side_effect = [
        ["Partner A"],
        ["Partner A"],
        ["Partner A"],
        ["Partner A"],
        ["Partner A", "Partner C"],
    ]
    mock_remove.return_value = True

    from automation.add_wallet_engine import _set_multiselect_list

    _set_multiselect_list(page, "Привязан к партнеру", "Partner A;Partner C")

    mock_remove.assert_not_called()
    mock_add.assert_called_once_with(
        page,
        multiselect,
        "Partner C",
        field_label="Привязан к партнеру",
    )


@patch("automation.add_wallet_engine._multiselect_add_option_for_set")
@patch("automation.add_wallet_engine._multiselect_remove_label")
@patch("automation.add_wallet_engine._get_selected_multiselect_labels")
@patch("automation.add_wallet_engine._find_multiselect_by_label")
def test_set_multiselect_no_ops_when_already_exact(
    mock_find,
    mock_labels,
    mock_remove,
    mock_add,
):
    page, multiselect = _mock_multiselect_page()
    mock_find.return_value = multiselect
    mock_labels.return_value = ["Partner A", "Partner C"]

    from automation.add_wallet_engine import _set_multiselect_list

    _set_multiselect_list(page, "Привязан к партнеру", "Partner A;Partner C")

    mock_remove.assert_not_called()
    mock_add.assert_not_called()


@patch("automation.add_wallet_engine._find_multiselect_by_label")
def test_set_multiselect_empty_values_no_touch(mock_find):
    page = MagicMock()
    from automation.add_wallet_engine import _set_multiselect_list

    _set_multiselect_list(page, "Привязан к партнеру", "")

    mock_find.assert_not_called()


@patch("automation.add_wallet_engine._multiselect_add_option_for_set")
@patch("automation.add_wallet_engine._multiselect_remove_label")
@patch("automation.add_wallet_engine._get_selected_multiselect_labels")
@patch("automation.add_wallet_engine._find_multiselect_by_label")
def test_set_multiselect_unknown_option_raises(mock_find, mock_labels, mock_remove, mock_add):
    page, multiselect = _mock_multiselect_page()
    mock_find.return_value = multiselect
    mock_labels.side_effect = [[], [], []]
    mock_remove.return_value = True
    mock_add.side_effect = RuntimeError("multiselect option not found: Группа=Missing")

    from automation.add_wallet_engine import _set_multiselect_list

    with pytest.raises(RuntimeError, match="multiselect option not found: Группа=Missing"):
        _set_multiselect_list(page, "Группа", "Missing")


@patch("automation.add_wallet_engine._multiselect_add_option_for_set")
@patch("automation.add_wallet_engine._multiselect_remove_label")
@patch("automation.add_wallet_engine._get_selected_multiselect_labels")
@patch("automation.add_wallet_engine._find_multiselect_by_label")
def test_set_multiselect_remove_failure_raises(mock_find, mock_labels, mock_remove, mock_add):
    page, multiselect = _mock_multiselect_page()
    mock_find.return_value = multiselect
    mock_labels.side_effect = [
        ["Group G1", "Group G2"],
        ["Group G1", "Group G2"],
    ]
    mock_remove.return_value = False

    from automation.add_wallet_engine import _set_multiselect_list

    with pytest.raises(RuntimeError, match="failed to remove multiselect option: Группа=Group G2"):
        _set_multiselect_list(page, "Группа", "Group G1")


@patch("automation.add_wallet_engine._multiselect_add_option_for_set")
@patch("automation.add_wallet_engine._multiselect_remove_label")
@patch("automation.add_wallet_engine._get_selected_multiselect_labels")
@patch("automation.add_wallet_engine._find_multiselect_by_label")
def test_set_multiselect_idempotent_on_second_call(
    mock_find,
    mock_labels,
    mock_remove,
    mock_add,
):
    page, multiselect = _mock_multiselect_page()
    mock_find.return_value = multiselect
    mock_labels.side_effect = [
        ["Partner A", "Partner B"],
        ["Partner A", "Partner B"],
        ["Partner A"],
        ["Partner A"],
        ["Partner A"],
        ["Partner A"],
    ]
    mock_remove.return_value = True

    from automation.add_wallet_engine import _set_multiselect_list

    _set_multiselect_list(page, "Привязан к партнеру", "Partner A")
    _set_multiselect_list(page, "Привязан к партнеру", "Partner A")

    mock_remove.assert_called_once_with(multiselect, "Partner B")
    mock_add.assert_not_called()


def test_sim_a_requested_fields_are_nested_phone_only():
    row = _row(aggregate="Sim A (1)", phone="79491103311", account="")
    needed = requested_nested_fields_for_add_row(row)
    assert set(needed) == {"phone"}
    assert needed["phone"] == "79491103311"


def test_nested_phone_not_confused_with_top_level():
    from automation.wallet_form_helpers import _pick_nested_phone_item

    phones = [
        {"label": "Телефон", "id": "top", "value": "79491103311", "visible": True},
        {"label": "Телефон", "id": "nested", "value": "", "visible": True},
    ]
    nested = _pick_nested_phone_item(phones, expected_top_phone="79491103311")
    assert nested is not None
    assert nested["id"] == "nested"


def test_wait_sim_a_does_not_require_device_or_nested_card():
    from automation.wallet_form_helpers import wait_for_requested_nested_fields

    page = MagicMock()
    phone_loc = MagicMock(name="nested_phone")
    polls = {"n": 0}

    def labeled(_page=None):
        polls["n"] += 1
        rows = [
            {
                "label": "Телефон",
                "id": "top",
                "value": "79491103311",
                "visible": True,
                "disabled": False,
                "readOnly": False,
                "rowIndex": 0,
            },
            {
                "label": "Девайс",
                "id": "device",
                "value": "",
                "visible": True,
                "disabled": False,
                "readOnly": False,
                "rowIndex": 3,
            },
        ]
        if polls["n"] >= 2:
            rows.append(
                {
                    "label": "Телефон",
                    "id": "nested",
                    "value": "",
                    "visible": True,
                    "disabled": False,
                    "readOnly": False,
                    "rowIndex": 5,
                }
            )
        return rows

    with (
        patch(
            "automation.wallet_form_helpers._list_labeled_inputs",
            side_effect=labeled,
        ),
        patch(
            "automation.wallet_form_helpers._locator_for_labeled_input",
            return_value=phone_loc,
        ),
        patch("automation.wallet_form_helpers._NESTED_FIELD_POLL_MS", 1),
        patch("automation.wallet_form_helpers._NESTED_FIELD_WAIT_MS", 2000),
    ):
        fields = wait_for_requested_nested_fields(
            page,
            needed={"phone": "998900000000"},
            expected_top_phone="79491103311",
            aggregate="Sim A (1)",
            card="9990110810347534",
            timing_scope="add_wallet",
        )

    assert fields == {"phone": phone_loc}
    assert polls["n"] == 2


def test_aggregate_already_sole_active_skips_clicks():
    page = MagicMock()
    from automation.wallet_form_helpers import (
        AggregateCheckboxSnapshot,
        switch_to_single_aggregate,
    )

    snap = [
        AggregateCheckboxSnapshot(name="Sim A (1)", checked=True, id="a1"),
        AggregateCheckboxSnapshot(name="ЧБР", checked=False, id="a2"),
    ]
    with patch(
        "automation.wallet_form_helpers.snapshot_aggregate_checkboxes",
        return_value=snap,
    ) as snap_fn:
        switch_to_single_aggregate(page, "Sim A (1)")
    page.locator.assert_not_called()
    assert snap_fn.called


def test_multiple_active_aggregates_are_unchecked():
    page = MagicMock()
    page.wait_for_timeout = MagicMock()
    from automation.wallet_form_helpers import (
        AggregateCheckboxSnapshot,
        switch_to_single_aggregate,
    )

    state = {
        "A": True,
        "B": True,
        "ЧБР": False,
    }
    ids = {"A": "id-A", "B": "id-B", "ЧБР": "id-CBR"}
    locators = {name: MagicMock(name=f"cb-{name}") for name in state}

    def snap(_page=None, card=None, timing_scope="set_aggregate"):
        return [
            AggregateCheckboxSnapshot(name=n, checked=c, id=ids[n])
            for n, c in state.items()
        ]

    def locator_by_id(_page, checkbox_id):
        name = checkbox_id.split("id-", 1)[-1]
        if name == "CBR":
            name = "ЧБР"
        cb = locators[name]

        def uncheck(force=True, _name=name):
            state[_name] = False

        def check(force=True, _name=name):
            state[_name] = True

        def is_checked(_name=name):
            return state[_name]

        cb.uncheck.side_effect = uncheck
        cb.check.side_effect = check
        cb.is_checked.side_effect = is_checked
        return cb

    with (
        patch(
            "automation.wallet_form_helpers.snapshot_aggregate_checkboxes",
            side_effect=snap,
        ),
        patch(
            "automation.wallet_form_helpers._checkbox_locator_by_id",
            side_effect=locator_by_id,
        ),
        patch("automation.wallet_form_helpers._AGGREGATE_TOGGLE_POLL_MS", 1),
        patch("automation.wallet_form_helpers._AGGREGATE_TOGGLE_TIMEOUT_MS", 50),
    ):
        switch_to_single_aggregate(page, "ЧБР")

    assert state == {"A": False, "B": False, "ЧБР": True}
    assert locators["A"].uncheck.called
    assert locators["B"].uncheck.called


def test_dom_recreate_uses_fresh_locator_after_uncheck():
    page = MagicMock()
    page.wait_for_timeout = MagicMock()
    from automation.wallet_form_helpers import (
        switch_to_single_aggregate,
    )

    generations = {"Old": 1, "Sim A (1)": 1}
    state = {"Old": True, "Sim A (1)": False}

    def snap(_page=None, card=None, timing_scope="set_aggregate"):
        from automation.wallet_form_helpers import AggregateCheckboxSnapshot

        return [
            AggregateCheckboxSnapshot(
                name=n, checked=state[n], id=f"id-{n}-g{generations[n]}"
            )
            for n in state
        ]

    def locator_by_id(_page, checkbox_id):
        gen = int(checkbox_id.rsplit("g", 1)[-1])
        name = "Old" if checkbox_id.startswith("id-Old") else "Sim A (1)"
        cb = MagicMock(name=f"{name}-{gen}")

        def is_checked():
            if generations[name] != gen:
                raise Exception("stale checkbox locator")
            return state[name]

        def uncheck(force=True):
            if generations[name] != gen:
                raise Exception("stale checkbox locator")
            state[name] = False
            generations[name] += 1

        def check(force=True):
            if generations[name] != gen:
                raise Exception("stale checkbox locator")
            state[name] = True
            generations[name] += 1

        cb.is_checked.side_effect = is_checked
        cb.uncheck.side_effect = uncheck
        cb.check.side_effect = check
        return cb

    with (
        patch(
            "automation.wallet_form_helpers.snapshot_aggregate_checkboxes",
            side_effect=snap,
        ),
        patch(
            "automation.wallet_form_helpers._checkbox_locator_by_id",
            side_effect=locator_by_id,
        ),
        patch("automation.wallet_form_helpers._AGGREGATE_TOGGLE_POLL_MS", 1),
        patch("automation.wallet_form_helpers._AGGREGATE_TOGGLE_TIMEOUT_MS", 200),
    ):
        switch_to_single_aggregate(page, "Sim A (1)")

    assert state["Old"] is False
    assert state["Sim A (1)"] is True
    assert generations["Old"] > 1
    assert generations["Sim A (1)"] > 1


def test_required_input_missing_is_explicit_error():
    block = _FakeBlock([_FakeRow("Аккаунт")])
    with pytest.raises(RuntimeError, match="required nested field not found"):
        fill_aggregate_field_if_present(block, ("Карта",), "123", required=True)


def test_fill_mismatch_retries_once_then_errors():
    from automation.wallet_form_helpers import fill_locator_confirmed

    page = MagicMock()
    first = MagicMock(name="first")
    second = MagicMock(name="second")
    resolve = MagicMock(return_value=second)

    with (
        patch("automation.wallet_form_helpers.fill_locator_text"),
        patch(
            "automation.wallet_form_helpers.read_locator_value",
            return_value="wrong",
        ),
    ):
        with pytest.raises(RuntimeError, match="failed to set nested field"):
            fill_locator_confirmed(
                page,
                field=first,
                value="998901234567",
                label="Телефон",
                resolve_fresh=resolve,
                card="9990110810347534",
                timing_prefix="aggregate",
                timing_scope="add_wallet",
            )

    assert resolve.call_count == 2


def test_fill_uses_waited_locator_without_second_label_scan():
    page = MagicMock()
    phone_field = MagicMock(name="waited")
    row = _row(aggregate="Sim A (1)")
    research = MagicMock(side_effect=AssertionError("must not re-scan labels"))

    with (
        patch("automation.add_wallet_engine.select_single_aggregate_checkbox"),
        patch(
            "automation.add_wallet_engine.wait_for_requested_nested_fields",
            return_value={"phone": phone_field},
        ),
        patch(
            "automation.add_wallet_engine.opportunistic_nested_card_item",
            return_value=None,
        ),
        patch("automation.add_wallet_engine.fill_locator_confirmed") as fill_conf,
        patch(
            "automation.wallet_form_helpers._list_labeled_inputs",
            side_effect=research,
        ),
    ):
        _fill_single_aggregate(page, row)

    assert fill_conf.call_count == 1
    assert fill_conf.call_args.kwargs["field"] is phone_field
    research.assert_not_called()


def test_stale_search_rows_are_not_accepted_as_new_filter():
    page = MagicMock()
    card = "9990110810347534"
    stale = (100, tuple(f"OLD{i:014d}" for i in range(5)))
    with (
        patch("automation.engine._DELETE_SEARCH_TIMEOUT_MS", 40),
        patch("automation.engine._ROW_MATCH_POLL_MS", 5),
        patch("automation.engine.wallet_editor_row_match_timeout_ms", return_value=0),
        patch("automation.engine._close_stale_modal"),
        patch("automation.engine._fill_card_search_input", return_value=card) as fill,
        patch("automation.engine._press_card_search_enter") as enter,
        patch("automation.engine._table_row_fingerprint", return_value=stale),
        patch(
            "automation.engine._try_match_strict_row_index",
            return_value=(None, 100, "OLD"),
        ),
        patch("automation.engine._page_shows_empty_wallet_results", return_value=False),
    ):
        with pytest.raises(CardSearchUnsettledError):
            card_exists_strict(page, card)

    fill.assert_called_once()
    assert enter.call_count == 2


def test_confirmed_absent_is_not_unsettled():
    page = MagicMock()
    card = "9990110810347534"
    before = (100, ("OLD0000000000001",))
    empty = (0, tuple())
    fps = [before, empty, empty]
    matches = [(None, 100, "OLD"), (None, 0, ""), (None, 0, "")]
    with (
        patch("automation.engine._DELETE_SEARCH_TIMEOUT_MS", 800),
        patch("automation.engine._DELETE_SEARCH_STABLE_POLLS", 2),
        patch("automation.engine._ROW_MATCH_POLL_MS", 10),
        patch("automation.engine.wallet_editor_row_match_timeout_ms", return_value=0),
        patch("automation.engine._close_stale_modal"),
        patch("automation.engine._fill_card_search_input", return_value=card),
        patch("automation.engine._press_card_search_enter"),
        patch("automation.engine._table_row_fingerprint", side_effect=fps),
        patch("automation.engine._try_match_strict_row_index", side_effect=matches),
        patch("automation.engine._page_shows_empty_wallet_results", return_value=True),
    ):
        assert card_exists_strict(page, card) is False


def test_unsettled_search_is_technical_not_skip():
    page = MagicMock()
    cfg = RunConfig(login="u", password="p", dry_run=False)
    with patch(
        "automation.add_wallet_engine.card_exists_strict",
        side_effect=CardSearchUnsettledError("9990110810347534"),
    ):
        result = _process_row(page, _row(), cfg=cfg, operator_profile="DENIS")
    assert result.result == RESULT_FAIL_TECHNICAL
    assert "unsettled" in result.comment

