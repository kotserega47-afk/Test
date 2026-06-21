"""WalletEditor Edit Wallet — Antares UI automation (v1)."""

from __future__ import annotations

import os

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
    _fill_multiselect_list,
    _fill_optional_select_by_label,
    _fill_optional_text_by_label,
    _fill_optional_textarea_by_label,
    _select_by_label,
    card_exists_strict,
    goto_wallet_page,
    save_add_wallet_modal,
)
from automation.add_wallet_engine import ACCOUNT_NUMBER_LABELS
from automation.audit import log, mask_card
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
from automation.engine import OpenCardStageError, open_card
from automation.add_wallet_engine import _ensure_logged_in
from automation.runtime import RunConfig, require_wallet_editor_antares_credentials, wallet_editor_playwright_slow_mo_ms
from core.playwright_cleanup import close_playwright_stack

EDIT_TITLE = "Изменение кошелька"
AGGREGATE_NESTED_COLUMNS = frozenset({"account", "merchant_id_sbp", "account_number"})


class AggregateNotActiveError(Exception):
    """Raised when Edit Wallet cannot update aggregate fields without switching aggregate."""


def _log(stage: str, *, card: str | None = None, extra: str = "") -> None:
    tail = f" card_tail={mask_card(card)}" if card else ""
    suffix = f" {extra}" if extra else ""
    log.info(f"{LOG_PREFIX} stage={stage}{tail}{suffix}")


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
        _fill_multiselect_list(page, "Привязан к партнеру", row.partners)

    if "groups" in provided:
        for group_label in ("Группа", "Группы"):
            try:
                _fill_multiselect_list(page, group_label, row.groups)
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
            open_card(page, row.card)
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
