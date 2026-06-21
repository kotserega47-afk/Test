"""WalletEditor Edit Wallet — Antares UI automation (v1)."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

from playwright.sync_api import sync_playwright

from automation.add_wallet_engine import (
    MODAL_BODY,
    MODAL_HEADER,
    PHASE2_OPTIONAL_SELECT_FIELDS,
    PHASE2_OPTIONAL_TEXT_FIELDS,
    PHASE2_OPTIONAL_TEXTAREA_FIELDS,
    SaveWaitOutcome,
    FieldNotEditableError,
    _checkbox_for_label,
    _detect_aggregate_expansion,
    _fill_gender_radio,
    _fill_kyc_checkbox,
    _fill_locator_text,
    _clear_locator_text,
    _find_aggregate_checkbox_label,
    _find_aggregate_field_input,
    _set_multiselect_list,
    _clear_multiselect_list,
    _clear_text_by_label,
    _clear_textarea_by_label,
    _uncheck_kyc_checkbox,
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
    build_success_comment,
)
from automation.engine import (
    OpenCardStageError,
    _close_stale_modal,
    _wait_for_strict_matching_row,
    open_card_with_row_matcher,
)
from automation.add_wallet_engine import _ensure_logged_in
from automation.runtime import (
    RunConfig,
    require_wallet_editor_antares_credentials,
    wallet_editor_playwright_slow_mo_ms,
)
from core.playwright_cleanup import close_playwright_stack

EDIT_TITLE = "Изменение кошелька"
AGGREGATE_NESTED_COLUMNS = frozenset({"account", "merchant_id_sbp", "account_number"})

_OPEN_CARD_STRICT_ATTEMPTS = 3
_OPEN_CARD_STRICT_RETRY_STAGES = frozenset({"modal_container", "modal_data", "card_verify"})


class AggregateNotActiveError(Exception):
    """Raised when Edit Wallet cannot update aggregate fields without switching aggregate."""


def _log(stage: str, *, card: str | None = None, extra: str = "") -> None:
    tail = f" card_tail={mask_card(card)}" if card else ""
    suffix = f" {extra}" if extra else ""
    log.info(f"{LOG_PREFIX} stage={stage}{tail}{suffix}")


def _timing_outcome_for_result(result: str) -> str:
    if result == RESULT_OK:
        return "ok"
    if result == RESULT_SKIP_NOT_FOUND:
        return "skip"
    return "fail"


def _log_timing_row(
    row: EditWalletRow,
    step: str,
    duration_ms: int,
    outcome: str,
    *,
    column: str | None = None,
) -> None:
    try:
        column_suffix = f" column={column}" if column else ""
        log.info(
            f"{LOG_PREFIX}[timing] row={row.row_number} "
            f"card_tail={mask_card(row.card)} step={step} "
            f"duration_ms={duration_ms} outcome={outcome}{column_suffix}"
        )
    except Exception:
        pass


def _log_timing_batch(step: str, duration_ms: int, outcome: str) -> None:
    try:
        log.info(
            f"{LOG_PREFIX}[timing] batch step={step} "
            f"duration_ms={duration_ms} outcome={outcome}"
        )
    except Exception:
        pass


@contextmanager
def _row_timing_step(row: EditWalletRow, step: str, *, success_outcome: str = "ok"):
    start = time.perf_counter()
    outcome = "fail"
    try:
        yield
        outcome = success_outcome
    except Exception:
        outcome = "fail"
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _log_timing_row(row, step, duration_ms, outcome)


@contextmanager
def _batch_timing_step(step: str, *, success_outcome: str = "ok"):
    start = time.perf_counter()
    outcome = "fail"
    try:
        yield
        outcome = success_outcome
    except Exception:
        outcome = "fail"
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _log_timing_batch(step, duration_ms, outcome)


def _run_timed_field_clear(row: EditWalletRow, column: str, action) -> None:
    start = time.perf_counter()
    outcome = "ok"
    try:
        if action():
            outcome = "noop"
    except Exception:
        outcome = "fail"
        duration_ms = int((time.perf_counter() - start) * 1000)
        _log_timing_row(row, "field_clear", duration_ms, outcome, column=column)
        raise
    duration_ms = int((time.perf_counter() - start) * 1000)
    _log_timing_row(row, "field_clear", duration_ms, outcome, column=column)


def _run_timed_field_update(row: EditWalletRow, column: str, action) -> None:
    start = time.perf_counter()
    outcome = "ok"
    try:
        if action():
            outcome = "skip"
    except Exception:
        outcome = "fail"
        duration_ms = int((time.perf_counter() - start) * 1000)
        _log_timing_row(row, "field_update", duration_ms, outcome, column=column)
        raise
    duration_ms = int((time.perf_counter() - start) * 1000)
    _log_timing_row(row, "field_update", duration_ms, outcome, column=column)


def _finish_row(
    row: EditWalletRow,
    result_item: EditWalletRowResult,
    *,
    row_started_at: float,
) -> EditWalletRowResult:
    duration_ms = int((time.perf_counter() - row_started_at) * 1000)
    _log_timing_row(
        row,
        "row_total",
        duration_ms,
        _timing_outcome_for_result(result_item.result),
    )
    return result_item


def open_card_strict(page, card: str) -> None:
    """Edit Wallet: shared stable open_card flow + strict row match + strict modal verify."""
    last_exc: OpenCardStageError | None = None

    for open_attempt in range(1, _OPEN_CARD_STRICT_ATTEMPTS + 1):
        if open_attempt > 1:
            _log("retry_after_no_modal", card=card, extra=f"attempt={open_attempt}")
            _close_stale_modal(page)

        _log("open_card_search", card=card, extra=f"attempt={open_attempt}")
        try:
            open_card_with_row_matcher(page, card, _wait_for_strict_matching_row)
            _log("open_card_strict_found", card=card)
            return
        except OpenCardStageError as exc:
            last_exc = exc
            if exc.stage == "card_verify":
                _log(
                    "open_card_modal_mismatch",
                    card=card,
                    extra=f"attempt={open_attempt}",
                )
            elif exc.stage == "modal_container":
                _log(
                    "open_card_modal_container_timeout",
                    card=card,
                    extra=f"attempt={open_attempt}",
                )
            if (
                exc.stage not in _OPEN_CARD_STRICT_RETRY_STAGES
                or open_attempt >= _OPEN_CARD_STRICT_ATTEMPTS
            ):
                raise

    if last_exc is not None:
        raise last_exc


def _assert_edit_modal(page) -> None:
    header = page.locator(MODAL_HEADER)
    text = header.inner_text(timeout=3_000)
    if EDIT_TITLE not in text:
        raise RuntimeError(f"ожидалась модалка «{EDIT_TITLE}», получено: {text!r}")


def _expected_aggregate_name(row: EditWalletRow) -> str | None:
    if row.aggregate and ({"aggregate", "aggregates"} & row.provided_columns):
        return row.aggregate
    return None


def _provided_aggregate_nested(provided: frozenset[str], cleared: frozenset[str]) -> frozenset[str]:
    return AGGREGATE_NESTED_COLUMNS & (provided | cleared)


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


def _clear_edit_aggregate_field(modal, labels: tuple[str, ...]) -> None:
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

    _clear_locator_text(
        field,
        matched_label or labels[0],
        log_prefix=LOG_PREFIX,
    )


def _clear_field_text(page, column: str, label: str) -> None:
    _log("field_clear_started", extra=f"column={column} control=text")
    try:
        _clear_text_by_label(page, label, log_prefix=LOG_PREFIX)
    except FieldNotEditableError as exc:
        _log("field_clear_failed", extra=f"column={column} reason={exc}")
        raise
    except Exception as exc:
        _log("field_clear_failed", extra=f"column={column} reason={exc}")
        raise RuntimeError(str(exc)) from exc
    _log("field_clear_completed", extra=f"column={column}")


def _clear_field_textarea(page, column: str, label: str) -> None:
    _log("field_clear_started", extra=f"column={column} control=textarea")
    try:
        _clear_textarea_by_label(page, label, log_prefix=LOG_PREFIX)
    except FieldNotEditableError as exc:
        _log("field_clear_failed", extra=f"column={column} reason={exc}")
        raise
    except Exception as exc:
        _log("field_clear_failed", extra=f"column={column} reason={exc}")
        raise RuntimeError(str(exc)) from exc
    _log("field_clear_completed", extra=f"column={column}")


def _clear_field_multiselect(page, column: str, label: str) -> bool:
    """Clear multiselect; returns True when already empty (noop)."""
    _log("multiselect_clear_started", extra=f"column={column} label={label}")
    try:
        noop = _clear_multiselect_list(page, label, column=column)
    except RuntimeError as exc:
        _log("multiselect_clear_failed", extra=f"column={column} reason={exc}")
        raise
    if noop:
        _log("field_clear_noop", extra=f"column={column} reason=already_empty")
        return True
    _log("multiselect_clear_completed", extra=f"column={column}")
    _log("field_clear_completed", extra=f"column={column}")
    return False


def _clear_field_kyc(page) -> bool:
    """Uncheck KYC; returns True when already unchecked (noop)."""
    _log("kyc_uncheck_started")
    try:
        noop = _uncheck_kyc_checkbox(page, log_prefix=LOG_PREFIX)
    except RuntimeError as exc:
        _log("field_clear_failed", extra="column=kyc reason=KYC checkbox not found")
        raise
    if noop:
        _log("kyc_uncheck_noop", extra="reason=already_unchecked")
        _log("field_clear_noop", extra="column=kyc reason=already_unchecked")
        return True
    _log("kyc_uncheck_completed", extra="checked=false")
    _log("field_clear_completed", extra="column=kyc")
    return False


def _clear_edit_wallet_fields(page, row: EditWalletRow) -> None:
    cleared = row.cleared_columns
    if not cleared:
        return

    if "partners" in cleared:
        _run_timed_field_clear(
            row,
            "partners",
            lambda: _clear_field_multiselect(page, "partners", "Привязан к партнеру"),
        )

    if "groups" in cleared:
        def _clear_groups() -> bool:
            for group_label in ("Группа", "Группы"):
                try:
                    return _clear_field_multiselect(page, "groups", group_label)
                except RuntimeError:
                    if group_label == "Группы":
                        raise
            return False

        _run_timed_field_clear(row, "groups", _clear_groups)

    nested_cleared = _provided_aggregate_nested(frozenset(), cleared)
    if nested_cleared:
        _assert_aggregate_block_visible(page)
        modal = page.locator(MODAL_BODY)
        if "account" in cleared:
            def _clear_account() -> bool:
                _log("field_clear_started", extra="column=account control=text")
                _clear_edit_aggregate_field(modal, ("Аккаунт",))
                _log("field_clear_completed", extra="column=account")
                return False

            _run_timed_field_clear(row, "account", _clear_account)
        if "merchant_id_sbp" in cleared:
            def _clear_merchant_id_sbp() -> bool:
                _log("field_clear_started", extra="column=merchant_id_sbp control=text")
                _clear_edit_aggregate_field(modal, ("MerchantId СБП",))
                _log("field_clear_completed", extra="column=merchant_id_sbp")
                return False

            _run_timed_field_clear(row, "merchant_id_sbp", _clear_merchant_id_sbp)
        if "account_number" in cleared:
            def _clear_account_number() -> bool:
                _log("field_clear_started", extra="column=account_number control=text")
                _clear_edit_aggregate_field(modal, ACCOUNT_NUMBER_LABELS)
                _log("field_clear_completed", extra="column=account_number")
                return False

            _run_timed_field_clear(row, "account_number", _clear_account_number)

    if "phone" in cleared:
        _run_timed_field_clear(
            row,
            "phone",
            lambda: (_clear_field_text(page, "phone", "Телефон"), False)[1],
        )

    for attr, label in PHASE2_OPTIONAL_TEXT_FIELDS:
        if attr in cleared:
            _run_timed_field_clear(
                row,
                attr,
                lambda a=attr, lbl=label: (_clear_field_text(page, a, lbl), False)[1],
            )

    for attr, label in PHASE2_OPTIONAL_TEXTAREA_FIELDS:
        if attr in cleared:
            _run_timed_field_clear(
                row,
                attr,
                lambda a=attr, lbl=label: (_clear_field_textarea(page, a, lbl), False)[1],
            )

    if "kyc" in cleared:
        _run_timed_field_clear(row, "kyc", lambda: _clear_field_kyc(page))


def _update_edit_wallet_form(page, row: EditWalletRow) -> None:
    provided = row.provided_columns

    if "phone" in provided:
        _run_timed_field_update(
            row,
            "phone",
            lambda: (not row.phone, _fill_optional_text_by_label(page, "Телефон", row.phone))[0],
        )
    if "status" in provided:
        _run_timed_field_update(
            row,
            "status",
            lambda: (False, _select_by_label(page, "Статус", row.status))[0],
        )
    if "state" in provided:
        _run_timed_field_update(
            row,
            "state",
            lambda: (False, _select_by_label(page, "Состояние", row.state))[0],
        )
    if "direction" in provided:
        _run_timed_field_update(
            row,
            "direction",
            lambda: (False, _select_by_label(page, "Направление", row.direction))[0],
        )
    if "pool" in provided:
        _run_timed_field_update(
            row,
            "pool",
            lambda: (False, _select_by_label(page, "Пул", row.pool))[0],
        )

    if "partners" in provided:
        _run_timed_field_update(
            row,
            "partners",
            lambda: (False, _set_multiselect_list(page, "Привязан к партнеру", row.partners))[0],
        )

    if "groups" in provided:
        def _update_groups() -> bool:
            for group_label in ("Группа", "Группы"):
                try:
                    _set_multiselect_list(page, group_label, row.groups)
                    return False
                except RuntimeError:
                    if group_label == "Группы":
                        raise
            return False

        _run_timed_field_update(row, "groups", _update_groups)

    expected_aggregate = _expected_aggregate_name(row)
    nested_provided = _provided_aggregate_nested(provided, frozenset())

    if expected_aggregate:
        _assert_aggregate_active(page, expected_aggregate)
    elif nested_provided:
        _assert_aggregate_block_visible(page)

    if nested_provided:
        modal = page.locator(MODAL_BODY)
        if "account" in provided:
            _run_timed_field_update(
                row,
                "account",
                lambda: (
                    not row.account,
                    _fill_edit_aggregate_field(modal, ("Аккаунт",), row.account),
                )[0],
            )
        if "merchant_id_sbp" in provided:
            _run_timed_field_update(
                row,
                "merchant_id_sbp",
                lambda: (
                    not row.merchant_id_sbp,
                    _fill_edit_aggregate_field(modal, ("MerchantId СБП",), row.merchant_id_sbp),
                )[0],
            )
        if "account_number" in provided:
            _run_timed_field_update(
                row,
                "account_number",
                lambda: (
                    not row.account_number,
                    _fill_edit_aggregate_field(modal, ACCOUNT_NUMBER_LABELS, row.account_number),
                )[0],
            )

    for attr, label in PHASE2_OPTIONAL_TEXT_FIELDS:
        if attr in provided:
            value = getattr(row, attr, "")
            _run_timed_field_update(
                row,
                attr,
                lambda v=value, lbl=label: (
                    not v,
                    _fill_optional_text_by_label(page, lbl, v),
                )[0],
            )
    for attr, label in PHASE2_OPTIONAL_TEXTAREA_FIELDS:
        if attr in provided:
            value = getattr(row, attr, "")
            _run_timed_field_update(
                row,
                attr,
                lambda v=value, lbl=label: (
                    not v,
                    _fill_optional_textarea_by_label(page, lbl, v),
                )[0],
            )
    for attr, label in PHASE2_OPTIONAL_SELECT_FIELDS:
        if attr in provided:
            value = getattr(row, attr, "")
            _run_timed_field_update(
                row,
                attr,
                lambda v=value, lbl=label: (
                    not v,
                    _fill_optional_select_by_label(page, lbl, v),
                )[0],
            )
    if "gender" in provided:
        _run_timed_field_update(
            row,
            "gender",
            lambda: (not row.gender, _fill_gender_radio(page, row.gender))[0],
        )
    if "kyc" in provided:
        _run_timed_field_update(
            row,
            "kyc",
            lambda: (False, _fill_kyc_checkbox(page, row.kyc))[0],
        )


def fill_edit_wallet_form(page, row: EditWalletRow) -> None:
    with _row_timing_step(row, "clear_total"):
        _clear_edit_wallet_fields(page, row)
    with _row_timing_step(row, "update_total"):
        _update_edit_wallet_form(page, row)


def _process_row(
    page,
    row: EditWalletRow,
    *,
    operator_profile: str,
) -> EditWalletRowResult:
    row_started_at = time.perf_counter()
    _log("row_started", card=row.card, extra=f"row={row.row_number}")
    _log(
        "row_intents",
        card=row.card,
        extra=(
            f"row={row.row_number} "
            f"update={sorted(row.provided_columns)} "
            f"clear={sorted(row.cleared_columns)}"
        ),
    )

    try:
        pre_search_started = time.perf_counter()
        pre_search_exists = card_exists_strict(page, row.card)
        pre_search_outcome = "ok" if pre_search_exists else "skip"
        _log_timing_row(
            row,
            "pre_search",
            int((time.perf_counter() - pre_search_started) * 1000),
            pre_search_outcome,
        )
        if not pre_search_exists:
            return _finish_row(
                row,
                make_row_result(
                    row,
                    row_number=row.row_number,
                    result=RESULT_SKIP_NOT_FOUND,
                    comment="карта не найдена в Antares",
                    operator_profile=operator_profile,
                ),
                row_started_at=row_started_at,
            )

        try:
            with _row_timing_step(row, "open_card_strict"):
                open_card_strict(page, row.card)
                _assert_edit_modal(page)
                _log("modal_opened", card=row.card)
        except (OpenCardStageError, RuntimeError) as exc:
            return _finish_row(
                row,
                make_row_result(
                    row,
                    row_number=row.row_number,
                    result=RESULT_FAIL_OPEN_CARD,
                    comment=str(exc),
                    operator_profile=operator_profile,
                ),
                row_started_at=row_started_at,
            )

        try:
            with _row_timing_step(row, "fill_total"):
                fill_edit_wallet_form(page, row)
            _log("form_filled", card=row.card)
        except AggregateNotActiveError as exc:
            return _finish_row(
                row,
                make_row_result(
                    row,
                    row_number=row.row_number,
                    result=RESULT_FAIL_AGGREGATE_NOT_ACTIVE,
                    comment=str(exc),
                    operator_profile=operator_profile,
                ),
                row_started_at=row_started_at,
            )
        except Exception as exc:
            return _finish_row(
                row,
                make_row_result(
                    row,
                    row_number=row.row_number,
                    result=RESULT_FAIL_FILL,
                    comment=str(exc),
                    operator_profile=operator_profile,
                ),
                row_started_at=row_started_at,
            )

        save_total_started = time.perf_counter()
        save_total_outcome = "ok"
        try:
            _log("save_clicked", card=row.card)
            save_outcome = save_add_wallet_modal(page)

            if save_outcome.status == "validation":
                save_total_outcome = "fail"
                _log_timing_row(
                    row,
                    "save_total",
                    int((time.perf_counter() - save_total_started) * 1000),
                    save_total_outcome,
                )
                return _finish_row(
                    row,
                    make_row_result(
                        row,
                        row_number=row.row_number,
                        result=RESULT_FAIL_VALIDATION,
                        comment=save_outcome.detail or "validation error",
                        operator_profile=operator_profile,
                    ),
                    row_started_at=row_started_at,
                )
            if save_outcome.status == "timeout":
                save_total_outcome = "fail"
                _log_timing_row(
                    row,
                    "save_total",
                    int((time.perf_counter() - save_total_started) * 1000),
                    save_total_outcome,
                )
                return _finish_row(
                    row,
                    make_row_result(
                        row,
                        row_number=row.row_number,
                        result=RESULT_FAIL_SAVE_TIMEOUT,
                        comment="modal did not close after save",
                        operator_profile=operator_profile,
                    ),
                    row_started_at=row_started_at,
                )

            _log("modal_closed", card=row.card)
        except Exception:
            save_total_outcome = "fail"
            _log_timing_row(
                row,
                "save_total",
                int((time.perf_counter() - save_total_started) * 1000),
                save_total_outcome,
            )
            raise
        _log_timing_row(
            row,
            "save_total",
            int((time.perf_counter() - save_total_started) * 1000),
            save_total_outcome,
        )

        post_search_started = time.perf_counter()
        post_search_exists = card_exists_strict(page, row.card)
        post_search_outcome = "ok" if post_search_exists else "fail"
        _log_timing_row(
            row,
            "post_search",
            int((time.perf_counter() - post_search_started) * 1000),
            post_search_outcome,
        )
        _log("post_search", card=row.card)
        if post_search_exists:
            _log("row_result", card=row.card, extra="OK")
            return _finish_row(
                row,
                make_row_result(
                    row,
                    row_number=row.row_number,
                    result=RESULT_OK,
                    comment=build_success_comment(row),
                    operator_profile=operator_profile,
                ),
                row_started_at=row_started_at,
            )

        return _finish_row(
            row,
            make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_FAIL_NOT_FOUND,
                comment="card not found after save",
                operator_profile=operator_profile,
            ),
            row_started_at=row_started_at,
        )
    except Exception as exc:
        log.exception(f"{LOG_PREFIX} stage=row_result card_tail={mask_card(row.card)} error={exc}")
        return _finish_row(
            row,
            make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_FAIL_TECHNICAL,
                comment=str(exc),
                operator_profile=operator_profile,
            ),
            row_started_at=row_started_at,
        )


def run(
    file_path: str,
    cfg: RunConfig,
    *,
    result_file_path: str | None = None,
) -> tuple[str, EditWalletBatchSummary]:
    batch_started_at = time.perf_counter()
    batch_outcome = "ok"
    try:
        with _batch_timing_step("contract_parse"):
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
            out_path = result_file_path or os.path.join(
                "/tmp/wallet_editor",
                "wallet_edit_result_empty.xlsx",
            )
            with _batch_timing_step("result_write"):
                write_result_excel(results, out_path)
            return out_path, summary

        slow_mo = wallet_editor_playwright_slow_mo_ms()
        _log("batch_summary", extra=f"rows={len(batch.rows)}")

        with sync_playwright() as p:
            browser = None
            context = None
            page = None
            try:
                with _batch_timing_step("browser_auth"):
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

                with _batch_timing_step("rows_total"):
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
        with _batch_timing_step("result_write"):
            write_result_excel(results, result_file_path)
        _log(
            "batch_summary",
            extra=f"total={summary.total} ok={summary.ok} skip={summary.skip} fail={summary.fail}",
        )
        return result_file_path, summary
    except Exception:
        batch_outcome = "fail"
        raise
    finally:
        _log_timing_batch(
            "batch_total",
            int((time.perf_counter() - batch_started_at) * 1000),
            batch_outcome,
        )
