"""WalletEditor Add Wallet — Antares UI automation (Phase 1)."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from automation.add_wallet_contract import (
    LOG_PREFIX,
    AddWalletBatchInput,
    AddWalletBatchSummary,
    AddWalletRow,
    AddWalletRowResult,
    PreparedAddWalletRow,
    RESULT_DRY_RUN,
    RESULT_FAIL_FILL,
    RESULT_FAIL_NOT_FOUND,
    RESULT_FAIL_OPEN_MODAL,
    RESULT_FAIL_SAVE_TIMEOUT,
    RESULT_FAIL_TECHNICAL,
    RESULT_FAIL_VALIDATION,
    RESULT_OK,
    RESULT_SKIP_DUP_CARD,
    RESULT_SKIP_DUP_FILE,
    make_row_result,
    prepare_add_wallet_batch,
    write_result_excel,
)
from automation.audit import (
    log,
    mask_card,
    normalize_card_digits,
    row_matches_card_strict,
)
from automation.engine import _ensure_logged_in
from automation.runtime import RunConfig, require_wallet_editor_antares_credentials, wallet_editor_playwright_slow_mo_ms
from core.playwright_cleanup import close_playwright_stack

WALLET_URL = "https://antares.plus/lkcard/#/wallet"
CARD_INPUT = 'input[placeholder="Карта"]'
APPLY_BUTTON = 'button:has-text("Применить")'
ROW_SELECTOR = "tr.pointer"
MODAL_ROOT = "#wallet-add-modal"
MODAL_BODY = "#wallet-add-modal___BV_modal_body_"
MODAL_HEADER = "#wallet-add-modal___BV_modal_header_"
SAVE_BUTTON = 'button:has-text("Сохранить")'
CREATE_TITLE = "Добавление кошелька"
EMPTY_TOTAL_RE = re.compile(r"Всего:\s*0")

_PAGE_READY_TIMEOUT_MS = 15_000
_SEARCH_SETTLE_MS = 2_000
_ROW_READ_TIMEOUT_MS = 500
_SAVE_WAIT_TIMEOUT_MS = 10_000
_MODAL_OPEN_TIMEOUT_MS = 10_000
_AGGREGATE_BLOCK_WAIT_MS = 5_000
_POLL_MS = 150

ACCOUNT_NUMBER_LABELS = (
    "Номер счёта",
    "Номер счета",
    "Номер расчёта",
    "Номер расчета",
)
AGGREGATE_VISIBILITY_LABELS = (
    "Аккаунт",
    "Карта",
    "MerchantId СБП",
    *ACCOUNT_NUMBER_LABELS,
)
_AGGREGATE_UNIQUE_VISIBILITY_LABELS = (
    "Аккаунт",
    "MerchantId СБП",
    *ACCOUNT_NUMBER_LABELS,
)
_DUPLICATE_TOP_LEVEL_LABELS = frozenset({"Карта", "Телефон"})


@dataclass
class SaveWaitOutcome:
    status: str
    detail: str = ""


def _log(stage: str, *, card: str | None = None, extra: str = "") -> None:
    tail = f" card_tail={mask_card(card)}" if card else ""
    suffix = f" {extra}" if extra else ""
    log.info(f"{LOG_PREFIX} stage={stage}{tail}{suffix}")


def _normalize_label_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _labels_match(actual: str, expected: str) -> bool:
    return _normalize_label_text(actual) == _normalize_label_text(expected)


def goto_wallet_page(page: Page) -> None:
    page.goto(WALLET_URL)
    page.locator(CARD_INPUT).wait_for(state="visible", timeout=_PAGE_READY_TIMEOUT_MS)


def _resolve_search_card(card: str) -> str:
    search = normalize_card_digits(card)
    if search:
        return search
    raw = (card or "").strip()
    if raw:
        return raw
    raise ValueError("empty card")


def _submit_card_filter(page: Page, card: str) -> None:
    search_card = _resolve_search_card(card)
    card_input = page.locator(CARD_INPUT)
    card_input.fill("")
    card_input.fill(search_card)
    card_input.press("Enter")

    deadline = time.monotonic() + _SEARCH_SETTLE_MS / 1000.0
    while time.monotonic() < deadline:
        if page.locator(ROW_SELECTOR).count() > 0:
            return
        page.wait_for_timeout(_POLL_MS)

    try:
        page.locator(APPLY_BUTTON).click(timeout=2_000)
    except PlaywrightTimeoutError:
        pass


def _page_shows_empty_results(page: Page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=1_000)
    except Exception:
        return False
    return bool(EMPTY_TOTAL_RE.search(body))


def card_exists_strict(page: Page, card: str) -> bool:
    _log("pre_search", card=card)
    _submit_card_filter(page, card)
    page.wait_for_timeout(_POLL_MS)

    if _page_shows_empty_results(page):
        rows = page.locator(ROW_SELECTOR)
        if rows.count() == 0:
            _log("duplicate_found", card=card, extra="no rows")
            return False

    rows = page.locator(ROW_SELECTOR)
    card_digits = normalize_card_digits(card)
    count = rows.count()
    for i in range(count):
        try:
            row_text = rows.nth(i).inner_text(timeout=_ROW_READ_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            continue
        if row_matches_card_strict(row_text, card_digits):
            _log("duplicate_found", card=card, extra="strict match")
            return True
    return False


def open_add_wallet_modal(page: Page) -> None:
    for selector in (
        'button:has-text("Добавление кошелька")',
        'button:has-text("Добавить кошел")',
    ):
        btn = page.locator(selector).first
        if btn.count() > 0:
            btn.click(timeout=5_000)
            page.locator(MODAL_BODY).wait_for(state="visible", timeout=_MODAL_OPEN_TIMEOUT_MS)
            return
    raise RuntimeError("кнопка открытия формы Add Wallet не найдена")


def _assert_create_modal(page: Page) -> None:
    header = page.locator(MODAL_HEADER)
    text = header.inner_text(timeout=3_000)
    if CREATE_TITLE not in text:
        raise RuntimeError(f"ожидалась модалка «{CREATE_TITLE}», получено: {text!r}")


def _find_text_input_by_label_in(scope, label_text: str):
    rows = scope.locator("div.row")
    for i in range(rows.count()):
        row = rows.nth(i)
        if not row.is_visible():
            continue
        labels = row.locator("label")
        if labels.count() == 0:
            continue
        try:
            label = labels.first.inner_text(timeout=1_000).strip()
        except Exception:
            continue
        if not _labels_match(label, label_text):
            continue
        inputs = row.locator("input[type='text'], input:not([type='checkbox']):not([type='radio'])")
        if inputs.count() == 0:
            continue
        return inputs.first
    return None


def _find_all_text_inputs_by_label_in(scope, label_text: str) -> list:
    matches = []
    rows = scope.locator("div.row")
    for i in range(rows.count()):
        row = rows.nth(i)
        try:
            if not row.is_visible():
                continue
        except Exception:
            continue
        labels = row.locator("label")
        if labels.count() == 0:
            continue
        try:
            label = labels.first.inner_text(timeout=1_000).strip()
        except Exception:
            continue
        if not _labels_match(label, label_text):
            continue
        inputs = row.locator("input[type='text'], input:not([type='checkbox']):not([type='radio'])")
        if inputs.count() == 0:
            continue
        matches.append(inputs.first)
    return matches


def _find_aggregate_field_input(modal, label_text: str):
    matches = _find_all_text_inputs_by_label_in(modal, label_text)
    if not matches:
        return None
    if label_text in _DUPLICATE_TOP_LEVEL_LABELS and len(matches) > 1:
        return matches[-1]
    return matches[-1]


def _find_text_input_by_label(page: Page, label_text: str):
    modal = page.locator(MODAL_BODY)
    return _find_text_input_by_label_in(modal, label_text)


def _find_select_by_label(page: Page, label_text: str):
    modal = page.locator(MODAL_BODY)
    rows = modal.locator("div.row")
    for i in range(rows.count()):
        row = rows.nth(i)
        if not row.is_visible():
            continue
        labels = row.locator("label")
        if labels.count() == 0:
            continue
        try:
            label = labels.first.inner_text(timeout=1_000).strip()
        except Exception:
            continue
        if not _labels_match(label, label_text):
            continue
        selects = row.locator("select")
        if selects.count() == 0:
            continue
        return selects.first
    return None


def _find_multiselect_by_label(page: Page, label_text: str):
    modal = page.locator(MODAL_BODY)
    rows = modal.locator("div.row")
    for i in range(rows.count()):
        row = rows.nth(i)
        if not row.is_visible():
            continue
        labels = row.locator("label")
        if labels.count() == 0:
            continue
        try:
            label = labels.first.inner_text(timeout=1_000).strip()
        except Exception:
            continue
        if not _labels_match(label, label_text):
            continue
        multiselect = row.locator(
            "xpath=.//div[contains(@class,'multiselect') and not(contains(@class,'multiselect__tags'))]"
        )
        if multiselect.count() == 0:
            return None
        return multiselect.first
    return None


def _fill_text_by_label(page: Page, label: str, value: str) -> None:
    if not value:
        return
    field = _find_text_input_by_label(page, label)
    if field is None:
        raise RuntimeError(f"поле не найдено: {label}")
    field.fill("")
    field.fill(value)


def _select_by_label(page: Page, label: str, value: str) -> None:
    if not value:
        return
    select = _find_select_by_label(page, label)
    if select is None:
        raise RuntimeError(f"select не найден: {label}")
    try:
        select.select_option(label=value)
    except Exception:
        select.select_option(value=value)


def _multiselect_add_option(page: Page, multiselect, option: str) -> None:
    option_norm = (option or "").strip()
    if not option_norm:
        return

    multiselect.scroll_into_view_if_needed()
    multiselect.wait_for(state="visible", timeout=3_000)
    try:
        multiselect.click()
    except Exception:
        multiselect.click(force=True)

    dropdown = page.locator(".multiselect__content-wrapper:visible")
    dropdown.wait_for(state="visible", timeout=5_000)
    options = dropdown.locator("li")

    found = None
    last_count = -1
    for _ in range(25):
        visible_count = options.count()
        for i in range(visible_count):
            option_el = options.nth(i)
            text = option_el.inner_text().strip()
            if option_norm.lower() in text.lower():
                found = option_el
                break
        if found:
            break
        if visible_count == last_count:
            break
        last_count = visible_count
        dropdown.evaluate("el => el.scrollTop = el.scrollHeight")
        page.wait_for_timeout(300)

    if not found:
        raise RuntimeError(f"опция multiselect не найдена: {option_norm}")

    found.scroll_into_view_if_needed()
    try:
        found.click()
    except Exception:
        found.click(force=True)


def _fill_multiselect_list(page: Page, label: str, raw: str) -> None:
    values = [part.strip() for part in (raw or "").split(";") if part.strip()]
    if not values:
        return
    multiselect = _find_multiselect_by_label(page, label)
    if multiselect is None:
        raise RuntimeError(f"multiselect не найден: {label}")
    for value in values:
        _multiselect_add_option(page, multiselect, value)


def checkbox_label_matches(text: str, aggregate_name: str) -> bool:
    return _labels_match(text, aggregate_name)


def _checkbox_for_label(label_el):
    checkbox = label_el.locator(
        "xpath=ancestor::div[contains(@class,'form-check') or contains(@class,'custom-control')]"
        "//input[@type='checkbox']"
    )
    if checkbox.count() == 0:
        checkbox = label_el.locator("xpath=preceding::input[@type='checkbox'][1]")
    if checkbox.count() == 0:
        checkbox = label_el.locator("xpath=following::input[@type='checkbox'][1]")
    return checkbox


def _find_aggregate_checkbox_label(modal, aggregate_name: str):
    labels = modal.locator("label")
    for i in range(labels.count()):
        label_el = labels.nth(i)
        try:
            text = label_el.inner_text(timeout=500).strip()
        except Exception:
            continue
        if not checkbox_label_matches(text, aggregate_name):
            continue
        if _checkbox_for_label(label_el).count() > 0:
            return label_el
    return None


def select_single_aggregate_checkbox(page: Page, aggregate_name: str) -> None:
    modal = page.locator(MODAL_BODY)
    label_el = _find_aggregate_checkbox_label(modal, aggregate_name)
    if label_el is None:
        raise RuntimeError(f"aggregate checkbox not found: {aggregate_name}")
    checkbox = _checkbox_for_label(label_el)
    if checkbox.count() == 0:
        raise RuntimeError(f"aggregate checkbox not found: {aggregate_name}")
    if not checkbox.first.is_checked():
        checkbox.first.check(force=True)


def _detect_aggregate_expansion(modal) -> str | None:
    for label in _AGGREGATE_UNIQUE_VISIBILITY_LABELS:
        field = _find_text_input_by_label_in(modal, label)
        if field is None:
            continue
        try:
            if field.is_visible():
                return label
        except Exception:
            return label

    card_matches = _find_all_text_inputs_by_label_in(modal, "Карта")
    if len(card_matches) >= 2:
        try:
            if card_matches[-1].is_visible():
                return "Карта"
        except Exception:
            return "Карта"
    return None


def wait_for_aggregate_fields_visible(page: Page, aggregate_name: str) -> None:
    modal = page.locator(MODAL_BODY)
    deadline = time.monotonic() + _AGGREGATE_BLOCK_WAIT_MS / 1000.0
    while time.monotonic() < deadline:
        detected = _detect_aggregate_expansion(modal)
        if detected:
            _log(
                "aggregate_fields_visible",
                extra=f"aggregate={aggregate_name} label={detected}",
            )
            return
        page.wait_for_timeout(_POLL_MS)
    _log("aggregate_fields_not_visible", extra=f"aggregate={aggregate_name}")
    raise RuntimeError(f"aggregate fields not visible: {aggregate_name}")


def fill_aggregate_field_if_present(
    modal,
    labels: tuple[str, ...],
    value: str,
    *,
    required: bool = False,
) -> None:
    field = None
    matched_label: str | None = None
    for label in labels:
        field = _find_aggregate_field_input(modal, label)
        if field is not None:
            matched_label = label
            break
    if field is None:
        return
    if not value:
        if required:
            raise RuntimeError(f"поле {matched_label} требует значение")
        return
    try:
        field.fill("")
        field.fill(value)
    except Exception as exc:
        if required:
            raise RuntimeError(f"не удалось заполнить поле {matched_label}: {exc}") from exc
        raise


def fill_aggregate_modal_fields(modal, row: AddWalletRow) -> None:
    fill_aggregate_field_if_present(modal, ("Карта",), row.card, required=True)
    fill_aggregate_field_if_present(modal, ("Телефон",), row.phone, required=True)
    fill_aggregate_field_if_present(modal, ("Аккаунт",), row.account)
    fill_aggregate_field_if_present(modal, ("MerchantId СБП",), row.merchant_id_sbp)
    fill_aggregate_field_if_present(modal, ACCOUNT_NUMBER_LABELS, row.account_number)


def _fill_single_aggregate(page: Page, row: AddWalletRow) -> None:
    if not row.aggregate:
        return
    select_single_aggregate_checkbox(page, row.aggregate)
    _log("aggregate_selected", extra=f"aggregate={row.aggregate}")
    wait_for_aggregate_fields_visible(page, row.aggregate)
    modal = page.locator(MODAL_BODY)
    _log("aggregate_fill_started", extra=f"aggregate={row.aggregate}")
    fill_aggregate_modal_fields(modal, row)
    _log("aggregate_fill_completed", extra=f"aggregate={row.aggregate}")


def fill_add_wallet_form(page: Page, row: AddWalletRow) -> None:
    _fill_text_by_label(page, "Карта", row.card)
    _fill_text_by_label(page, "Телефон", row.phone)
    _select_by_label(page, "Направление", row.direction)
    _select_by_label(page, "Статус", row.status)
    _select_by_label(page, "Состояние", row.state)
    _select_by_label(page, "Пул", row.pool)
    for group_label in ("Группа", "Группы"):
        try:
            _fill_multiselect_list(page, group_label, row.groups)
            break
        except RuntimeError:
            if group_label == "Группы":
                raise
    _fill_multiselect_list(page, "Привязан к партнеру", row.partners)
    _fill_single_aggregate(page, row)


def detect_add_wallet_validation_error(page: Page) -> str | None:
    modal = page.locator(MODAL_BODY)
    if not modal.is_visible():
        return None

    selectors = (
        ".invalid-feedback",
        ".text-danger",
        ".alert-danger",
        ".alert",
        ".toast-body",
        "[role='alert']",
    )
    for selector in selectors:
        loc = modal.locator(selector)
        for i in range(loc.count()):
            item = loc.nth(i)
            try:
                if item.is_visible():
                    text = item.inner_text(timeout=500).strip()
                    if text:
                        return text
            except Exception:
                continue

    for selector in selectors:
        loc = page.locator(selector)
        for i in range(min(loc.count(), 3)):
            item = loc.nth(i)
            try:
                if item.is_visible():
                    text = item.inner_text(timeout=500).strip()
                    if text:
                        return text
            except Exception:
                continue
    return None


def wait_modal_closed_or_error(page: Page, *, timeout_ms: int = _SAVE_WAIT_TIMEOUT_MS) -> SaveWaitOutcome:
    modal = page.locator(MODAL_BODY)
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if not modal.is_visible():
            return SaveWaitOutcome(status="closed")
        error_text = detect_add_wallet_validation_error(page)
        if error_text:
            return SaveWaitOutcome(status="validation", detail=error_text)
        page.wait_for_timeout(_POLL_MS)
    if modal.is_visible():
        return SaveWaitOutcome(status="timeout")
    return SaveWaitOutcome(status="closed")


def save_add_wallet_modal(page: Page) -> SaveWaitOutcome:
    page.locator(SAVE_BUTTON).click(timeout=5_000)
    return wait_modal_closed_or_error(page)


def _process_row(
    page: Page,
    row: AddWalletRow,
    *,
    cfg: RunConfig,
    operator_profile: str,
) -> AddWalletRowResult:
    _log("row_started", card=row.card, extra=f"row={row.row_number}")

    try:
        if card_exists_strict(page, row.card):
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_SKIP_DUP_CARD,
                comment="карта уже существует в Antares",
                operator_profile=operator_profile,
                dry_run=cfg.dry_run,
            )

        if cfg.dry_run:
            _log("dry_run_would_create", card=row.card)
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_DRY_RUN,
                comment="dry-run: карта не найдена, создание пропущено",
                operator_profile=operator_profile,
                dry_run=True,
            )

        try:
            open_add_wallet_modal(page)
            _assert_create_modal(page)
            _log("modal_opened", card=row.card)
        except Exception as exc:
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_FAIL_OPEN_MODAL,
                comment=str(exc),
                operator_profile=operator_profile,
                dry_run=False,
            )

        try:
            fill_add_wallet_form(page, row)
            _log("form_filled", card=row.card)
        except Exception as exc:
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_FAIL_FILL,
                comment=str(exc),
                operator_profile=operator_profile,
                dry_run=False,
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
                dry_run=False,
            )
        if save_outcome.status == "timeout":
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_FAIL_SAVE_TIMEOUT,
                comment="modal did not close after save",
                operator_profile=operator_profile,
                dry_run=False,
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
                dry_run=False,
            )

        return make_row_result(
            row,
            row_number=row.row_number,
            result=RESULT_FAIL_NOT_FOUND,
            comment="card not found after save",
            operator_profile=operator_profile,
            dry_run=False,
        )
    except Exception as exc:
        log.exception(f"{LOG_PREFIX} stage=row_result card_tail={mask_card(row.card)} error={exc}")
        return make_row_result(
            row,
            row_number=row.row_number,
            result=RESULT_FAIL_TECHNICAL,
            comment=str(exc),
            operator_profile=operator_profile,
            dry_run=cfg.dry_run,
        )


def run(
    file_path: str,
    cfg: RunConfig,
    *,
    result_file_path: str | None = None,
) -> tuple[str, AddWalletBatchSummary]:
    _log("contract_validated", extra=f"file={file_path}")
    require_wallet_editor_antares_credentials(cfg)
    profile = (cfg.operator_profile or "unknown").strip()

    batch = prepare_add_wallet_batch(file_path, dry_run=cfg.dry_run)
    summary = AddWalletBatchSummary(dry_run=cfg.dry_run)
    results: list[AddWalletRowResult] = []

    for invalid in batch.invalid_rows:
        row_number = invalid.row.row_number if invalid.row else 0
        item = make_row_result(
            invalid.row,
            row_number=row_number,
            result=invalid.result,
            comment=invalid.comment,
            operator_profile=profile,
            dry_run=cfg.dry_run,
        )
        results.append(item)
        summary.record(invalid.result)

    if not batch.rows and not results:
        out_path = result_file_path or os.path.join("/tmp/wallet_editor", "wallet_add_result_empty.xlsx")
        write_result_excel(results, out_path)
        return out_path, summary

    slow_mo = wallet_editor_playwright_slow_mo_ms()
    _log("batch_summary", extra=f"rows={len(batch.rows)} dry_run={cfg.dry_run}")

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
                item = _process_row(page, row, cfg=cfg, operator_profile=profile)
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
            f"wallet_add_result_{profile}.xlsx",
        )
    write_result_excel(results, result_file_path)
    _log(
        "batch_summary",
        extra=(
            f"total={summary.total} ok={summary.ok} skip={summary.skip} "
            f"fail={summary.fail} dry={summary.dry_run_would_create}"
        ),
    )
    return result_file_path, summary
