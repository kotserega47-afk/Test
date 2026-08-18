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
    RESULT_STOP_BEFORE_SAVE,
    make_row_result,
    prepare_add_wallet_batch,
    write_result_excel,
)
from automation.audit import (
    log,
    mask_card,
    normalize_card_digits,
)
from automation.engine import (
    CardSearchUnsettledError,
    _ensure_logged_in,
    card_exists_strict as engine_card_exists_strict,
)
from automation.runtime import (
    RunConfig,
    require_wallet_editor_antares_credentials,
    wallet_editor_playwright_slow_mo_ms,
)
from automation.wallet_form_helpers import (
    AggregateSwitchError,
    FieldNotEditableError,
    _locator_for_labeled_input,
    clear_locator_text as _clear_locator_text,
    fill_locator_confirmed,
    fill_locator_text as _fill_locator_text,
    opportunistic_nested_card_item,
    requested_nested_fields_for_add_row,
    resolve_nested_field_locator,
    switch_to_single_aggregate,
    time_form_step,
    wait_for_requested_nested_fields,
)
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


_ADD_WALLET_TIMING_PROFILE = "wallet_editor"
_ADD_WALLET_TIMING_SCOPE = "add_wallet"


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


def _clear_text_by_label(page: Page, label: str, *, log_prefix: str = LOG_PREFIX) -> None:
    field = _find_text_input_by_label(page, label)
    if field is None:
        raise RuntimeError(f"field not found: {label}")
    _clear_locator_text(field, label, log_prefix=log_prefix)


def _clear_textarea_by_label(page: Page, label: str, *, log_prefix: str = LOG_PREFIX) -> None:
    field = _find_textarea_by_label(page, label)
    if field is None:
        raise RuntimeError(f"field not found: {label}")
    _clear_locator_text(field, label, log_prefix=log_prefix)


def _clear_multiselect_list(page: Page, label: str, *, column: str) -> bool:
    """Clear all multiselect chips. Returns True when already empty (no-op)."""
    multiselect = _find_multiselect_by_label(page, label)
    if multiselect is None:
        raise RuntimeError(f"multiselect не найден: {label}")

    selected = _get_selected_multiselect_labels(multiselect)
    if not selected:
        return True

    for _ in range(10):
        selected = _get_selected_multiselect_labels(multiselect)
        if not selected:
            return False
        for chip_text in list(selected):
            if not _multiselect_remove_label(multiselect, chip_text):
                raise RuntimeError(f"failed to clear multiselect: {column}")
        page.wait_for_timeout(200)

    remaining = _get_selected_multiselect_labels(multiselect)
    if remaining:
        raise RuntimeError(f"failed to clear multiselect: {column}")
    return False


def _uncheck_kyc_checkbox(page: Page, *, log_prefix: str = LOG_PREFIX) -> bool:
    modal = page.locator(MODAL_BODY)
    checkbox = _find_kyc_checkbox(modal)
    if checkbox is None:
        raise RuntimeError("KYC checkbox not found")

    try:
        checked = checkbox.is_checked()
    except Exception as exc:
        raise RuntimeError("KYC checkbox not found") from exc

    if not checked:
        return True

    try:
        checkbox.uncheck(force=True)
    except Exception:
        try:
            checkbox.click(force=True)
        except Exception as exc:
            raise RuntimeError("KYC checkbox not found") from exc

    return False


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
    """Strict search via engine: fingerprint settle, second Enter without refill.

    Raises ``CardSearchUnsettledError`` when the UI cannot produce a trustworthy
    result (distinct from confirmed absence).
    """
    _log("pre_search", card=card)
    found = engine_card_exists_strict(page, card)
    _log(
        "duplicate_found" if found else "duplicate_absent",
        card=card,
        extra="strict match" if found else "confirmed absent",
    )
    return found


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
        inputs = row.locator(
            "input[type='text'], input[type='password'], "
            "input:not([type='checkbox']):not([type='radio'])"
        )
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
        inputs = row.locator(
            "input[type='text'], input[type='password'], "
            "input:not([type='checkbox']):not([type='radio'])"
        )
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


def _find_textarea_by_label_in(scope, label_text: str):
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
        textareas = row.locator("textarea")
        if textareas.count() == 0:
            continue
        return textareas.first
    return None


def _find_textarea_by_label(page: Page, label_text: str):
    modal = page.locator(MODAL_BODY)
    return _find_textarea_by_label_in(modal, label_text)


def _fill_optional_text_by_label(page: Page, label: str, value: str) -> None:
    if not value:
        return
    field = _find_text_input_by_label(page, label)
    if field is None:
        return
    _fill_locator_text(field, label, value)


def _fill_optional_textarea_by_label(page: Page, label: str, value: str) -> None:
    if not value:
        return
    field = _find_textarea_by_label(page, label)
    if field is None:
        return
    _fill_locator_text(field, label, value)


def _fill_optional_select_by_label(page: Page, label: str, value: str) -> None:
    if not value:
        return
    select = _find_select_by_label(page, label)
    if select is None:
        return
    try:
        select.select_option(label=value)
    except Exception:
        try:
            select.select_option(value=value)
        except Exception:
            return


_GENDER_VALUE_TO_OPTION = {
    "м": "М",
    "m": "М",
    "male": "М",
    "ж": "Ж",
    "f": "Ж",
    "female": "Ж",
}
_KYC_TRUE_VALUES = frozenset({"1", "true", "yes", "да", "y", "checked"})
_KYC_LABELS = ("KYC", "КУС", "Кус")
_KYC_EXACT_LABELS = frozenset(_normalize_label_text(label) for label in _KYC_LABELS)
_KYC_SEARCH_ANCHOR_LABELS = (
    "Приоритет для выплат",
    "Длина очереди",
    "Глубина очереди",
    "Bakai customer_id",
)
_POST_AGGREGATE_SECTION_LABELS = (
    "Привязан к партнеру",
    "Группа",
    "Группы",
)

PHASE2_OPTIONAL_TEXT_FIELDS = (
    ("surname", "Фамилия"),
    ("first_name", "Имя"),
    ("patronymic", "Отчество"),
    ("login", "Логин"),
    ("password", "Пароль"),
    ("password_extra", "Дополнительный пароль"),
    ("security_question", "Контрольный вопрос"),
    ("bank_card_sim", "Sim банкпозиция карты"),
    ("balance", "Баланс"),
    ("merch", "Мерч"),
    ("marker", "Маркер"),
    ("cluster_sim", "Кластер sim"),
    ("cluster_phone", "Номер телефона внутри кластера"),
    ("cluster_sim_slot", "Номер слота симкарты внутри телефона"),
    ("server_id", "Сервер ID"),
    ("queue_length", "Длина очереди"),
    ("queue_depth", "Глубина очереди"),
    ("bakai_customer_id", "Bakai customer_id"),
)
PHASE2_OPTIONAL_TEXTAREA_FIELDS = (
    ("comment_service", "Комментарий к сервисные работы"),
    ("comment_deleted_account", "Комментарий к удаленный аккаунт"),
    ("comment", "Комментарий"),
)
PHASE2_OPTIONAL_SELECT_FIELDS = (
    ("gateway", "Шлюз"),
    ("topup_method", "Метод пополнения"),
    ("role", "Роль"),
    ("ours", "Наш"),
    ("cluster", "Кластер"),
    ("payout_priority", "Приоритет для выплат"),
)

# Lower-form control types verified against Antares «Добавление кошелька» modal UI.
ADD_WALLET_LOWER_FORM_CONTROL_TYPES: dict[str, tuple[str, str]] = {
    "gateway": ("Шлюз", "select"),
    "role": ("Роль", "select"),
    "marker": ("Маркер", "text"),
    "pool": ("Пул", "select"),
    "cluster_sim": ("Кластер sim", "text"),
    "server_id": ("Сервер ID", "text"),
    "ours": ("Наш", "select"),
    "cluster": ("Кластер", "select"),
    "payout_priority": ("Приоритет для выплат", "select"),
}


def normalize_gender_option(value: str) -> str | None:
    return _GENDER_VALUE_TO_OPTION.get((value or "").strip().casefold())


def is_kyc_true(value: str) -> bool:
    return (value or "").strip().casefold() in _KYC_TRUE_VALUES


def _fill_gender_radio(page: Page, value: str) -> None:
    if not value:
        return
    option = normalize_gender_option(value)
    if option is None:
        raise RuntimeError(f"неизвестное значение gender: {value}")
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
            row_label = labels.first.inner_text(timeout=1_000).strip()
        except Exception:
            continue
        if not _labels_match(row_label, "Пол"):
            continue
        for j in range(labels.count()):
            option_label_el = labels.nth(j)
            try:
                option_text = option_label_el.inner_text(timeout=500).strip()
            except Exception:
                continue
            if not _labels_match(option_text, option):
                continue
            option_label_el.click()
            return
        raise RuntimeError(f"radio gender не найден: {option}")
    raise RuntimeError("поле gender (Пол) не найдено")


def _find_checkbox_by_exact_label(modal, label_text: str):
    labels = modal.locator("label")
    for i in range(labels.count()):
        label_el = labels.nth(i)
        try:
            text = label_el.inner_text(timeout=500).strip()
        except Exception:
            continue
        if not _labels_match(text, label_text):
            continue
        checkbox = _checkbox_for_label(label_el)
        if checkbox.count() > 0:
            return checkbox.first
    return None


def _row_label_texts(row) -> list[str]:
    texts: list[str] = []
    labels = row.locator("label")
    for i in range(labels.count()):
        label_el = labels.nth(i)
        try:
            texts.append(label_el.inner_text(timeout=500).strip())
        except Exception:
            continue
    return texts


def _row_has_label(row, label_text: str) -> bool:
    for text in _row_label_texts(row):
        if _labels_match(text, label_text):
            return True
    return False


def _is_kyc_label_text(text: str) -> bool:
    return _normalize_label_text(text) in _KYC_EXACT_LABELS


def _find_kyc_search_start_row_index(modal) -> int:
    rows = modal.locator("div.row")
    count = rows.count()

    for i in range(count):
        row = rows.nth(i)
        for anchor in _KYC_SEARCH_ANCHOR_LABELS:
            if _row_has_label(row, anchor):
                return i

    last_post_section = -1
    for i in range(count):
        row = rows.nth(i)
        for section_label in _POST_AGGREGATE_SECTION_LABELS:
            if _row_has_label(row, section_label):
                last_post_section = i
    if last_post_section >= 0:
        return last_post_section + 1

    return count


def _kyc_checkbox_for_label(label_el):
    checkbox = label_el.locator(
        "xpath=ancestor::div[contains(@class,'form-check') or contains(@class,'custom-control')]"
        "[1]//input[@type='checkbox']"
    )
    if checkbox.count() > 0:
        return checkbox.first

    checkbox = label_el.locator("input[type='checkbox']")
    if checkbox.count() > 0:
        return checkbox.first

    parent = label_el.locator("xpath=..")
    checkbox = parent.locator("input[type='checkbox']")
    if checkbox.count() > 0:
        return checkbox.first

    checkbox = label_el.locator(
        "xpath=preceding-sibling::input[@type='checkbox'][1] | "
        "following-sibling::input[@type='checkbox'][1]"
    )
    if checkbox.count() > 0:
        return checkbox.first

    return None


def _find_kyc_checkbox(modal):
    start_index = _find_kyc_search_start_row_index(modal)
    rows = modal.locator("div.row")
    count = rows.count()
    if start_index >= count:
        return None

    for preferred in _KYC_LABELS:
        normalized_preferred = _normalize_label_text(preferred)
        for i in range(start_index, count):
            row = rows.nth(i)
            labels = row.locator("label")
            for j in range(labels.count()):
                label_el = labels.nth(j)
                try:
                    text = label_el.inner_text(timeout=500).strip()
                except Exception:
                    continue
                if _normalize_label_text(text) != normalized_preferred:
                    continue
                checkbox = _kyc_checkbox_for_label(label_el)
                if checkbox is not None:
                    return checkbox
    return None


def _fill_kyc_checkbox(page: Page, value: str) -> None:
    value_repr = (value or "").strip()
    if not value_repr or not is_kyc_true(value_repr):
        return

    modal = page.locator(MODAL_BODY)
    checkbox = _find_kyc_checkbox(modal)
    if checkbox is None:
        _log(
            "kyc_not_found",
            extra=f"kyc_value={value_repr!r} kyc_control_found=false",
        )
        return

    try:
        initial_checked = checkbox.is_checked()
    except Exception:
        initial_checked = False

    _log(
        "kyc_control_found",
        extra=(
            f"kyc_value={value_repr!r} kyc_control_found=true "
            f"kyc_initial_checked={initial_checked}"
        ),
    )

    if not initial_checked:
        try:
            checkbox.check(force=True)
        except Exception:
            try:
                checkbox.click(force=True)
            except Exception as exc:
                _log(
                    "kyc_not_found",
                    extra=f"kyc_value={value_repr!r} kyc_check_failed={exc}",
                )
                return

    try:
        checked = checkbox.is_checked()
    except Exception:
        checked = True

    _log(
        "kyc_checked",
        extra=(
            f"kyc_value={value_repr!r} kyc_initial_checked={initial_checked} "
            f"kyc_checked={checked}"
        ),
    )


def _fill_phase2_top_level_fields(page: Page, row: AddWalletRow) -> None:
    for attr, label in PHASE2_OPTIONAL_TEXT_FIELDS:
        _fill_optional_text_by_label(page, label, getattr(row, attr, ""))
    for attr, label in PHASE2_OPTIONAL_TEXTAREA_FIELDS:
        _fill_optional_textarea_by_label(page, label, getattr(row, attr, ""))
    for attr, label in PHASE2_OPTIONAL_SELECT_FIELDS:
        _fill_optional_select_by_label(page, label, getattr(row, attr, ""))
    _fill_gender_radio(page, row.gender)
    _fill_kyc_checkbox(page, row.kyc)


def _fill_text_by_label(page: Page, label: str, value: str) -> None:
    if not value:
        return
    field = _find_text_input_by_label(page, label)
    if field is None:
        raise RuntimeError(f"поле не найдено: {label}")
    _fill_locator_text(field, label, value)


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


def _multiselect_value_matches_chip(chip_text: str, value: str) -> bool:
    value_norm = (value or "").strip()
    if not value_norm:
        return False
    chip_norm = (chip_text or "").strip()
    return value_norm.lower() in chip_norm.lower()


def _multiselect_value_already_selected(selected: list[str], value: str) -> bool:
    return any(_multiselect_value_matches_chip(text, value) for text in selected)


def _chip_matches_any_desired(chip_text: str, desired_values: list[str]) -> bool:
    return any(_multiselect_value_matches_chip(chip_text, value) for value in desired_values)


def _multiselect_selection_matches_desired(selected: list[str], desired_values: list[str]) -> bool:
    for value in desired_values:
        if not _multiselect_value_already_selected(selected, value):
            return False
    for chip in selected:
        if not _chip_matches_any_desired(chip, desired_values):
            return False
    return True


def _get_selected_multiselect_labels(multiselect) -> list[str]:
    chips = multiselect.locator(".multiselect__tag")
    try:
        chips.first.wait_for(timeout=3_000)
    except Exception:
        pass

    result: list[str] = []
    for i in range(chips.count()):
        result.append(chips.nth(i).inner_text().strip())
    return result


def _multiselect_remove_label(multiselect, label: str) -> bool:
    chips = multiselect.locator(".multiselect__tag")
    for i in range(chips.count()):
        chip = chips.nth(i)
        text = chip.inner_text().strip()
        if _multiselect_value_matches_chip(text, label):
            chip.locator(".multiselect__tag-icon").click()
            return True
    return False


def _multiselect_add_option_for_set(
    page: Page,
    multiselect,
    option: str,
    *,
    field_label: str,
) -> None:
    selected = _get_selected_multiselect_labels(multiselect)
    if _multiselect_value_already_selected(selected, option):
        return
    try:
        _multiselect_add_option(page, multiselect, option)
    except RuntimeError as exc:
        if "опция multiselect не найдена" in str(exc):
            raise RuntimeError(f"multiselect option not found: {field_label}={option}") from exc
        raise


def _set_multiselect_list(page: Page, label: str, raw: str) -> None:
    """Set multiselect selection exactly to provided values (remove extras, add missing)."""
    values = [part.strip() for part in (raw or "").split(";") if part.strip()]
    if not values:
        return

    multiselect = _find_multiselect_by_label(page, label)
    if multiselect is None:
        raise RuntimeError(f"multiselect не найден: {label}")

    selected = _get_selected_multiselect_labels(multiselect)
    if _multiselect_selection_matches_desired(selected, values):
        return

    for _ in range(10):
        selected = _get_selected_multiselect_labels(multiselect)
        extras = [chip for chip in selected if not _chip_matches_any_desired(chip, values)]
        if not extras:
            break
        for extra in extras:
            if not _multiselect_remove_label(multiselect, extra):
                raise RuntimeError(f"failed to remove multiselect option: {label}={extra}")
        page.wait_for_timeout(200)

    for value in values:
        selected = _get_selected_multiselect_labels(multiselect)
        if _multiselect_value_already_selected(selected, value):
            continue
        _multiselect_add_option_for_set(page, multiselect, value, field_label=label)

    final_selected = _get_selected_multiselect_labels(multiselect)
    if not _multiselect_selection_matches_desired(final_selected, values):
        raise RuntimeError(
            f"multiselect final state mismatch: {label} expected={values!r} actual={final_selected!r}"
        )


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


def select_single_aggregate_checkbox(
    page: Page, aggregate_name: str, *, card: str | None = None
) -> None:
    """Keep the named aggregate as the only active checkbox; skip clicks if already sole."""
    try:
        switch_to_single_aggregate(
            page,
            aggregate_name,
            card=card,
            timing_scope=_ADD_WALLET_TIMING_SCOPE,
        )
    except AggregateSwitchError as exc:
        raise RuntimeError(str(exc)) from exc


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
        if required:
            names = "/".join(labels)
            raise RuntimeError(f"required nested field not found: {names}")
        return
    if not value:
        if required:
            raise RuntimeError(f"поле {matched_label} требует значение")
        return
    label_for_error = matched_label or labels[0]
    try:
        _fill_locator_text(field, label_for_error, value)
    except FieldNotEditableError:
        raise
    except Exception as exc:
        if required:
            raise RuntimeError(f"не удалось заполнить поле {matched_label}: {exc}") from exc
        raise


def fill_aggregate_modal_fields(modal, row: AddWalletRow) -> None:
    needed = requested_nested_fields_for_add_row(row)
    fill_aggregate_field_if_present(
        modal, ("Карта",), row.card, required="card" in needed
    )
    fill_aggregate_field_if_present(
        modal, ("Телефон",), row.phone, required="phone" in needed
    )
    fill_aggregate_field_if_present(modal, ("Аккаунт",), row.account)
    fill_aggregate_field_if_present(modal, ("MerchantId СБП",), row.merchant_id_sbp)
    fill_aggregate_field_if_present(modal, ACCOUNT_NUMBER_LABELS, row.account_number)


def _fill_confirmed_nested_field(
    page: Page,
    *,
    field,
    value: str,
    label: str,
    key: str,
    expected_top_phone: str,
    card: str | None,
) -> None:
    def resolve_fresh():
        return resolve_nested_field_locator(
            page, key, expected_top_phone=expected_top_phone
        )

    fill_locator_confirmed(
        page,
        field=field,
        value=value,
        label=label,
        resolve_fresh=resolve_fresh,
        card=card,
        timing_prefix="aggregate",
        timing_scope=_ADD_WALLET_TIMING_SCOPE,
    )


def _fill_single_aggregate(page: Page, row: AddWalletRow) -> None:
    if not row.aggregate:
        return
    select_single_aggregate_checkbox(page, row.aggregate, card=row.card)
    _log("aggregate_selected", extra=f"aggregate={row.aggregate}")

    needed = requested_nested_fields_for_add_row(row)
    _log(
        "requested_nested_fields",
        extra=f"aggregate={row.aggregate} fields={sorted(needed.keys())}",
    )
    fields = wait_for_requested_nested_fields(
        page,
        needed=needed,
        expected_top_phone=row.phone,
        aggregate=row.aggregate,
        card=row.card,
        timing_scope=_ADD_WALLET_TIMING_SCOPE,
    )

    _log("aggregate_fill_started", extra=f"aggregate={row.aggregate}")
    with time_form_step(
        "aggregate_fill",
        card=row.card,
        profile=_ADD_WALLET_TIMING_PROFILE,
        scope=_ADD_WALLET_TIMING_SCOPE,
    ):
        if "phone" in needed:
            field = fields.get("phone")
            if field is None:
                raise RuntimeError("required nested field not found: Телефон")
            _fill_confirmed_nested_field(
                page,
                field=field,
                value=needed["phone"],
                label="Телефон",
                key="phone",
                expected_top_phone=row.phone,
                card=row.card,
            )
        for key, labels in (
            ("account", ("Аккаунт",)),
            ("merchant_id_sbp", ("MerchantId СБП",)),
            ("account_number", ACCOUNT_NUMBER_LABELS),
        ):
            if key not in needed:
                continue
            field = fields.get(key)
            if field is None:
                raise RuntimeError(f"required nested field not found: {labels[0]}")
            _fill_confirmed_nested_field(
                page,
                field=field,
                value=needed[key],
                label=labels[0],
                key=key,
                expected_top_phone=row.phone,
                card=row.card,
            )

        nested_card_locator = fields.get("card")
        if nested_card_locator is None:
            item = opportunistic_nested_card_item(page)
            if item is not None:
                nested_card_locator = _locator_for_labeled_input(page, item)

        if nested_card_locator is not None and row.card:
            _fill_confirmed_nested_field(
                page,
                field=nested_card_locator,
                value=row.card,
                label="Карта",
                key="card",
                expected_top_phone=row.phone,
                card=row.card,
            )
        elif "card" in needed:
            raise RuntimeError("required nested field not found: Карта")
        else:
            _log(
                "nested_card_skipped",
                extra=f"aggregate={row.aggregate} reason=not_present",
            )
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
    _fill_phase2_top_level_fields(page, row)


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

        if cfg.stop_before_save:
            _log("stop_before_save", card=row.card, extra="save skipped")
            return make_row_result(
                row,
                row_number=row.row_number,
                result=RESULT_STOP_BEFORE_SAVE,
                comment="form filled; save skipped (stop_before_save)",
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
    except CardSearchUnsettledError:
        log.exception(
            f"{LOG_PREFIX} stage=row_result card_tail={mask_card(row.card)} "
            "error=card_search_unsettled"
        )
        return make_row_result(
            row,
            row_number=row.row_number,
            result=RESULT_FAIL_TECHNICAL,
            comment="card search UI unsettled",
            operator_profile=operator_profile,
            dry_run=cfg.dry_run,
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

    slow_mo = (
        cfg.slow_mo_ms
        if cfg.slow_mo_ms is not None
        else wallet_editor_playwright_slow_mo_ms()
    )
    _log(
        "batch_summary",
        extra=(
            f"rows={len(batch.rows)} dry_run={cfg.dry_run} "
            f"stop_before_save={cfg.stop_before_save} slow_mo={slow_mo}"
        ),
    )

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
                if item.result == RESULT_STOP_BEFORE_SAVE:
                    pause_ms = cfg.stop_before_save_pause_ms
                    try:
                        if pause_ms is None:
                            try:
                                input(
                                    "Форма заполнена (save не нажат). "
                                    "Enter — закрыть браузер без сохранения… "
                                )
                            except EOFError:
                                page.wait_for_timeout(30_000)
                        else:
                            page.wait_for_timeout(max(0, int(pause_ms)))
                    except Exception as pause_exc:
                        _log(
                            "stop_before_save_pause_ended",
                            card=row.card,
                            extra=f"reason={pause_exc}",
                        )
                    break
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
