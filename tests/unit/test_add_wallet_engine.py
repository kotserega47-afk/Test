from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from automation.add_wallet_contract import (
    AddWalletRow,
    RESULT_DRY_RUN,
    RESULT_FAIL_NOT_FOUND,
    RESULT_FAIL_SAVE_TIMEOUT,
    RESULT_FAIL_VALIDATION,
    RESULT_OK,
    RESULT_SKIP_DUP_CARD,
)
from automation.add_wallet_engine import (
    SaveWaitOutcome,
    _process_row,
    card_exists_strict,
    detect_add_wallet_validation_error,
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
