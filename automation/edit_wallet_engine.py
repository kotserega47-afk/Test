"""WalletEditor Edit Wallet — Antares UI automation (v1)."""

from __future__ import annotations

import os
import time

from playwright.sync_api import sync_playwright

from automation.add_wallet_engine import (
    MODAL_BODY,
    MODAL_HEADER,
    PHASE2_OPTIONAL_SELECT_FIELDS,
    PHASE2_OPTIONAL_TEXT_FIELDS,
    PHASE2_OPTIONAL_TEXTAREA_FIELDS,
    SaveWaitOutcome,
    _checkbox_for_label,
    _detect_aggregate_expansion,
    _fill_gender_radio,
    _fill_kyc_checkbox,
    _fill_locator_text,
    _find_aggregate_checkbox_label,
    _find_aggregate_field_input,
    _set_multiselect_list,
    _fill_optional_select_by_label,
    _fill_optional_text_by_label,
    _fill_optional_textarea_by_label,
    _select_by_label,
    card_exists_strict,
    goto_wallet_page,
    save_add_wallet_modal,
)
from automation.add_wallet_engine import ACCOUNT_NUMBER_LABELS
from automation.audit import log, mask_card, normalize_card_digits, row_matches_card_strict
from automation.edit_wallet_contract import (
    LOG_PREFIX,
    RESULT_FAIL_AGGREGATE_NOT_ACTIVE,
    RESULT_FAIL_FILL,
    RESULT_FAIL_NOT_FOUND,
    RESULT_FAIL_OPEN_CARD,
    RESULT_FAIL_SAVE_TIMEOUT,
    RESULT_FAIL_TECHNICAL,
    RESULT_FAIL_VALIDATION,
    RESULT_OK,
    RESULT_SKIP_NOT_FOUND,
    EditWalletBatchSummary,
    EditWalletRow,
    EditWalletRowResult,
    make_row_result,
    prepare_edit_wallet_batch,
    write_result_excel,
)
from automation.engine import (
    CARD_INPUT,
    ROW_SELECTOR,
    OpenCardStageError,
    _close_stale_modal,
    _read_row_text,
    _submit_card_filter,
    _verify_modal_card_number,
    _wait_modal_card_data_ready,
    _wait_modal_container_visible,
    _ROW_MATCH_POLL_MS,
)
from automation.add_wallet_engine import _ensure_logged_in
from automation.runtime import (
    RunConfig,
    require_wallet_editor_antares_credentials,
    wallet_editor_playwright_slow_mo_ms,
    wallet_editor_row_match_timeout_ms,
)
from core.playwright_cleanup import close_playwright_stack

EDIT_TITLE = "Изменение кошелька"
AGGREGATE_NESTED_COLUMNS = frozenset({"account", "merchant_id_sbp", "account_number"})


class AggregateNotActiveError(Exception):
    """Raised when Edit Wallet cannot update aggregate fields without switching aggregate."""


def _log(stage: str, *, card: str | None = None, extra: str = "") -> None:
    tail = f" card_tail={mask_card(card)}" if card else ""
    suffix = f" {extra}" if extra else ""
    log.info(f"{LOG_PREFIX} stage={stage}{tail}{suffix}")


def _try_find_strict_row_index(rows, card_digits: str, card: str) -> int | None:
    for i in range(rows.count()):
        row_text = _read_row_text(rows.nth(i), row_index=i, card=card)
        if row_text is None:
            continue
        if row_matches_card_strict(row_text, card_digits):
            return i
    return None


def _wait_for_visible_strict_row(page, card: str, card_digits: str) -> int:
    rows = page.locator(ROW_SELECTOR)
    timeout_ms = wallet_editor_row_match_timeout_ms()

    if timeout_ms <= 0:
        match_index = _try_find_strict_row_index(rows, card_digits, card)
        if match_index is not None:
            return match_index
        raise OpenCardStageError(
            "row_match",
            card,
            message=f"Карта не найдена (strict row_match): {card}",
        )

    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if rows.count() > 0:
            match_index = _try_find_strict_row_index(rows, card_digits, card)
            if match_index is not None:
                return match_index
        page.wait_for_timeout(_ROW_MATCH_POLL_MS)

    raise OpenCardStageError(
        "row_match",
        card,
        message=f"Карта не найдена (strict row_match): {card}",
    )


def _press_enter_on_search(page) -> None:
    page.locator(CARD_INPUT).press("Enter")


def _search_for_strict_row(page, card: str) -> int:
    card_digits = normalize_card_digits(card)

    _log("open_card_search", card=card, extra="attempt=1")
    _submit_card_filter(page, card)

    for attempt in (1, 2, 3):
        if attempt == 2:
            _log("open_card_search_retry", card=card, extra="reason=no_strict_row")
            _log("open_card_search", card=card, extra="attempt=2")
            _press_enter_on_search(page)
        elif attempt == 3:
            _log("open_card_search_retry", card=card, extra="reason=no_strict_row")
            _log("open_card_search", card=card, extra="attempt=3")
            _submit_card_filter(page, card)

        try:
            page.wait_for_selector(ROW_SELECTOR, timeout=10_000)
            return _wait_for_visible_strict_row(page, card, card_digits)
        except OpenCardStageError:
            if attempt == 3:
                raise
            continue
        except Exception as exc:
            if attempt == 3:
                raise OpenCardStageError(
                    "row_match",
                    card,
                    message=f"Карта не найдена (strict row_match): {card}",
                ) from exc
            continue

    raise OpenCardStageError(
        "row_match",
        card,
        message=f"Карта не найдена (strict row_match): {card}",
    )


def _click_strict_row_and_verify_modal(page, card: str, row_index: int) -> None:
    modal = page.locator(MODAL_BODY)
    rows = page.locator(ROW_SELECTOR)
    card_digits = normalize_card_digits(card)

    row_text = _read_row_text(rows.nth(row_index), row_index=row_index, card=card)
    if not row_text or not row_matches_card_strict(row_text, card_digits):
        raise OpenCardStageError(
            "row_match",
            card,
            message=f"strict row match lost before click: {card}",
        )

    rows.nth(row_index).click()
    _wait_modal_container_visible(modal, card)
    modal_card_value = _wait_modal_card_data_ready(page, card)
    try:
        _verify_modal_card_number(card, modal_card_value)
    except OpenCardStageError as exc:
        if exc.stage == "card_verify":
            _log(
                "open_card_modal_mismatch",
                card=card,
                extra=f"expected={card} actual={modal_card_value}",
            )
        raise


def open_card_strict(page, card: str) -> None:
    """Edit Wallet card open: strict row match, search retries, modal verify with one retry."""
    _close_stale_modal(page)

    for open_attempt in (1, 2):
        if open_attempt == 2:
            _log("open_card_retry_after_mismatch", card=card)
            _close_stale_modal(page)

        try:
            row_index = _search_for_strict_row(page, card)
            _click_strict_row_and_verify_modal(page, card, row_index)
            _log("open_card_strict_found", card=card)
            return
        except OpenCardStageError as exc:
            if exc.stage == "card_verify" and open_attempt == 1:
                continue
            raise


def _assert_edit_modal(page) -> None:
    header = page.locator(MODAL_HEADER)
    text = header.inner_text(timeout=3_000)
    if EDIT_TITLE not in text:
        raise RuntimeError(f"ожидалась модалка «{EDIT_TITLE}», получено: {text!r}")


def _expected_aggregate_name(row: EditWalletRow) -> str | None:
    if row.aggregate and ({"aggregate", "aggregates"} & row.provided_columns):
        return row.aggregate
    return None


def _provided_aggregate_nested(provided: frozenset[str]) -> frozenset[str]:
    return AGGREGATE_NESTED_COLUMNS & provided


def _assert_aggregate_active(page, aggregate_name: str) -> None:
    modal = page.locator(MODAL_BODY)
    label_el = _find_aggregate_checkbox_label(modal, aggregate_name)
    if label_el is None:
        raise AggregateNotActiveError(f"aggregate not active: {aggregate_name}")

    checkbox = _checkbox_for_label(label_el)
    if checkbox.count() == 0:
        raise AggregateNotActiveError(f"aggregate not active: {aggregate_name}")

    try:
        checked = checkbox.first.is_checked()
    except Exception as exc:
        raise AggregateNotActiveError(f"aggregate not active: {aggregate_name}") from exc

    if not checked:
        raise AggregateNotActiveError(f"aggregate not active: {aggregate_name}")

    if _detect_aggregate_expansion(modal) is None:
        raise AggregateNotActiveError(f"aggregate not active: {aggregate_name}")


def _assert_aggregate_block_visible(page) -> None:
    modal = page.locator(MODAL_BODY)
    if _detect_aggregate_expansion(modal) is None:
        raise AggregateNotActiveError("aggregate block not visible")


def _fill_edit_aggregate_field(modal, labels: tuple[str, ...], value: str) -> None:
    field = None
    matched_label: str | None = None
    for label in labels:
        field = _find_aggregate_field_input(modal, label)
        if field is not None:
            matched_label = label
            break

    if field is None:
        label_name = matched_label or labels[0]
        raise AggregateNotActiveError(f"aggregate field not visible: {label_name}")

    try:
        visible = field.is_visible()
    except Exception as exc:
        raise AggregateNotActiveError(
            f"aggregate field not visible: {matched_label or labels[0]}"
        ) from exc

    if not visible:
        raise AggregateNotActiveError(f"aggregate field not visible: {matched_label or labels[0]}")

    _fill_locator_text(
        field,
        matched_label or labels[0],
        value,
        log_prefix=LOG_PREFIX,
    )


def fill_edit_wallet_form(page, row: EditWalletRow) -> None:
    provided = row.provided_columns

    if "phone" in provided:
        _fill_optional_text_by_label(page, "Телефон", row.phone)
    if "status" in provided:
        _select_by_label(page, "Статус", row.status)
    if "state" in provided:
        _select_by_label(page, "Состояние", row.state)
    if "direction" in provided:
        _select_by_label(page, "Направление", row.direction)
    if "pool" in provided:
        _select_by_label(page, "Пул", row.pool)

    if "partners" in provided:
        _set_multiselect_list(page, "Привязан к партнеру", row.partners)

    if "groups" in provided:
        for group_label in ("Группа", "Группы"):
            try:
                _set_multiselect_list(page, group_label, row.groups)
                break
            except RuntimeError:
                if group_label == "Группы":
                    raise

    expected_aggregate = _expected_aggregate_name(row)
    nested_provided = _provided_aggregate_nested(provided)

    if expected_aggregate:
        _assert_aggregate_active(page, expected_aggregate)
    elif nested_provided:
        _assert_aggregate_block_visible(page)

    if nested_provided:
        modal = page.locator(MODAL_BODY)
        if "account" in provided:
            _fill_edit_aggregate_field(modal, ("Аккаунт",), row.account)
        if "merchant_id_sbp" in provided:
            _fill_edit_aggregate_field(modal, ("MerchantId СБП",), row.merchant_id_sbp)
        if "account_number" in provided:
            _fill_edit_aggregate_field(modal, ACCOUNT_NUMBER_LABELS, row.account_number)

    for attr, label in PHASE2_OPTIONAL_TEXT_FIELDS:
        if attr in provided:
            _fill_optional_text_by_label(page, label, getattr(row, attr, ""))
    for attr, label in PHASE2_OPTIONAL_TEXTAREA_FIELDS:
        if attr in provided:
            _fill_optional_textarea_by_label(page, label, getattr(row, attr, ""))
    for attr, label in PHASE2_OPTIONAL_SELECT_FIELDS:
        if attr in provided:
            _fill_optional_select_by_label(page, label, getattr(row, attr, ""))
    if "gender" in provided:
        _fill_gender_radio(page, row.gender)
    if "kyc" in provided:
        _fill_kyc_checkbox(page, row.kyc)


def _process_row(
    page,
    row: EditWalletRow,
    *,
    operator_profile: str,
) -> EditWalletRowResult:
    _log("row_started", card=row.card, extra=f"row={row.row_number}")

    try:
        if not card_exists_strict(page, row.card):
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_SKIP_NOT_FOUND,
                comment="карта не найдена в Antares",
                operator_profile=operator_profile,
            )

        try:
            open_card_strict(page, row.card)
            _assert_edit_modal(page)
            _log("modal_opened", card=row.card)
        except (OpenCardStageError, RuntimeError) as exc:
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_FAIL_OPEN_CARD,
                comment=str(exc),
                operator_profile=operator_profile,
            )

        try:
            fill_edit_wallet_form(page, row)
            _log("form_filled", card=row.card)
        except AggregateNotActiveError as exc:
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_FAIL_AGGREGATE_NOT_ACTIVE,
                comment=str(exc),
                operator_profile=operator_profile,
            )
        except Exception as exc:
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_FAIL_FILL,
                comment=str(exc),
                operator_profile=operator_profile,
            )

        _log("save_clicked", card=row.card)
        save_outcome = save_add_wallet_modal(page)

        if save_outcome.status == "validation":
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_FAIL_VALIDATION,
                comment=save_outcome.detail or "validation error",
                operator_profile=operator_profile,
            )
        if save_outcome.status == "timeout":
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_FAIL_SAVE_TIMEOUT,
                comment="modal did not close after save",
                operator_profile=operator_profile,
            )

        _log("modal_closed", card=row.card)
        _log("post_search", card=row.card)
        if card_exists_strict(page, row.card):
            _log("row_result", card=row.card, extra="OK")
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_OK,
                comment="card found after save",
                operator_profile=operator_profile,
            )

        return make_row_result(
            row,
            row_number=row.row_number,
            result=RESULT_FAIL_NOT_FOUND,
            comment="card not found after save",
            operator_profile=operator_profile,
        )
    except Exception as exc:
        log.exception(f"{LOG_PREFIX} stage=row_result card_tail={mask_card(row.card)} error={exc}")
        return make_row_result(
            row,
            row_number=row.row_number,
            result=RESULT_FAIL_TECHNICAL,
            comment=str(exc),
            operator_profile=operator_profile,
        )


def run(
    file_path: str,
    cfg: RunConfig,
    *,
    result_file_path: str | None = None,
) -> tuple[str, EditWalletBatchSummary]:
    _log("contract_validated", extra=f"file={file_path}")
    require_wallet_editor_antares_credentials(cfg)
    profile = (cfg.operator_profile or "unknown").strip()

    batch = prepare_edit_wallet_batch(file_path)
    summary = EditWalletBatchSummary()
    results: list[EditWalletRowResult] = []

    for invalid in batch.invalid_rows:
        row_number = invalid.row.row_number if invalid.row else 0
        item = make_row_result(
            invalid.row,
            row_number=row_number,
            result=invalid.result,
            comment=invalid.comment,
            operator_profile=profile,
        )
        results.append(item)
        summary.record(invalid.result)

    if not batch.rows and not results:
        out_path = result_file_path or os.path.join("/tmp/wallet_editor", "wallet_edit_result_empty.xlsx")
        write_result_excel(results, out_path)
        return out_path, summary

    slow_mo = wallet_editor_playwright_slow_mo_ms()
    _log("batch_summary", extra=f"rows={len(batch.rows)}")

    with sync_playwright() as p:
        browser = None
        context = None
        page = None
        try:
            browser = p.chromium.launch(
                headless=cfg.headless,
                slow_mo=slow_mo,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            if os.path.exists(cfg.auth_state_path):
                context = browser.new_context(storage_state=cfg.auth_state_path)
            else:
                context = browser.new_context()
            page = context.new_page()
            _ensure_logged_in(page, context, cfg)
            goto_wallet_page(page)

            for row in batch.rows:
                item = _process_row(page, row, operator_profile=profile)
                results.append(item)
                summary.record(item.result)
                _log(
                    "row_result",
                    card=row.card,
                    extra=f"result={item.result}",
                )
        finally:
            close_playwright_stack(page=page, context=context, browser=browser)

    if result_file_path is None:
        result_file_path = os.path.join(
            "/tmp/wallet_editor",
            f"wallet_edit_result_{profile}.xlsx",
        )
    write_result_excel(results, result_file_path)
    _log(
        "batch_summary",
        extra=f"total={summary.total} ok={summary.ok} skip={summary.skip} fail={summary.fail}",
    )
    return result_file_path, summary
