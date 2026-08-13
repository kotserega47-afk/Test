"""Unit tests for Wallet Editor action=delete."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from automation.audit import Stats
from automation.engine import (
    ALLOWED_ACTIONS,
    DELETE_BUTTON_TEXT,
    DELETE_CONFIRM_OK_TEXT,
    DELETE_CONFIRM_TEXT,
    RESULT_DRY_RUN_WOULD_DELETE,
    RESULT_FAIL_CONFIRM_DIALOG_NOT_FOUND,
    RESULT_FAIL_DELETE_BUTTON_NOT_FOUND,
    RESULT_FAIL_DELETE_CONFLICT,
    RESULT_FAIL_DELETE_TIMEOUT,
    RESULT_FAIL_OPEN_CARD,
    RESULT_FAIL_STILL_EXISTS,
    RESULT_OK_DELETED,
    RESULT_SKIP_NOT_FOUND,
    RESULT_STOP_BEFORE_DELETE,
    OpenCardStageError,
    _click_delete_confirm_ok,
    _find_delete_confirm_dialog,
    _find_wallet_delete_button,
    _prepare_df,
    _validate_delete_conflicts,
    ensure_wallet_deleted,
    open_matched_card_row,
    timing_outcome_from_result,
)
from automation.runtime import RunConfig
from automation.add_wallet_contract import ExcelRouting, detect_excel_routing
from integrations.wallet_editor_registry_lifecycle import (
    ACTION_DELETE,
    partner_from_row,
    result_row_dates,
)
from core.datetime_utils import now_msk


def _cfg(**kwargs) -> RunConfig:
    defaults = {
        "login": "u",
        "password": "p",
        "auth_state_path": "/tmp/we_auth.json",
        "dry_run": False,
        "stop_before_delete": False,
        "headless": True,
    }
    defaults.update(kwargs)
    return RunConfig(**defaults)


def test_delete_is_allowed_action():
    assert "delete" in ALLOWED_ACTIONS


def test_prepare_df_accepts_delete_without_value_column(tmp_path):
    path = tmp_path / "delete.xlsx"
    pd.DataFrame(
        {"card": ["9860246700001620"], "action": ["delete"]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "delete"
    assert df.iloc[0]["value"] == ""
    assert "value" in df.columns


def test_prepare_df_delete_case_and_whitespace(tmp_path):
    path = tmp_path / "delete_case.xlsx"
    pd.DataFrame(
        {
            "card": ["9860246700001620", "9860246700001621", "9860246700001622"],
            "action": ["DELETE", " Delete ", "DeLeTe"],
        }
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert list(df["action"]) == ["delete", "delete", "delete"]


def test_prepare_df_delete_ignores_optional_value(tmp_path):
    path = tmp_path / "delete_value.xlsx"
    pd.DataFrame(
        {
            "card": ["9860246700001620"],
            "action": ["delete"],
            "value": ["ignored"],
        }
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "delete"
    assert df.iloc[0]["value"] == "ignored"  # kept but unused by delete


def test_prepare_df_still_requires_value_for_legacy_compat_when_present(tmp_path):
    path = tmp_path / "legacy.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["remove_partner"],
            "value": ["Ostin"],
        }
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "remove_partner"
    assert df.iloc[0]["value"] == "Ostin"


def test_delete_conflict_with_other_action():
    df = pd.DataFrame(
        {
            "card": ["4111111111111111", "4111111111111111"],
            "action": ["delete", "remove_partner"],
            "value": ["", "Ostin"],
            "status": ["", ""],
            "comment": ["", ""],
        }
    )
    n = _validate_delete_conflicts(df)
    assert n == 2
    assert list(df["status"]) == [RESULT_FAIL_DELETE_CONFLICT, RESULT_FAIL_DELETE_CONFLICT]


def test_delete_conflict_duplicate_delete_rows():
    df = pd.DataFrame(
        {
            "card": ["4111111111111111", "4111111111111111"],
            "action": ["delete", "delete"],
            "value": ["", ""],
            "status": ["", ""],
            "comment": ["", ""],
        }
    )
    n = _validate_delete_conflicts(df)
    assert n == 2
    assert (df["status"] == RESULT_FAIL_DELETE_CONFLICT).all()


def test_delete_alone_no_conflict():
    df = pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["delete"],
            "value": [""],
            "status": [""],
            "comment": [""],
        }
    )
    assert _validate_delete_conflicts(df) == 0
    assert df.iloc[0]["status"] == ""


def test_routing_card_action_delete_is_disable(tmp_path):
    path = tmp_path / "any_name.xlsx"
    pd.DataFrame(
        {"card": ["9860246700001620"], "action": ["delete"]}
    ).to_excel(path, index=False)

    routing, err = detect_excel_routing(str(path), original_filename="batch.xlsx")
    assert routing == ExcelRouting.DISABLE
    assert err is None


def test_ensure_wallet_deleted_skip_not_found():
    page = MagicMock()
    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=None) as find,
        patch("automation.engine.open_matched_card_row") as open_row,
        patch("automation.engine._submit_card_filter") as submit,
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())
    assert result == RESULT_SKIP_NOT_FOUND
    find.assert_called_once()
    open_row.assert_not_called()
    submit.assert_not_called()  # search is inside find_*, which is mocked


def test_ensure_wallet_deleted_dry_run_one_search_no_open():
    page = MagicMock()
    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=0) as find,
        patch("automation.engine.open_matched_card_row") as open_row,
        patch("automation.engine._find_wallet_delete_button") as find_btn,
        patch("automation.engine._submit_card_filter") as submit,
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg(dry_run=True))
    assert result == RESULT_DRY_RUN_WOULD_DELETE
    find.assert_called_once()
    open_row.assert_not_called()
    find_btn.assert_not_called()
    submit.assert_not_called()


def test_pre_open_search_filled_exactly_once():
    """Before opening the form, card filter must be submitted exactly once."""
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()
    submit_calls = []

    def fake_find(page_arg, card):
        from automation.engine import _submit_card_filter

        _submit_card_filter(page_arg, card)
        return 0

    def counting_submit(page_arg, card):
        submit_calls.append(card)

    with (
        patch("automation.engine.find_strict_matching_row_index", side_effect=fake_find),
        patch("automation.engine._submit_card_filter", side_effect=counting_submit),
        patch("automation.engine.open_matched_card_row") as open_row,
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok"),
        patch("automation.engine.classify_after_delete_confirm", return_value=RESULT_OK_DELETED),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_OK_DELETED
    assert len(submit_calls) == 1
    open_row.assert_called_once_with(page, "9860246700001620", 0)


def test_successful_delete_fills_search_exactly_twice():
    """Success path: one pre-open search + one post-delete verification search."""
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()
    submit_calls: list[str] = []

    def counting_submit(page_arg, card):
        submit_calls.append(card)

    def find_with_submit(page_arg, card):
        counting_submit(page_arg, card)
        return 0

    def verify_gone(page_arg, card):
        counting_submit(page_arg, card)
        return False

    with (
        patch("automation.engine.find_strict_matching_row_index", side_effect=find_with_submit),
        patch("automation.engine.open_matched_card_row") as open_row,
        patch("automation.engine.open_card_strict") as legacy_open,
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok"),
        patch("automation.engine._wait_form_hidden_after_delete", return_value=True),
        patch("automation.engine._wait_confirm_dialog_gone"),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        patch("automation.engine.card_exists_strict", side_effect=verify_gone),
        patch("automation.engine._submit_card_filter", side_effect=counting_submit),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_OK_DELETED
    assert len(submit_calls) == 2
    assert submit_calls == ["9860246700001620", "9860246700001620"]
    open_row.assert_called_once_with(page, "9860246700001620", 0)
    legacy_open.assert_not_called()
    delete_btn.click.assert_called_once()


def test_found_card_opens_from_current_search_results():
    page = MagicMock()
    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=2) as find,
        patch("automation.engine.open_matched_card_row") as open_row,
        patch("automation.engine._find_wallet_delete_button", return_value=MagicMock()),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=MagicMock()),
        patch("automation.engine._click_delete_confirm_ok"),
        patch("automation.engine.classify_after_delete_confirm", return_value=RESULT_OK_DELETED),
        patch("automation.engine.open_card_strict") as legacy_open,
        patch("automation.engine._submit_card_filter") as submit,
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_OK_DELETED
    find.assert_called_once()
    open_row.assert_called_once_with(page, "9860246700001620", 2)
    legacy_open.assert_not_called()
    submit.assert_not_called()


def test_ensure_wallet_deleted_fail_open_card():
    page = MagicMock()
    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=0),
        patch(
            "automation.engine.open_matched_card_row",
            side_effect=OpenCardStageError("modal_container", "9860246700001620"),
        ),
        patch("automation.engine._close_stale_modal"),
        patch("automation.engine._submit_card_filter") as submit,
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())
    assert result == RESULT_FAIL_OPEN_CARD
    submit.assert_not_called()


def test_ensure_wallet_deleted_button_not_found():
    page = MagicMock()
    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=0),
        patch("automation.engine.open_matched_card_row"),
        patch("automation.engine._find_wallet_delete_button", return_value=None),
        patch("automation.engine._close_stale_modal"),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())
    assert result == RESULT_FAIL_DELETE_BUTTON_NOT_FOUND


def test_ensure_wallet_deleted_confirm_dialog_not_found():
    page = MagicMock()
    delete_btn = MagicMock()
    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=0),
        patch("automation.engine.open_matched_card_row"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=None),
        patch("automation.engine._close_stale_modal"),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())
    assert result == RESULT_FAIL_CONFIRM_DIALOG_NOT_FOUND
    delete_btn.click.assert_called_once()


def test_post_delete_runs_separate_verification_search():
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()
    verify = MagicMock(return_value=False)

    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=0),
        patch("automation.engine.open_matched_card_row"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok"),
        patch("automation.engine._wait_form_hidden_after_delete", return_value=True),
        patch("automation.engine._wait_confirm_dialog_gone"),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        patch("automation.engine.card_exists_strict", verify),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_OK_DELETED
    verify.assert_called_once_with(page, "9860246700001620")


def test_ensure_wallet_deleted_ok_when_form_closes_and_card_gone():
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()

    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=0),
        patch("automation.engine.open_matched_card_row"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok") as click_ok,
        patch("automation.engine._wait_form_hidden_after_delete", return_value=True),
        patch("automation.engine._wait_confirm_dialog_gone"),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        patch("automation.engine.card_exists_strict", return_value=False),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_OK_DELETED
    click_ok.assert_called_once_with(dialog)
    delete_btn.click.assert_called_once()


def test_form_timeout_recovery_card_gone_returns_ok_deleted():
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()
    recover = MagicMock()

    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=0),
        patch("automation.engine.open_matched_card_row"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok") as click_ok,
        patch("automation.engine._wait_form_hidden_after_delete", return_value=False),
        patch("automation.engine._find_delete_confirm_dialog", return_value=None),
        patch("automation.engine._wallet_form_visible", side_effect=[True, False, False]),
        patch("automation.engine._close_stale_modal", recover),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        patch("automation.engine.card_exists_strict", return_value=False),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_OK_DELETED
    recover.assert_called()
    click_ok.assert_called_once()  # OK only once — no retry on recovery


def test_form_timeout_recovery_card_still_exists():
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()

    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=0),
        patch("automation.engine.open_matched_card_row"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok") as click_ok,
        patch("automation.engine._wait_form_hidden_after_delete", return_value=False),
        patch("automation.engine._find_delete_confirm_dialog", return_value=None),
        patch("automation.engine._wallet_form_visible", return_value=False),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        patch("automation.engine.card_exists_strict", return_value=True),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_FAIL_STILL_EXISTS
    click_ok.assert_called_once()


def test_form_timeout_search_impossible_returns_delete_timeout():
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()

    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=0),
        patch("automation.engine.open_matched_card_row"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok") as click_ok,
        patch("automation.engine._wait_form_hidden_after_delete", return_value=False),
        patch("automation.engine._find_delete_confirm_dialog", return_value=None),
        patch("automation.engine._wallet_form_visible", return_value=True),
        patch("automation.engine._close_stale_modal", side_effect=Exception("stuck")),
        patch("automation.engine.ensure_wallet_search_ready", return_value=False),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_FAIL_DELETE_TIMEOUT
    click_ok.assert_called_once()


def test_recovery_does_not_click_ok_or_delete_again():
    page = MagicMock()
    from automation.engine import classify_after_delete_confirm

    click_ok = MagicMock()
    delete_btn = MagicMock()

    with (
        patch("automation.engine._wait_form_hidden_after_delete", return_value=False),
        patch("automation.engine._find_delete_confirm_dialog", side_effect=[MagicMock(), None, None]),
        patch("automation.engine._dismiss_confirm_dialog_without_ok") as dismiss,
        patch("automation.engine._wallet_form_visible", return_value=False),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        patch("automation.engine.card_exists_strict", return_value=False),
        patch("automation.engine._click_delete_confirm_ok", click_ok),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
    ):
        result = classify_after_delete_confirm(page, "9860246700001620")

    assert result == RESULT_OK_DELETED
    dismiss.assert_called_once()
    click_ok.assert_not_called()
    delete_btn.click.assert_not_called()


def test_timeout_on_first_card_does_not_block_second_delete():
    """After stuck first delete, second card still gets a clean ensure_wallet_deleted call."""
    results = []

    def fake_delete(page_arg, card, cfg):
        if card.endswith("1620"):
            results.append(("first", card))
            return RESULT_FAIL_DELETE_TIMEOUT
        results.append(("second", card))
        return RESULT_OK_DELETED

    calls_ready = []

    def fake_ready(page_arg, *, allow_goto=True):
        calls_ready.append(allow_goto)
        return True

    df = pd.DataFrame(
        {
            "card": ["9860246700001620", "9860246700009999"],
            "action": ["delete", "delete"],
            "value": ["", ""],
            "status": ["", ""],
            "comment": ["", ""],
        }
    )

    pw = MagicMock()
    browser = MagicMock()
    context = MagicMock()
    page_obj = MagicMock()
    pw.chromium.launch.return_value = browser
    browser.new_context.return_value = context
    context.new_page.return_value = page_obj

    with (
        patch("automation.engine._prepare_df", return_value=df.copy()),
        patch("automation.engine._ensure_result_date_columns"),
        patch("automation.engine._validate_set_direction_pre_playwright", return_value=0),
        patch("automation.engine._validate_delete_conflicts", return_value=0),
        patch("automation.engine.load_hold_pairs_snapshot", return_value=MagicMock()),
        patch("automation.engine._apply_add_partner_hold_precheck"),
        patch("automation.engine.require_wallet_editor_antares_credentials"),
        patch("automation.engine.wallet_editor_playwright_slow_mo_ms", return_value=0),
        patch("automation.engine.sync_playwright") as sp,
        patch("automation.engine._ensure_logged_in"),
        patch("automation.engine.ensure_wallet_deleted", side_effect=fake_delete),
        patch("automation.engine.ensure_wallet_search_ready", side_effect=fake_ready),
        patch("automation.engine._write_result", return_value="/tmp/out.xlsx"),
        patch("automation.engine.close_playwright_stack"),
        patch("os.path.exists", return_value=False),
    ):
        sp.return_value.__enter__.return_value = pw
        sp.return_value.__exit__.return_value = None

        from automation.engine import run

        _out, stats = run("/tmp/in.xlsx", _cfg())

    assert [r[0] for r in results] == ["first", "second"]
    assert len(calls_ready) >= 2  # cleanup after each delete card
    assert stats.ok == 1
    assert stats.fail == 1


def test_ensure_wallet_deleted_technical_on_unexpected_error():
    page = MagicMock()
    with (
        patch(
            "automation.engine.find_strict_matching_row_index",
            side_effect=RuntimeError("boom"),
        ),
    ):
        from automation.engine import RESULT_FAIL_TECHNICAL

        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())
    assert result == RESULT_FAIL_TECHNICAL


def test_stop_before_delete_opens_once_without_clicking_delete():
    page = MagicMock()
    delete_btn = MagicMock()
    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=0) as find,
        patch("automation.engine.open_matched_card_row") as open_row,
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._pause_stop_before_delete"),
        patch("automation.engine._submit_card_filter") as submit,
    ):
        result = ensure_wallet_deleted(
            page, "9860246700001620", _cfg(stop_before_delete=True)
        )
    assert result == RESULT_STOP_BEFORE_DELETE
    find.assert_called_once()
    open_row.assert_called_once_with(page, "9860246700001620", 0)
    delete_btn.click.assert_not_called()
    submit.assert_not_called()


def test_open_matched_card_row_does_not_resubmit_search():
    page = MagicMock()
    modal = MagicMock()
    rows = MagicMock()
    row = MagicMock()
    row.inner_text.return_value = "9860246700001620"
    rows.nth.return_value = row
    locator_calls: list[str] = []

    def locator(sel):
        locator_calls.append(sel)
        if sel == "#wallet-add-modal___BV_modal_body_":
            return modal
        if sel == "tr.pointer":
            return rows
        return MagicMock()

    page.locator.side_effect = locator

    with (
        patch("automation.engine._submit_card_filter") as submit,
        patch("automation.engine._wait_modal_container_visible"),
        patch("automation.engine._wait_modal_card_data_ready", return_value="9860246700001620"),
        patch("automation.engine._verify_modal_card_number"),
    ):
        open_matched_card_row(page, "9860246700001620", 0)

    submit.assert_not_called()
    row.click.assert_called_once()
    assert locator_calls.count("tr.pointer") == 1


def test_open_matched_card_row_retries_fresh_locator_without_refill():
    page = MagicMock()
    modal = MagicMock()
    rows_first = MagicMock()
    rows_second = MagicMock()
    row1 = MagicMock()
    row1.inner_text.return_value = "9860246700001620"
    row2 = MagicMock()
    row2.inner_text.return_value = "9860246700001620"
    rows_first.nth.return_value = row1
    rows_second.nth.return_value = row2
    row_locators = [rows_first, rows_second]

    def locator(sel):
        if sel == "#wallet-add-modal___BV_modal_body_":
            return modal
        if sel == "tr.pointer":
            return row_locators.pop(0)
        return MagicMock()

    page.locator.side_effect = locator
    wait_calls = {"n": 0}

    def wait_modal(*_a, **_k):
        wait_calls["n"] += 1
        if wait_calls["n"] == 1:
            raise OpenCardStageError("modal_container", "9860246700001620")

    with (
        patch("automation.engine._submit_card_filter") as submit,
        patch(
            "automation.engine._fill_card_search_input",
            side_effect=AssertionError("must not re-fill"),
        ),
        patch("automation.engine._wait_modal_container_visible", side_effect=wait_modal),
        patch(
            "automation.engine._wait_modal_card_data_ready",
            return_value="9860246700001620",
        ),
        patch("automation.engine._verify_modal_card_number"),
    ):
        open_matched_card_row(page, "9860246700001620", 0)

    submit.assert_not_called()
    row1.click.assert_called_once()
    row2.click.assert_called_once()
    assert wait_calls["n"] == 2
    assert row_locators == []  # both fresh locators consumed


def test_open_matched_card_row_both_clicks_fail():
    page = MagicMock()
    modal = MagicMock()
    rows = MagicMock()
    row = MagicMock()
    row.inner_text.return_value = "9860246700001620"
    rows.nth.return_value = row

    def locator(sel):
        if sel == "#wallet-add-modal___BV_modal_body_":
            return modal
        if sel == "tr.pointer":
            return rows
        return MagicMock()

    page.locator.side_effect = locator

    with (
        patch(
            "automation.engine._wait_modal_container_visible",
            side_effect=OpenCardStageError("modal_container", "9860246700001620"),
        ),
        patch("automation.engine._wait_modal_card_data_ready") as data_ready,
        pytest.raises(OpenCardStageError) as exc_info,
    ):
        open_matched_card_row(page, "9860246700001620", 0)

    assert exc_info.value.stage == "modal_container"
    assert row.click.call_count == 2
    data_ready.assert_not_called()


def test_timing_outcome_from_delete_results():
    assert timing_outcome_from_result(RESULT_OK_DELETED) == "ok"
    assert timing_outcome_from_result(RESULT_SKIP_NOT_FOUND) == "skip"
    assert timing_outcome_from_result(RESULT_DRY_RUN_WOULD_DELETE) == "skip"
    assert timing_outcome_from_result(RESULT_STOP_BEFORE_DELETE) == "skip"
    assert timing_outcome_from_result(RESULT_FAIL_DELETE_TIMEOUT) == "fail"
    assert timing_outcome_from_result(RESULT_FAIL_STILL_EXISTS) == "fail"
    assert timing_outcome_from_result(RESULT_FAIL_OPEN_CARD) == "fail"
    assert timing_outcome_from_result("removed") == "ok"
    assert timing_outcome_from_result("skip: not selected") == "skip"


def test_log_step_duration_uses_caller_outcome_for_fail_delete(caplog):
    import logging

    from automation.audit import log_step_duration

    caplog.set_level(logging.INFO)
    with log_step_duration(profile="WALTER", scope="disable", step="action:delete") as timing:
        timing.outcome = timing_outcome_from_result(RESULT_FAIL_DELETE_TIMEOUT)
    assert "step=action:delete" in caplog.text
    assert "outcome=fail" in caplog.text


def test_log_step_duration_ok_deleted_and_skip(caplog):
    import logging

    from automation.audit import log_step_duration

    caplog.set_level(logging.INFO)
    with log_step_duration(profile="WALTER", scope="disable", step="action:delete") as timing:
        timing.outcome = timing_outcome_from_result(RESULT_OK_DELETED)
    assert "outcome=ok" in caplog.text

    with log_step_duration(profile="WALTER", scope="disable", step="action:delete") as timing:
        timing.outcome = timing_outcome_from_result(RESULT_SKIP_NOT_FOUND)
    assert "outcome=skip" in caplog.text


def test_click_delete_confirm_ok_exact_match_only():
    ok = MagicMock()
    ok.is_visible.return_value = True
    ok.inner_text.return_value = DELETE_CONFIRM_OK_TEXT
    cancel = MagicMock()
    cancel.is_visible.return_value = True
    cancel.inner_text.return_value = "Отмена"
    almost = MagicMock()
    almost.is_visible.return_value = True
    almost.inner_text.return_value = "Okay"

    buttons = MagicMock()
    buttons.count.return_value = 3
    buttons.nth.side_effect = [cancel, almost, ok]

    dialog = MagicMock()
    dialog.locator.return_value = buttons

    _click_delete_confirm_ok(dialog)
    ok.click.assert_called_once()
    cancel.click.assert_not_called()
    almost.click.assert_not_called()


def test_find_delete_confirm_dialog_requires_confirm_text():
    other = MagicMock()
    other.is_visible.return_value = True
    other.inner_text.return_value = "Сохранить изменения?"

    target = MagicMock()
    target.is_visible.return_value = True
    target.inner_text.return_value = f"Внимание\n{DELETE_CONFIRM_TEXT}\n"

    dialogs = MagicMock()
    dialogs.count.return_value = 2
    dialogs.nth.side_effect = [other, target]

    page = MagicMock()
    page.locator.return_value = dialogs

    found = _find_delete_confirm_dialog(page)
    assert found is target


def test_find_wallet_delete_button_requires_danger_and_exact_text():
    danger = MagicMock()
    danger.is_visible.return_value = True
    danger.inner_text.return_value = DELETE_BUTTON_TEXT
    danger.get_attribute.return_value = "btn btn-danger"

    plain = MagicMock()
    plain.is_visible.return_value = True
    plain.inner_text.return_value = DELETE_BUTTON_TEXT
    plain.get_attribute.return_value = "btn btn-secondary"
    plain.evaluate.return_value = "rgb(100,100,100)|rgb(0,0,0)"

    buttons = MagicMock()
    buttons.count.return_value = 2
    buttons.nth.side_effect = [plain, danger]

    modal = MagicMock()
    modal.is_visible.return_value = True
    modal.locator.return_value = buttons

    page = MagicMock()
    page.locator.return_value = modal

    found = _find_wallet_delete_button(page)
    assert found is danger


def test_stats_inc_delete_result_codes():
    s = Stats()
    s.inc(RESULT_OK_DELETED)
    s.inc(RESULT_SKIP_NOT_FOUND)
    s.inc(RESULT_DRY_RUN_WOULD_DELETE)
    s.inc(RESULT_FAIL_STILL_EXISTS)
    s.inc("removed")
    assert s.ok == 2
    assert s.skip == 2
    assert s.fail == 1


def test_registry_delete_does_not_set_disable_date_or_partner():
    processed = now_msk()
    op, disable = result_row_dates(ACTION_DELETE, RESULT_OK_DELETED, processed)
    assert op
    assert disable == ""
    assert partner_from_row(ACTION_DELETE, "") == ""
    assert partner_from_row("delete", "anything") == ""


def test_result_excel_and_telegram_path_for_delete(tmp_path, monkeypatch):
    """Delete routes through disable worker path: engine result + Stats summary."""
    from automation import worker as worker_mod
    from automation.runtime import WalletEditorTask

    result_xlsx = tmp_path / "wallet_editor_result_delete_WALTER.xlsx"
    pd.DataFrame(
        {
            "card": ["9860246700001620"],
            "action": ["delete"],
            "value": [""],
            "status": [RESULT_OK_DELETED],
            "comment": [RESULT_OK_DELETED],
        }
    ).to_excel(result_xlsx, index=False)

    sent = {}

    def fake_run(file_path, cfg):
        return str(result_xlsx), Stats(ok=1, fail=0, skip=0)

    monkeypatch.setattr(worker_mod, "run", fake_run)
    monkeypatch.setattr(worker_mod, "manual_sync_enabled", lambda: False)
    monkeypatch.setattr(
        worker_mod,
        "send_text",
        lambda **kw: sent.setdefault("text", kw.get("text")),
    )
    monkeypatch.setattr(
        worker_mod,
        "send_document",
        lambda **kw: sent.setdefault("doc", kw.get("path")),
    )
    monkeypatch.setattr(
        worker_mod,
        "prepare_registry_outbox_and_schedule",
        lambda *a, **k: sent.setdefault("registry", True),
    )
    monkeypatch.setattr(worker_mod, "delayed_cleanup", lambda *a, **k: None)
    monkeypatch.setattr(
        worker_mod,
        "build_wallet_editor_result_path",
        lambda *a, **k: str(result_xlsx),
    )

    task = WalletEditorTask(
        file_path=str(tmp_path / "in.xlsx"),
        chat_id=1,
        telegram_user_id=2,
        operator_profile="WALTER",
        source_file_name="delete.xlsx",
        login="u",
        password="p",
        auth_state_path=str(tmp_path / "auth.json"),
    )
    worker_mod._run_disable_task("WALTER", task)

    assert "OK=1" in sent["text"]
    assert sent["doc"] == str(result_xlsx)
    assert sent["registry"] is True


# --- Strict search settle (stale 100-row table) ---


def test_search_waits_through_stale_100_rows_then_finds_card():
    """100 old rows before Enter; target appears only after filter refresh."""
    from automation.engine import find_strict_matching_row_index

    page = MagicMock()
    card = "9990080815129999"
    stale = (100, tuple(f"OLD{i:014d}" for i in range(5)))
    fresh = (1, (card,))
    fps = [stale, stale, stale, fresh]
    matches = [
        (None, 100, "OLD"),
        (None, 100, "OLD"),
        (None, 100, "OLD"),
        (0, 1, card),
    ]

    with (
        patch("automation.engine._DELETE_SEARCH_TIMEOUT_MS", 800),
        patch("automation.engine._DELETE_SEARCH_STABLE_POLLS", 2),
        patch("automation.engine._ROW_MATCH_POLL_MS", 10),
        patch("automation.engine.wallet_editor_row_match_timeout_ms", return_value=0),
        patch("automation.engine._close_stale_modal"),
        patch("automation.engine._fill_card_search_input", return_value=card),
        patch("automation.engine._press_card_search_enter"),
        patch("automation.engine._page_shows_empty_wallet_results", return_value=False),
        patch("automation.engine._table_row_fingerprint", side_effect=fps),
        patch("automation.engine._try_match_strict_row_index", side_effect=matches),
    ):
        page.locator.return_value = MagicMock()
        idx = find_strict_matching_row_index(page, card)

    assert idx == 0


def test_search_old_rows_linger_then_update_to_target():
    from automation.engine import find_strict_matching_row_index

    page = MagicMock()
    card = "9990080815129999"
    stale = (100, ("AAAAAAAAAAAAAAA1", "AAAAAAAAAAAAAAA2"))
    updated = (1, (card,))
    fps = [stale, stale, stale, updated]
    matches = [(None, 100, "A"), (None, 100, "A"), (None, 100, "A"), (0, 1, card)]

    with (
        patch("automation.engine._DELETE_SEARCH_TIMEOUT_MS", 800),
        patch("automation.engine._DELETE_SEARCH_STABLE_POLLS", 2),
        patch("automation.engine._ROW_MATCH_POLL_MS", 10),
        patch("automation.engine.wallet_editor_row_match_timeout_ms", return_value=0),
        patch("automation.engine._close_stale_modal"),
        patch("automation.engine._fill_card_search_input", return_value=card),
        patch("automation.engine._press_card_search_enter"),
        patch("automation.engine._page_shows_empty_wallet_results", return_value=False),
        patch("automation.engine._table_row_fingerprint", side_effect=fps),
        patch("automation.engine._try_match_strict_row_index", side_effect=matches),
    ):
        assert find_strict_matching_row_index(page, card) == 0


def test_search_empty_after_refresh_returns_none_skip():
    from automation.engine import find_strict_matching_row_index

    page = MagicMock()
    card = "9990080815129999"
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
        assert find_strict_matching_row_index(page, card) is None


def test_search_unsettled_stale_table_raises_not_skip():
    """100 stale rows that never refresh even after second Enter → technical error."""
    from automation.engine import CardSearchUnsettledError, find_strict_matching_row_index

    page = MagicMock()
    card = "9990080815129999"
    stale = (100, tuple(f"OLD{i:014d}" for i in range(5)))
    enter_calls = []

    def counting_enter(*args, **kwargs):
        enter_calls.append(1)

    with (
        patch("automation.engine._DELETE_SEARCH_TIMEOUT_MS", 40),
        patch("automation.engine._ROW_MATCH_POLL_MS", 5),
        patch("automation.engine.wallet_editor_row_match_timeout_ms", return_value=0),
        patch("automation.engine._close_stale_modal"),
        patch("automation.engine._fill_card_search_input", return_value=card) as fill,
        patch("automation.engine._press_card_search_enter", side_effect=counting_enter),
        patch("automation.engine._table_row_fingerprint", return_value=stale),
        patch(
            "automation.engine._try_match_strict_row_index",
            return_value=(None, 100, "OLD"),
        ),
        patch("automation.engine._page_shows_empty_wallet_results", return_value=False),
    ):
        with pytest.raises(CardSearchUnsettledError):
            find_strict_matching_row_index(page, card)

    fill.assert_called_once()
    assert len(enter_calls) == 2


def test_zero_rows_second_enter_stable_empty_is_absent():
    """0 rows → Enter → 0 rows → second Enter → 0 rows ⇒ confirmed absent."""
    from automation.engine import find_strict_matching_row_index

    page = MagicMock()
    card = "9990080815129999"
    empty = (0, tuple())
    enter_calls = []

    def counting_enter(*args, **kwargs):
        enter_calls.append(kwargs)

    with (
        patch("automation.engine._DELETE_SEARCH_TIMEOUT_MS", 40),
        patch("automation.engine._DELETE_SEARCH_STABLE_POLLS", 2),
        patch("automation.engine._ROW_MATCH_POLL_MS", 5),
        patch("automation.engine.wallet_editor_row_match_timeout_ms", return_value=0),
        patch("automation.engine._close_stale_modal"),
        patch("automation.engine._fill_card_search_input", return_value=card) as fill,
        patch("automation.engine._press_card_search_enter", side_effect=counting_enter),
        patch("automation.engine._table_row_fingerprint", return_value=empty),
        patch(
            "automation.engine._try_match_strict_row_index",
            return_value=(None, 0, ""),
        ),
        patch("automation.engine._page_shows_empty_wallet_results", return_value=True),
    ):
        assert find_strict_matching_row_index(page, card) is None

    fill.assert_called_once()
    assert len(enter_calls) == 2


def test_zero_rows_second_enter_card_appears_is_found():
    from automation.engine import find_strict_matching_row_index

    page = MagicMock()
    card = "9990080815129999"
    empty = (0, tuple())
    phase = {"enter": 0}

    def press_enter(*args, **kwargs):
        phase["enter"] += 1

    def fingerprint(_page, _card):
        return empty

    def try_match(rows, digits, card_arg):
        # Only after the second Enter do we "see" the card.
        if phase["enter"] >= 2:
            return 0, 1, card
        return None, 0, ""

    with (
        patch("automation.engine._DELETE_SEARCH_TIMEOUT_MS", 40),
        patch("automation.engine._DELETE_SEARCH_STABLE_POLLS", 2),
        patch("automation.engine._ROW_MATCH_POLL_MS", 5),
        patch("automation.engine.wallet_editor_row_match_timeout_ms", return_value=0),
        patch("automation.engine._close_stale_modal"),
        patch("automation.engine._fill_card_search_input", return_value=card) as fill,
        patch("automation.engine._press_card_search_enter", side_effect=press_enter),
        patch("automation.engine._table_row_fingerprint", side_effect=fingerprint),
        patch("automation.engine._try_match_strict_row_index", side_effect=try_match),
        patch("automation.engine._page_shows_empty_wallet_results", return_value=True),
    ):
        assert find_strict_matching_row_index(page, card) == 0

    fill.assert_called_once()
    assert phase["enter"] == 2


def test_retry_enter_does_not_call_fill_again():
    from automation.engine import find_strict_matching_row_index

    page = MagicMock()
    card = "9990080815129999"
    empty = (0, tuple())

    with (
        patch("automation.engine._DELETE_SEARCH_TIMEOUT_MS", 40),
        patch("automation.engine._DELETE_SEARCH_STABLE_POLLS", 2),
        patch("automation.engine._ROW_MATCH_POLL_MS", 5),
        patch("automation.engine.wallet_editor_row_match_timeout_ms", return_value=0),
        patch("automation.engine._close_stale_modal"),
        patch("automation.engine._fill_card_search_input", return_value=card) as fill,
        patch("automation.engine._press_card_search_enter") as enter,
        patch("automation.engine._table_row_fingerprint", return_value=empty),
        patch(
            "automation.engine._try_match_strict_row_index",
            return_value=(None, 0, ""),
        ),
        patch("automation.engine._page_shows_empty_wallet_results", return_value=True),
    ):
        find_strict_matching_row_index(page, card)

    fill.assert_called_once_with(page, card)
    assert enter.call_count == 2


def test_control_search_zero_rows_maps_to_ok_deleted():
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()

    with (
        patch("automation.engine.find_strict_matching_row_index", return_value=0),
        patch("automation.engine.open_matched_card_row"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok"),
        patch("automation.engine._wait_form_hidden_after_delete", return_value=True),
        patch("automation.engine._wait_confirm_dialog_gone"),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        # post-delete control search: confirmed absent after empty settle
        patch("automation.engine.card_exists_strict", return_value=False),
    ):
        assert ensure_wallet_deleted(page, "9990080815129999", _cfg()) == RESULT_OK_DELETED


def test_initial_search_zero_rows_maps_to_skip_not_found():
    page = MagicMock()
    with patch("automation.engine.find_and_open_card_for_delete", return_value=None):
        assert (
            ensure_wallet_deleted(page, "9990080815129999", _cfg())
            == RESULT_SKIP_NOT_FOUND
        )


def test_unsettled_search_maps_to_fail_technical_not_skip():
    from automation.engine import CardSearchUnsettledError, RESULT_FAIL_TECHNICAL

    page = MagicMock()
    with patch(
        "automation.engine.find_and_open_card_for_delete",
        side_effect=CardSearchUnsettledError("9990080815129999"),
    ):
        result = ensure_wallet_deleted(page, "9990080815129999", _cfg())
    assert result == RESULT_FAIL_TECHNICAL
    assert result != RESULT_SKIP_NOT_FOUND


def test_post_delete_unsettled_verify_is_fail_delete_timeout():
    from automation.engine import CardSearchUnsettledError, classify_after_delete_confirm

    page = MagicMock()
    with (
        patch("automation.engine._wait_form_hidden_after_delete", return_value=True),
        patch("automation.engine._wait_confirm_dialog_gone"),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        patch(
            "automation.engine.card_exists_strict",
            side_effect=CardSearchUnsettledError("9990080815129999"),
        ),
    ):
        assert (
            classify_after_delete_confirm(page, "9990080815129999")
            == RESULT_FAIL_DELETE_TIMEOUT
        )


def test_settled_without_match_after_row_set_change():
    """Fingerprint changes to a new non-empty set without the card → SKIP (None)."""
    from automation.engine import find_strict_matching_row_index

    page = MagicMock()
    card = "9990080815129999"
    before = (100, ("OLD0000000000001", "OLD0000000000002"))
    other = (2, ("1111111111111111", "2222222222222222"))
    fps = [before, other, other, other]
    matches = [(None, 100, "OLD"), (None, 2, "1111"), (None, 2, "1111"), (None, 2, "1111")]

    with (
        patch("automation.engine._DELETE_SEARCH_TIMEOUT_MS", 800),
        patch("automation.engine._DELETE_SEARCH_STABLE_POLLS", 2),
        patch("automation.engine._ROW_MATCH_POLL_MS", 10),
        patch("automation.engine.wallet_editor_row_match_timeout_ms", return_value=0),
        patch("automation.engine._close_stale_modal"),
        patch("automation.engine._fill_card_search_input", return_value=card),
        patch("automation.engine._press_card_search_enter"),
        patch("automation.engine._page_shows_empty_wallet_results", return_value=False),
        patch("automation.engine._table_row_fingerprint", side_effect=fps),
        patch("automation.engine._try_match_strict_row_index", side_effect=matches),
    ):
        assert find_strict_matching_row_index(page, card) is None
