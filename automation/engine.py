from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import os
import re
import tempfile
import time

import pandas as pd
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from core.playwright_cleanup import close_playwright_stack

from automation.audit import (
    Stats,
    log,
    log_step_duration,
    mask_card,
    normalize_card_digits,
    row_matches_card,
    row_matches_card_strict,
    shorten_for_log,
)
from automation.runtime import (
    RunConfig,
    require_wallet_editor_antares_credentials,
    retry,
    wallet_editor_open_card_settle_ms,
    wallet_editor_playwright_slow_mo_ms,
    wallet_editor_row_match_timeout_ms,
)
from core.datetime_utils import EXCEL_DATE_FORMAT, now_msk
from integrations.wallet_editor_hold import (
    HOLD_CHECK_FAILED_MANUAL_COMMENT,
    HOLD_SKIP_COMMENT,
    HoldPairsSnapshot,
    is_card_partner_on_hold,
    load_hold_pairs_snapshot,
)
from integrations.wallet_editor_registry_lifecycle import (
    DISABLE_DATE_COLUMN,
    OPERATION_DATE_COLUMN,
    result_row_dates,
)


BASE_DIR = "/tmp"

WALLET_URL = "https://antares.plus/lkcard/#/wallet"
CARD_INPUT = 'input[placeholder="Карта"]'
APPLY_BUTTON = 'button:has-text("Применить")'
ROW_SELECTOR = "tr.pointer"
MODAL_BODY = "#wallet-add-modal___BV_modal_body_"
MODAL_CLOSE_BUTTON = "#wallet-add-modal___BV_modal_header_ button.close"
SAVE_BUTTON = 'button:has-text("Сохранить")'

_WALLET_UI_READY_TIMEOUT_MS = 15_000
_AUTH_NETWORKIDLE_TIMEOUT_MS = 60_000
_MODAL_CONTAINER_TIMEOUT_MS = 10_000
_MODAL_DATA_TIMEOUT_MS = 10_000
_MODAL_DATA_POLL_MS = 100
_ROW_MATCH_POLL_MS = 150
_ROW_TEXT_READ_TIMEOUT_MS = 500
_CARD_SEARCH_ENTER_CHECK_MS = 2000
_CARD_SEARCH_FALLBACK_CLICK_TIMEOUT_MS = 2000
_DELETE_SEARCH_TIMEOUT_MS = 10_000
_DELETE_SEARCH_STABLE_POLLS = 3
_DELETE_SEARCH_SAMPLE_ROWS = 5
_EMPTY_WALLET_TOTAL_RE = re.compile(r"Всего:\s*0", re.IGNORECASE)


class OpenCardStageError(Exception):
    """open_card failed at a specific wait/verify stage."""

    def __init__(
        self,
        stage: str,
        card: str,
        cause: Exception | None = None,
        message: str | None = None,
    ) -> None:
        self.stage = stage
        self.card = card
        text = message or f"open_card failed stage={stage} card={card}"
        super().__init__(text)
        self.__cause__ = cause


class CardSearchUnsettledError(Exception):
    """Card filter did not produce a confirmed table update within the wait window."""

    def __init__(self, card: str, message: str | None = None) -> None:
        self.card = card
        super().__init__(
            message
            or f"поиск карты не подтвердил обновление результатов: {mask_card(card)}"
        )

ALLOWED_ACTIONS = {
    "remove_partner",
    "add_partner",
    "set_status",
    "set_direction",
    "add_group",
    "set_group",
    "clear_groups",
    "delete",
}
DIRECTION_LABEL = "Направление"
GROUP_LABELS = ("Группа", "Группы")
GROUP_VALUE_ACTIONS = frozenset({"add_group", "set_group"})

RESULT_OK_DELETED = "OK_DELETED"
RESULT_DRY_RUN_WOULD_DELETE = "DRY_RUN_WOULD_DELETE"
RESULT_STOP_BEFORE_DELETE = "STOP_BEFORE_DELETE"
RESULT_SKIP_NOT_FOUND = "SKIP_NOT_FOUND"
RESULT_FAIL_OPEN_CARD = "FAIL_OPEN_CARD"
RESULT_FAIL_DELETE_BUTTON_NOT_FOUND = "FAIL_DELETE_BUTTON_NOT_FOUND"
RESULT_FAIL_CONFIRM_DIALOG_NOT_FOUND = "FAIL_CONFIRM_DIALOG_NOT_FOUND"
RESULT_FAIL_DELETE_TIMEOUT = "FAIL_DELETE_TIMEOUT"
RESULT_FAIL_STILL_EXISTS = "FAIL_STILL_EXISTS"
RESULT_FAIL_TECHNICAL = "FAIL_TECHNICAL"
RESULT_FAIL_DELETE_CONFLICT = "FAIL_DELETE_CONFLICT"

DELETE_BUTTON_TEXT = "Удалить"
DELETE_CONFIRM_TEXT = "Удалить кошелек?"
DELETE_CONFIRM_OK_TEXT = "OK"
_DELETE_CONFIRM_TIMEOUT_MS = 10_000
_DELETE_MODAL_CLOSE_TIMEOUT_MS = 15_000
_DELETE_CONFIRM_POLL_MS = 100

DELETE_RESULT_CODES = frozenset(
    {
        RESULT_OK_DELETED,
        RESULT_DRY_RUN_WOULD_DELETE,
        RESULT_STOP_BEFORE_DELETE,
        RESULT_SKIP_NOT_FOUND,
        RESULT_FAIL_OPEN_CARD,
        RESULT_FAIL_DELETE_BUTTON_NOT_FOUND,
        RESULT_FAIL_CONFIRM_DIALOG_NOT_FOUND,
        RESULT_FAIL_DELETE_TIMEOUT,
        RESULT_FAIL_STILL_EXISTS,
        RESULT_FAIL_TECHNICAL,
        RESULT_FAIL_DELETE_CONFLICT,
    }
)
ALLOWED_STATUSES = {
    "Готов к работе",
    "Не готов. Sim",
    "Не готов. Нет доступа",
    "Не готов. Баланс",
    "Не готов. Сервисные работы",
    "Не готов. Плановый прозвон",
    "Тест",
    "Сбой эмулятора",
    "Требуется звонок",
    "Не готов. смс",
    "Готов к работе: Выплаты",
    "Перевыпуск",
    "Работа робота",
    "Активный вход",
    "В подключении",
    "Активный выход"
}

AUTO_NO_PARTNERS_STATUS = "Не готов. Плановый прозвон"
AUTO_STATUS_SOURCE_STATUSES = frozenset({
    "Готов к работе",
    "Активный вход",
    "Активный выход",
})


def _normalize_status(status: str) -> str:
    return (status or "").strip()


def _normalize_label_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _labels_match(actual: str, expected: str) -> bool:
    return _normalize_label_text(actual) == _normalize_label_text(expected)


def should_auto_set_no_partners_status(
    final_partner_count: int,
    current_status: str,
    explicit_status_action_seen: bool,
) -> bool:
    if final_partner_count > 0:
        return False
    if explicit_status_action_seen:
        return False
    return _normalize_status(current_status) in AUTO_STATUS_SOURCE_STATUSES


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
            label = labels.first.inner_text(timeout=2000).strip()
        except Exception:
            continue
        if not _labels_match(label, label_text):
            continue
        selects = row.locator("select")
        if selects.count() == 0:
            continue
        return selects.first
    return None


def _get_select_options(select) -> list[tuple[str, str]]:
    options = select.locator("option")
    result = []
    for j in range(options.count()):
        opt = options.nth(j)
        value = (opt.get_attribute("value") or "").strip()
        text = opt.inner_text().strip()
        result.append((value, text))
    return result


def _get_selected_option(select) -> tuple[str, str]:
    data = select.evaluate(
        """el => {
            const opt = el.options[el.selectedIndex];
            if (!opt) return { value: '', text: '' };
            return {
                value: (opt.value || '').trim(),
                text: (opt.textContent || '').trim()
            };
        }"""
    )
    return data["value"], data["text"]


def _option_value_matches(target: str, value: str) -> bool:
    return (value or "").strip() == (target or "").strip()


def _option_visible_text_matches(target: str, text: str) -> bool:
    return _normalize_label_text(text) == _normalize_label_text(target)


def _resolve_direction_option(
    options: list[tuple[str, str]], target: str
) -> tuple[str, str] | None:
    target_stripped = (target or "").strip()
    if not target_stripped:
        return None

    for value, text in options:
        if _option_value_matches(target_stripped, value):
            return value, text

    for value, text in options:
        if _option_visible_text_matches(target_stripped, text):
            return value, text

    return None


def _selection_matches_direction(
    current_value: str,
    current_text: str,
    option_value: str,
    option_text: str,
) -> bool:
    if option_value and _option_value_matches(current_value, option_value):
        return True
    if _option_visible_text_matches(current_text, option_text):
        return True
    if option_value and _option_value_matches(current_text, option_value):
        return True
    if _option_visible_text_matches(current_value, option_text):
        return True
    return False


def _find_direction_select(page: Page):
    return _find_select_by_label(page, DIRECTION_LABEL)


def _find_status_select(page: Page):
    selects = page.locator(f"{MODAL_BODY} select")
    for i in range(selects.count()):
        s = selects.nth(i)
        options = s.locator("option")
        texts = [options.nth(j).inner_text().strip() for j in range(options.count())]
        if any(t in ALLOWED_STATUSES for t in texts):
            return s
    return None


def _get_current_card_status(page: Page) -> str:
    select = _find_status_select(page)
    if not select:
        log.warning("⚠️ [Status] status select not found for read")
        return ""
    return select.evaluate(
        "el => el.options[el.selectedIndex]?.textContent?.trim() || ''"
    ) or ""


def _coerce_group_excel_value(raw) -> tuple[str, str | None]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "", None

    if isinstance(raw, bool):
        return "", f"недопустимое числовое значение для группы: {raw}"

    if isinstance(raw, int):
        return str(raw), None

    if isinstance(raw, float):
        if raw.is_integer():
            return str(int(raw)), None
        return "", f"недопустимое числовое значение для группы: {raw}"

    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return "", None

    try:
        numeric = float(text)
    except ValueError:
        return text, None

    if numeric.is_integer():
        return str(int(numeric)), None
    return "", f"недопустимое числовое значение для группы: {text}"


def _apply_auto_no_partners_status_after_actions(
    page: Page,
    cfg: RunConfig,
    explicit_status_action_seen: bool,
) -> bool:
    chip_pairs = get_partner_chips(page)
    final_count = len(chip_pairs)
    log.info(f"🏷️ [Card] final partners count after card actions={final_count}")

    if final_count > 0:
        log.info("ℹ️ [Card] auto status skipped because partners remain")
        return False

    if explicit_status_action_seen:
        log.info("ℹ️ [Card] auto status skipped because explicit set_status in Excel")
        return False

    current_status = _get_current_card_status(page)
    log.info(f"📌 [Card] current status before auto check={current_status!r}")

    if not should_auto_set_no_partners_status(
        final_count, current_status, explicit_status_action_seen
    ):
        log.info(
            "ℹ️ [Card] auto status skipped because current status not allowed: "
            f"{current_status!r}"
        )
        return False

    log.info(f"⚠️ [Card] auto status applied → {AUTO_NO_PARTNERS_STATUS}")
    result = ensure_status_set(page, AUTO_NO_PARTNERS_STATUS, cfg)
    return not result.startswith("skip")


def _wait_for_wallet_page_ready(page: Page, *, timeout_ms: int = _WALLET_UI_READY_TIMEOUT_MS) -> None:
    """Wait until wallet search UI is usable (card filter input visible)."""
    page.locator(CARD_INPUT).wait_for(state="visible", timeout=timeout_ms)


def _ensure_logged_in(page: Page, context, cfg: RunConfig) -> None:
    log.info("🔐 [Auth] opening wallet page")
    page.goto(WALLET_URL)

    if "#/login" not in page.url:
        _wait_for_wallet_page_ready(page)
        log.info("✅ [Auth] existing session is valid")
        return

    log.warning("⚠️ [Auth] redirected to login, performing login")
    require_wallet_editor_antares_credentials(cfg)
    page.goto("https://antares.plus/lkcard/#/login")

    page.fill("input[type='text']", cfg.login)
    page.fill("input[type='password']", cfg.password)
    page.click("button:has-text('Войти')")

    page.wait_for_load_state("networkidle", timeout=_AUTH_NETWORKIDLE_TIMEOUT_MS)
    _wait_for_wallet_page_ready(page)

    if "#/login" in page.url:
        log.error("❌ [Auth] login failed")
        raise Exception("❌ Логин не удался")

    context.storage_state(path=cfg.auth_state_path)
    log.info(f"✅ [Auth] session refreshed and saved to {cfg.auth_state_path}")


def _close_stale_modal(page: Page) -> None:
    modal = page.locator(MODAL_BODY)
    if not modal.is_visible():
        return

    log.warning("⚠️ [Card] stale modal detected")

    log.info("🔄 [Card] stale modal close attempted")
    close_btn = page.locator(MODAL_CLOSE_BUTTON)
    try:
        close_btn.click(timeout=5000)
    except Exception as e:
        log.warning(f"⚠️ [Card] close button click failed: {e}, trying Escape")
        page.keyboard.press("Escape")

    try:
        modal.wait_for(state="hidden", timeout=10000)
    except Exception:
        if modal.is_visible():
            page.keyboard.press("Escape")
            try:
                modal.wait_for(state="hidden", timeout=5000)
            except Exception:
                pass

    if modal.is_visible():
        log.error("❌ [Card] failed to close stale modal")
        raise Exception("Не удалось закрыть модалку предыдущей карты")

    log.info("✅ [Card] stale modal closed")


def _digits_only(value: str) -> str:
    return normalize_card_digits(value)


def _read_row_text(row, *, row_index: int, card: str) -> str | None:
    try:
        return row.inner_text(timeout=_ROW_TEXT_READ_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        log.info("[Card] row text not ready card=%s row_index=%s", card, row_index)
        return None


def _try_match_row_index(
    rows,
    card_digits: str,
    card: str,
) -> tuple[int | None, int, str]:
    row_count = rows.count()
    first_text = ""
    for i in range(row_count):
        row_text = _read_row_text(rows.nth(i), row_index=i, card=card)
        if row_text is None:
            continue
        if not first_text:
            first_text = row_text
        if row_matches_card(row_text, card_digits):
            return i, row_count, row_text
    return None, row_count, first_text


def _log_row_match_failure(
    card: str,
    card_digits: str,
    rows_count: int,
    first_row_text: str,
) -> None:
    log.error(
        "[Card] row match failed card=%s stage=row_match rows=%s first_row=%r "
        "first_row_tail=%s expected_tail=%s",
        card,
        rows_count,
        shorten_for_log(first_row_text),
        mask_card(normalize_card_digits(first_row_text)),
        mask_card(card_digits),
    )


def _try_match_strict_row_index(
    rows,
    card_digits: str,
    card: str,
) -> tuple[int | None, int, str]:
    row_count = rows.count()
    first_text = ""
    for i in range(row_count):
        row_text = _read_row_text(rows.nth(i), row_index=i, card=card)
        if row_text is None:
            continue
        if not first_text:
            first_text = row_text
        if row_matches_card_strict(row_text, card_digits):
            return i, row_count, row_text
    return None, row_count, first_text


def _wait_for_strict_matching_row(page: Page, rows, card: str, card_digits: str) -> int:
    log.info("[Card] waiting strict row text card=%s", card)
    timeout_ms = wallet_editor_row_match_timeout_ms()

    def attempt() -> tuple[int | None, int, str]:
        return _try_match_strict_row_index(rows, card_digits, card)

    if timeout_ms <= 0:
        match_index, rows_count, first_text = attempt()
        if match_index is not None:
            log.info("[Card] strict row text loaded card=%s", card)
            log.info("[Card] matched strict row index=%s card=%s", match_index, card)
            return match_index
        _log_row_match_failure(card, card_digits, rows_count, first_text)
        log.info("[Card] row_not_found card=%s stage=strict_row_match", card)
        raise OpenCardStageError(
            "row_match",
            card,
            message=f"Карта не найдена (strict row_match): {card}",
        )

    deadline = time.monotonic() + timeout_ms / 1000.0
    last_count = 0
    last_first = ""
    while time.monotonic() < deadline:
        match_index, rows_count, first_text = attempt()
        last_count = rows_count
        last_first = first_text
        if match_index is not None:
            log.info("[Card] strict row text loaded card=%s", card)
            log.info("[Card] matched strict row index=%s card=%s", match_index, card)
            return match_index
        if rows_count > 0:
            page.wait_for_timeout(_ROW_MATCH_POLL_MS)
        else:
            page.wait_for_timeout(_ROW_MATCH_POLL_MS)

    _log_row_match_failure(card, card_digits, last_count, last_first)
    log.info("[Card] row_not_found card=%s stage=strict_row_match", card)
    raise OpenCardStageError(
        "row_match",
        card,
        message=f"Карта не найдена (strict row_match): {card}",
    )


def _wait_for_matching_row(page: Page, rows, card: str, card_digits: str) -> int:
    log.info("[Card] waiting row text card=%s", card)
    timeout_ms = wallet_editor_row_match_timeout_ms()

    def attempt() -> tuple[int | None, int, str]:
        return _try_match_row_index(rows, card_digits, card)

    if timeout_ms <= 0:
        match_index, rows_count, first_text = attempt()
        if match_index is not None:
            log.info("[Card] row text loaded card=%s", card)
            log.info("[Card] matched row index=%s card=%s", match_index, card)
            return match_index
        _log_row_match_failure(card, card_digits, rows_count, first_text)
        raise OpenCardStageError(
            "row_match",
            card,
            message=f"Карта не найдена (stage=row_match): {card}",
        )

    deadline = time.monotonic() + timeout_ms / 1000.0
    last_count = 0
    last_first = ""
    while time.monotonic() < deadline:
        match_index, rows_count, first_text = attempt()
        last_count = rows_count
        last_first = first_text
        if match_index is not None:
            log.info("[Card] row text loaded card=%s", card)
            log.info("[Card] matched row index=%s card=%s", match_index, card)
            return match_index
        if rows_count > 0:
            page.wait_for_timeout(_ROW_MATCH_POLL_MS)
        else:
            page.wait_for_timeout(_ROW_MATCH_POLL_MS)

    _log_row_match_failure(card, card_digits, last_count, last_first)
    raise OpenCardStageError(
        "row_match",
        card,
        message=f"Карта не найдена (stage=row_match): {card}",
    )


def _try_get_modal_card_value(page: Page) -> str | None:
    modal = page.locator(MODAL_BODY)
    if not modal.is_visible():
        return None

    rows = modal.locator("div.row")
    for i in range(rows.count()):
        row = rows.nth(i)
        if not row.is_visible():
            continue
        labels = row.locator("label")
        if labels.count() == 0:
            continue
        try:
            label_text = labels.first.inner_text(timeout=2000).strip()
        except Exception:
            continue
        if label_text != "Карта":
            continue
        inputs = row.locator("input[type='text']")
        if inputs.count() == 0:
            continue
        inp = inputs.first
        if not inp.is_visible():
            continue
        value = inp.input_value().strip()
        if value:
            return value

    log.warning("⚠️ [Card] card field not found by label; using first visible modal input")
    inputs = modal.locator("input[type='text']")
    for i in range(inputs.count()):
        inp = inputs.nth(i)
        if not inp.is_visible():
            continue
        value = inp.input_value().strip()
        if value:
            return value

    return None


def _try_get_modal_card_value_fast(page: Page) -> str | None:
    """Read modal card input without long label waits — for polling while data loads."""
    modal = page.locator(MODAL_BODY)
    if not modal.is_visible():
        return None

    rows = modal.locator("div.row")
    for i in range(rows.count()):
        row = rows.nth(i)
        if not row.is_visible():
            continue
        inputs = row.locator("input[type='text']")
        for j in range(inputs.count()):
            inp = inputs.nth(j)
            if not inp.is_visible():
                continue
            value = inp.input_value().strip()
            if value and _digits_only(value):
                return value

    inputs = modal.locator("input[type='text']")
    for i in range(inputs.count()):
        inp = inputs.nth(i)
        if not inp.is_visible():
            continue
        value = inp.input_value().strip()
        if value and _digits_only(value):
            return value

    return None


def _wait_modal_container_visible(modal, card: str) -> None:
    try:
        modal.wait_for(state="visible", timeout=_MODAL_CONTAINER_TIMEOUT_MS)
    except Exception as exc:
        log.error("[Card] open_card timeout stage=modal_container card=%s", card)
        raise OpenCardStageError("modal_container", card, exc) from exc
    log.info("[Card] modal container visible card=%s", card)


def _wait_modal_card_data_ready(page: Page, card: str) -> str:
    log.info("[Card] waiting modal data card=%s", card)
    settle_ms = wallet_editor_open_card_settle_ms()
    if settle_ms > 0:
        page.wait_for_timeout(settle_ms)

    deadline = time.monotonic() + _MODAL_DATA_TIMEOUT_MS / 1000.0
    last_value: str | None = None
    while time.monotonic() < deadline:
        last_value = _try_get_modal_card_value_fast(page)
        if last_value and _digits_only(last_value):
            return last_value
        page.wait_for_timeout(_MODAL_DATA_POLL_MS)

    log.error("[Card] open_card timeout stage=modal_data card=%s", card)
    raise OpenCardStageError(
        "modal_data",
        card,
        message=f"open_card failed stage=modal_data card={card} reason=card field not populated",
    )


def _verify_modal_card_number(card: str, modal_card_value: str) -> None:
    card_digits = _digits_only(card)
    modal_digits = _digits_only(modal_card_value)
    if card_digits and modal_digits and card_digits != modal_digits:
        log.error("[Card] open_card timeout stage=card_verify card=%s", card)
        raise OpenCardStageError(
            "card_verify",
            card,
            message=f"Модалка не соответствует карте: {card}",
        )
    if not (card_digits and modal_digits):
        log.error("[Card] open_card timeout stage=card_verify card=%s", card)
        raise OpenCardStageError(
            "card_verify",
            card,
            message=f"open_card failed stage=card_verify card={card} reason=empty card value",
        )


def _resolve_search_card(card: str) -> tuple[str, bool]:
    """Digits-only card string for Antares filter input; fallback or fail if invalid."""
    raw = (card or "").strip()
    search = normalize_card_digits(card)
    if search:
        return search, search != raw
    if raw:
        log.warning(
            "[Card] search card has no digits; using raw card raw_tail=%s",
            mask_card(raw),
        )
        return raw, False
    raise OpenCardStageError(
        "search_input",
        card or "",
        message="open_card failed stage=search_input: empty or invalid card number",
    )


def _submit_card_filter(page: Page, card: str) -> None:
    search_card, normalized_changed = _resolve_search_card(card)
    card_input = page.locator(CARD_INPUT)
    card_input.fill("")
    card_input.fill(search_card)
    log.info(
        "[Card] search input filled raw_tail=%s search_tail=%s normalized_changed=%s",
        mask_card(card),
        mask_card(search_card),
        normalized_changed,
    )

    card_input.press("Enter")
    log.info(
        "[Card] search submitted via enter search_tail=%s",
        mask_card(search_card),
    )

    deadline = time.monotonic() + _CARD_SEARCH_ENTER_CHECK_MS / 1000.0
    while time.monotonic() < deadline:
        if page.locator(ROW_SELECTOR).count() > 0:
            return
        page.wait_for_timeout(_ROW_MATCH_POLL_MS)

    log.info(
        "[Card] search fallback apply search_tail=%s",
        mask_card(search_card),
    )
    try:
        page.locator(APPLY_BUTTON).click(
            timeout=_CARD_SEARCH_FALLBACK_CLICK_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError:
        log.info(
            "[Card] search fallback apply skipped search_tail=%s reason=button unavailable",
            mask_card(search_card),
        )


def open_card_with_row_matcher(page: Page, card: str, wait_for_row) -> None:
    """Stable card open flow shared by Disable Wallet and Edit Wallet."""
    modal = page.locator(MODAL_BODY)

    _close_stale_modal(page)

    log.info("🔎 [Card] searching card raw_tail=%s", mask_card(card))
    _submit_card_filter(page, card)
    page.wait_for_selector(ROW_SELECTOR, timeout=10000)

    rows = page.locator(ROW_SELECTOR)
    row_count = rows.count()
    log.info(f"📋 [Card] rows found={row_count} for card={card}")

    card_digits = normalize_card_digits(card)
    match_index = wait_for_row(page, rows, card, card_digits)

    row_text = _read_row_text(rows.nth(match_index), row_index=match_index, card=card)
    if row_text is None:
        log.info(
            "[Card] row_not_found card=%s reason=row_text_unreadable index=%s",
            card,
            match_index,
        )
        raise OpenCardStageError(
            "row_match",
            card,
            message=f"row text unreadable before click: {card}",
        )

    rows.nth(match_index).click()
    log.info(
        "[Card] row_clicked card=%s index=%s selector=%s",
        card,
        match_index,
        ROW_SELECTOR,
    )

    try:
        _wait_modal_container_visible(modal, card)
    except OpenCardStageError:
        log.info(
            "[Card] modal_container failed card=%s row_index=%s row_clicked=true",
            card,
            match_index,
        )
        raise
    log.info("[Card] modal_container_visible card=%s", card)

    modal_card_value = _wait_modal_card_data_ready(page, card)
    log.info("[Card] modal_data_visible card=%s", card)

    try:
        _verify_modal_card_number(card, modal_card_value)
    except OpenCardStageError as exc:
        if exc.stage == "card_verify":
            log.info(
                "[Card] modal_card_mismatch card=%s expected_tail=%s actual_tail=%s",
                card,
                mask_card(card),
                mask_card(modal_card_value),
            )
        raise

    log.info("[Card] modal_card_verified card=%s", card)
    log.info(f"✅ [Card] card modal opened card={card}")


def open_card(page: Page, card: str) -> None:
    open_card_with_row_matcher(page, card, _wait_for_matching_row)


def open_card_strict(page: Page, card: str) -> None:
    """Open wallet form using strict card row match (delete / lifecycle ops)."""
    open_card_with_row_matcher(page, card, _wait_for_strict_matching_row)


def card_exists_strict(page: Page, card: str) -> bool:
    """Strict search: True only when a table row exactly matches the card digits.

    Raises CardSearchUnsettledError when the filter result set never settles.
    """
    return find_strict_matching_row_index(page, card) is not None


def _table_row_fingerprint(page: Page, card: str) -> tuple[int, tuple[str, ...]]:
    """Compact fingerprint of current wallet table rows (count + sample texts)."""
    rows = page.locator(ROW_SELECTOR)
    try:
        count = rows.count()
    except Exception:
        return 0, tuple()
    samples: list[str] = []
    for i in range(min(count, _DELETE_SEARCH_SAMPLE_ROWS)):
        text = _read_row_text(rows.nth(i), row_index=i, card=card)
        if text is None:
            samples.append("")
        else:
            samples.append(normalize_card_digits(text) or shorten_for_log(text, max_len=64))
    return count, tuple(samples)


def _page_shows_empty_wallet_results(page: Page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=1_000)
    except Exception:
        return False
    return bool(_EMPTY_WALLET_TOTAL_RE.search(body or ""))


def find_strict_matching_row_index(page: Page, card: str) -> int | None:
    """Fill search once, wait for filter refresh, return strict match index or None.

    None means the filter settled and the card is absent (SKIP_NOT_FOUND).
    Raises CardSearchUnsettledError when results never leave the pre-filter state
    (e.g. still showing the previous 100 rows) — callers must NOT treat that as skip.
    """
    _close_stale_modal(page)

    before_fp = _table_row_fingerprint(page, card)
    log.info(
        "🔎 [Delete] strict search start card=%s before_rows=%s",
        mask_card(card),
        before_fp[0],
    )
    _submit_card_filter(page, card)

    card_digits = normalize_card_digits(card)
    timeout_ms = max(_DELETE_SEARCH_TIMEOUT_MS, wallet_editor_row_match_timeout_ms())
    deadline = time.monotonic() + timeout_ms / 1000.0

    filter_changed = False
    stable_ticks = 0
    last_fp: tuple[int, tuple[str, ...]] | None = None
    last_rows = before_fp[0]

    while time.monotonic() < deadline:
        rows = page.locator(ROW_SELECTOR)
        match_index, rows_count, _ = _try_match_strict_row_index(rows, card_digits, card)
        last_rows = rows_count
        if match_index is not None:
            log.info(
                "🔎 [Delete] strict search result card=%s found=True index=%s rows=%s "
                "filter_changed=%s",
                mask_card(card),
                match_index,
                rows_count,
                filter_changed,
            )
            return match_index

        current_fp = _table_row_fingerprint(page, card)
        if current_fp != before_fp:
            filter_changed = True

        empty = rows_count == 0 or _page_shows_empty_wallet_results(page)
        if empty and filter_changed:
            log.info(
                "🔎 [Delete] strict search result card=%s found=False rows=0 "
                "reason=empty_after_filter",
                mask_card(card),
            )
            return None

        if filter_changed:
            if current_fp == last_fp:
                stable_ticks += 1
            else:
                stable_ticks = 1
                last_fp = current_fp
            if stable_ticks >= _DELETE_SEARCH_STABLE_POLLS:
                log.info(
                    "🔎 [Delete] strict search result card=%s found=False rows=%s "
                    "reason=settled_without_match",
                    mask_card(card),
                    rows_count,
                )
                return None
        else:
            stable_ticks = 0
            last_fp = current_fp

        page.wait_for_timeout(_ROW_MATCH_POLL_MS)

    if not filter_changed:
        log.error(
            "❌ [Delete] search unsettled card=%s before_rows=%s last_rows=%s "
            "(stale table never refreshed)",
            mask_card(card),
            before_fp[0],
            last_rows,
        )
        raise CardSearchUnsettledError(card)

    # Filter changed at some point but never stayed stable long enough — still
    # treat as unsettled rather than false SKIP.
    log.error(
        "❌ [Delete] search unsettled card=%s rows=%s reason=no_stable_result",
        mask_card(card),
        last_rows,
    )
    raise CardSearchUnsettledError(card)


def open_matched_card_row(page: Page, card: str, match_index: int) -> None:
    """Open wallet form from an already-matched row — does not re-fill search."""
    modal = page.locator(MODAL_BODY)
    rows = page.locator(ROW_SELECTOR)

    row_text = _read_row_text(rows.nth(match_index), row_index=match_index, card=card)
    if row_text is None:
        log.info(
            "[Card] row_not_found card=%s reason=row_text_unreadable index=%s",
            card,
            match_index,
        )
        raise OpenCardStageError(
            "row_match",
            card,
            message=f"row text unreadable before click: {card}",
        )

    rows.nth(match_index).click()
    log.info(
        "[Card] row_clicked card=%s index=%s selector=%s (reuse search results)",
        card,
        match_index,
        ROW_SELECTOR,
    )

    try:
        _wait_modal_container_visible(modal, card)
    except OpenCardStageError:
        log.info(
            "[Card] modal_container failed card=%s row_index=%s row_clicked=true",
            card,
            match_index,
        )
        raise
    log.info("[Card] modal_container_visible card=%s", card)

    modal_card_value = _wait_modal_card_data_ready(page, card)
    log.info("[Card] modal_data_visible card=%s", card)

    try:
        _verify_modal_card_number(card, modal_card_value)
    except OpenCardStageError as exc:
        if exc.stage == "card_verify":
            log.info(
                "[Card] modal_card_mismatch card=%s expected_tail=%s actual_tail=%s",
                card,
                mask_card(card),
                mask_card(modal_card_value),
            )
        raise

    log.info("[Card] modal_card_verified card=%s", card)
    log.info(f"✅ [Card] card modal opened card={card}")


def find_and_open_card_for_delete(page: Page, card: str) -> int | None:
    """One search fill + open the matched row (no second search).

    Returns match index, or None when the card is absent (SKIP_NOT_FOUND).
    Raises OpenCardStageError when the row was found but the form failed to open.
    """
    match_index = find_strict_matching_row_index(page, card)
    if match_index is None:
        return None
    open_matched_card_row(page, card, match_index)
    return match_index


def _button_looks_danger(button) -> bool:
    classes = (button.get_attribute("class") or "").casefold()
    if "btn-danger" in classes or "danger" in classes:
        return True
    try:
        color = button.evaluate(
            """el => {
                const s = window.getComputedStyle(el);
                return (s.backgroundColor || '') + '|' + (s.color || '');
            }"""
        )
    except Exception:
        return False
    # Approximate red: high R relative to G/B in rgb(...)
    for part in str(color).split("|"):
        m = re.search(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", part)
        if not m:
            continue
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if r >= 160 and r > g + 40 and r > b + 40:
            return True
    return False


def _find_wallet_delete_button(page: Page):
    """Red button with exact text «Удалить» inside the open wallet form."""
    modal = page.locator(MODAL_BODY)
    if not modal.is_visible():
        return None

    # Scroll form to bottom so the red delete control is in view.
    try:
        modal.evaluate(
            """el => {
                el.scrollTop = el.scrollHeight;
                const root = el.closest('.modal-body, .modal-content, .modal') || el;
                if (root && root !== el) root.scrollTop = root.scrollHeight;
            }"""
        )
    except Exception as exc:
        log.warning("⚠️ [Delete] scroll form to delete button failed: %s", exc)

    buttons = modal.locator("button")
    exact_matches = []
    for i in range(buttons.count()):
        btn = buttons.nth(i)
        try:
            if not btn.is_visible():
                continue
            text = (btn.inner_text(timeout=1000) or "").strip()
        except Exception:
            continue
        if text != DELETE_BUTTON_TEXT:
            continue
        exact_matches.append(btn)

    for btn in exact_matches:
        if _button_looks_danger(btn):
            try:
                btn.scroll_into_view_if_needed(timeout=3_000)
            except Exception:
                pass
            return btn
    return None


def _find_delete_confirm_dialog(page: Page):
    """Visible confirmation dialog containing «Удалить кошелек?»."""
    for selector in (".modal.show", "[role='dialog']", ".modal"):
        dialogs = page.locator(selector)
        count = dialogs.count()
        for i in range(count):
            dialog = dialogs.nth(i)
            try:
                if not dialog.is_visible():
                    continue
                text = dialog.inner_text(timeout=1000) or ""
            except Exception:
                continue
            if DELETE_CONFIRM_TEXT in text:
                return dialog
    return None


def _wait_delete_confirm_dialog(page: Page):
    deadline = time.monotonic() + _DELETE_CONFIRM_TIMEOUT_MS / 1000.0
    while time.monotonic() < deadline:
        dialog = _find_delete_confirm_dialog(page)
        if dialog is not None:
            return dialog
        page.wait_for_timeout(_DELETE_CONFIRM_POLL_MS)
    return None


def _click_delete_confirm_ok(dialog) -> None:
    """Click button with exact text OK inside the confirmation dialog only."""
    buttons = dialog.locator("button")
    for i in range(buttons.count()):
        btn = buttons.nth(i)
        try:
            if not btn.is_visible():
                continue
            text = (btn.inner_text(timeout=1000) or "").strip()
        except Exception:
            continue
        if text == DELETE_CONFIRM_OK_TEXT:
            btn.click()
            return
    raise RuntimeError("кнопка OK не найдена внутри диалога подтверждения удаления")


def _pause_stop_before_delete(page: Page, cfg: RunConfig) -> None:
    pause_ms = cfg.stop_before_save_pause_ms
    try:
        if pause_ms is None:
            log.info(
                "ℹ️ [Delete] stop_before_delete — waiting for Enter in console "
                "(browser stays open)"
            )
            try:
                input("stop_before_delete: press Enter to continue… ")
            except EOFError:
                page.wait_for_timeout(3_000)
        else:
            log.info("ℹ️ [Delete] stop_before_delete pause_ms=%s", pause_ms)
            page.wait_for_timeout(max(0, int(pause_ms)))
    except Exception as exc:
        log.warning("⚠️ [Delete] stop_before_delete pause ended: %s", exc)


def _status_from_delete_result(result: str) -> str:
    """Map typed delete codes to Excel status column (typed codes preserved)."""
    code = (result or "").strip()
    if code in DELETE_RESULT_CODES:
        return code
    if code.lower().startswith("skip") or code.lower().startswith("dry_run"):
        return RESULT_SKIP_NOT_FOUND if "not" in code.lower() else code
    return RESULT_FAIL_TECHNICAL


def timing_outcome_from_result(result: str) -> str:
    """Map Wallet Editor / delete result codes to WE/timing outcome."""
    code = (result or "").strip()
    upper = code.upper()
    lower = code.lower()
    if upper == RESULT_OK_DELETED or upper == "OK" or lower.startswith("ok"):
        return "ok"
    if (
        upper == RESULT_SKIP_NOT_FOUND
        or upper == RESULT_DRY_RUN_WOULD_DELETE
        or upper == RESULT_STOP_BEFORE_DELETE
        or lower.startswith("skip")
        or lower.startswith("dry_run")
        or lower.startswith("stop_before")
    ):
        return "skip"
    if upper.startswith("FAIL") or lower.startswith("fail"):
        return "fail"
    if not lower.startswith("skip") and (
        lower.startswith("set")
        or "removed" in lower
        or "added" in lower
        or "cleared" in lower
        or lower == "saved"
    ):
        return "ok"
    if lower.startswith("skip"):
        return "skip"
    return "fail"


def _wallet_form_visible(page: Page) -> bool:
    try:
        return bool(page.locator(MODAL_BODY).is_visible())
    except Exception:
        return False


def _search_input_ready(page: Page) -> bool:
    try:
        return bool(page.locator(CARD_INPUT).is_visible())
    except Exception:
        return False


def _dismiss_confirm_dialog_without_ok(page: Page) -> None:
    """Dismiss leftover «Удалить кошелек?» without clicking OK or Удалить again."""
    dialog = _find_delete_confirm_dialog(page)
    if dialog is None:
        return
    log.info("🗑️ [Delete] dismissing leftover confirm dialog without OK")
    try:
        close_btn = dialog.locator(
            "button.close, button[aria-label='Close'], "
            ".close, [data-dismiss='modal']"
        )
        if close_btn.count() > 0:
            candidate = close_btn.first
            if candidate.is_visible():
                candidate.click(timeout=2_000)
                page.wait_for_timeout(_DELETE_CONFIRM_POLL_MS)
                if _find_delete_confirm_dialog(page) is None:
                    return
    except Exception as exc:
        log.warning("⚠️ [Delete] confirm close button failed: %s", exc)
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(_DELETE_CONFIRM_POLL_MS)
    except Exception as exc:
        log.warning("⚠️ [Delete] Escape on confirm failed: %s", exc)


def _goto_wallet_page(page: Page) -> None:
    log.info("🔄 [Delete] navigating to wallet page to restore search UI")
    page.goto(WALLET_URL)
    _wait_for_wallet_page_ready(page)


def ensure_wallet_search_ready(page: Page, *, allow_goto: bool = True) -> bool:
    """Restore a clean wallet list page so strict search can run.

    Never clicks «Удалить» or confirm «OK». Returns True when CARD_INPUT is usable
    and no wallet form / delete-confirm overlay blocks the page.
    """
    try:
        if _find_delete_confirm_dialog(page) is not None:
            _dismiss_confirm_dialog_without_ok(page)

        if _wallet_form_visible(page):
            try:
                _close_stale_modal(page)
            except Exception as exc:
                log.warning("⚠️ [Delete] safe form close failed: %s", exc)
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(_DELETE_CONFIRM_POLL_MS)
                except Exception:
                    pass

        if (
            _search_input_ready(page)
            and not _wallet_form_visible(page)
            and _find_delete_confirm_dialog(page) is None
        ):
            return True

        if not allow_goto:
            return False

        _goto_wallet_page(page)

        if _find_delete_confirm_dialog(page) is not None:
            _dismiss_confirm_dialog_without_ok(page)
        if _wallet_form_visible(page):
            try:
                _close_stale_modal(page)
            except Exception:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass

        return (
            _search_input_ready(page)
            and not _wallet_form_visible(page)
            and _find_delete_confirm_dialog(page) is None
        )
    except Exception as exc:
        log.error("❌ [Delete] ensure_wallet_search_ready failed: %s", exc)
        return False


def _wait_form_hidden_after_delete(page: Page) -> bool:
    """Wait for wallet form to close after confirm OK. True if hidden."""
    try:
        page.locator(MODAL_BODY).wait_for(
            state="hidden",
            timeout=_DELETE_MODAL_CLOSE_TIMEOUT_MS,
        )
        return True
    except PlaywrightTimeoutError:
        return False


def _wait_confirm_dialog_gone(page: Page, *, timeout_ms: int = 5_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if _find_delete_confirm_dialog(page) is None:
            return
        page.wait_for_timeout(_DELETE_CONFIRM_POLL_MS)


def classify_after_delete_confirm(page: Page, card: str) -> str:
    """After OK: recover UI if needed, then classify by strict post-search only."""
    form_closed = _wait_form_hidden_after_delete(page)
    if form_closed:
        log.info("✅ [Delete] form closed after OK card=%s", mask_card(card))
        _wait_confirm_dialog_gone(page)
    else:
        log.warning(
            "⚠️ [Delete] form close timeout — recovering UI before verification "
            "card=%s",
            mask_card(card),
        )
        # Do NOT click OK / Удалить again — only dismiss overlays and restore search.
        if _find_delete_confirm_dialog(page) is not None:
            _dismiss_confirm_dialog_without_ok(page)
        if _wallet_form_visible(page):
            try:
                _close_stale_modal(page)
            except Exception as exc:
                log.warning("⚠️ [Delete] recovery form close failed: %s", exc)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass

    if not ensure_wallet_search_ready(page, allow_goto=True):
        log.error(
            "❌ [Delete] cannot restore UI for verification search card=%s",
            mask_card(card),
        )
        return RESULT_FAIL_DELETE_TIMEOUT

    try:
        still_exists = card_exists_strict(page, card)
    except Exception as exc:
        log.error(
            "❌ [Delete] verification search failed card=%s: %s",
            mask_card(card),
            exc,
        )
        return RESULT_FAIL_DELETE_TIMEOUT

    if still_exists:
        log.error("❌ [Delete] card still exists after delete card=%s", mask_card(card))
        return RESULT_FAIL_STILL_EXISTS

    log.info("✅ [Delete] card deleted (strict search empty) card=%s", mask_card(card))
    return RESULT_OK_DELETED


def ensure_wallet_deleted(page: Page, card: str, cfg: RunConfig) -> str:
    """Full wallet delete: one pre-search → open matched row → Удалить → OK → verify.

    Card number is typed into search exactly twice on the success path:
    1) initial find (then open without re-typing);
    2) post-delete verification search.
    """
    log.info("🗑️ [Delete] start card=%s dry_run=%s", mask_card(card), cfg.dry_run)
    try:
        # dry_run / stop paths: search once; open only when mutating.
        if cfg.dry_run:
            try:
                match_index = find_strict_matching_row_index(page, card)
            except CardSearchUnsettledError:
                log.error(
                    "❌ [Delete] dry_run search unsettled card=%s",
                    mask_card(card),
                )
                return RESULT_FAIL_TECHNICAL
            if match_index is None:
                log.info(
                    "ℹ️ [Delete] card not found → SKIP_NOT_FOUND card=%s",
                    mask_card(card),
                )
                return RESULT_SKIP_NOT_FOUND
            log.info(
                "ℹ️ [Delete] dry_run → DRY_RUN_WOULD_DELETE (no open/click) card=%s",
                mask_card(card),
            )
            return RESULT_DRY_RUN_WOULD_DELETE

        try:
            match_index = find_and_open_card_for_delete(page, card)
        except CardSearchUnsettledError:
            log.error("❌ [Delete] search unsettled card=%s", mask_card(card))
            return RESULT_FAIL_TECHNICAL
        except OpenCardStageError as exc:
            log.error(
                "❌ [Delete] open_card failed card=%s stage=%s",
                mask_card(card),
                exc.stage,
            )
            try:
                _close_stale_modal(page)
            except Exception:
                pass
            return RESULT_FAIL_OPEN_CARD

        if match_index is None:
            log.info(
                "ℹ️ [Delete] card not found → SKIP_NOT_FOUND card=%s",
                mask_card(card),
            )
            return RESULT_SKIP_NOT_FOUND

        delete_btn = _find_wallet_delete_button(page)
        if delete_btn is None:
            log.error("❌ [Delete] button «Удалить» not found card=%s", mask_card(card))
            try:
                _close_stale_modal(page)
            except Exception:
                pass
            return RESULT_FAIL_DELETE_BUTTON_NOT_FOUND

        if cfg.stop_before_delete:
            log.info(
                "ℹ️ [Delete] stop_before_delete — form open, not clicking Удалить card=%s",
                mask_card(card),
            )
            _pause_stop_before_delete(page, cfg)
            return RESULT_STOP_BEFORE_DELETE

        delete_btn.click()
        log.info("🗑️ [Delete] clicked Удалить card=%s", mask_card(card))

        dialog = _wait_delete_confirm_dialog(page)
        if dialog is None:
            log.error("❌ [Delete] confirm dialog not found card=%s", mask_card(card))
            try:
                _close_stale_modal(page)
            except Exception:
                pass
            return RESULT_FAIL_CONFIRM_DIALOG_NOT_FOUND

        try:
            _click_delete_confirm_ok(dialog)
        except Exception as exc:
            log.error(
                "❌ [Delete] confirm OK click failed card=%s: %s",
                mask_card(card),
                exc,
            )
            try:
                _close_stale_modal(page)
            except Exception:
                pass
            return RESULT_FAIL_CONFIRM_DIALOG_NOT_FOUND

        log.info("🗑️ [Delete] confirmed OK card=%s", mask_card(card))
        # Source of truth is strict post-search, not form close alone.
        return classify_after_delete_confirm(page, card)
    except Exception as exc:
        log.exception("❌ [Delete] technical failure card=%s: %s", mask_card(card), exc)
        return RESULT_FAIL_TECHNICAL


def _partner_matches_chip(chip_text: str, partner: str) -> bool:
    partner_norm = (partner or "").strip()
    if not partner_norm:
        return False
    chip_norm = (chip_text or "").strip()
    return partner_norm.lower() in chip_norm.lower()


def _partner_already_selected(chips: list[str], partner: str) -> bool:
    return any(_partner_matches_chip(text, partner) for text in chips)


def _find_multiselect_by_label(page: Page, *label_texts: str):
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
            label = labels.first.inner_text(timeout=2000).strip()
        except Exception:
            continue
        if not any(_labels_match(label, expected) for expected in label_texts):
            continue
        multiselect = row.locator(
            "xpath=.//div[contains(@class,'multiselect') and not(contains(@class,'multiselect__tags'))]"
        )
        if multiselect.count() == 0:
            return None
        return multiselect.first
    return None


def _get_selected_multiselect_labels(multiselect, log_prefix: str) -> list[str]:
    chips = multiselect.locator(".multiselect__tag")
    try:
        chips.first.wait_for(timeout=3000)
    except Exception:
        log.warning(f"⚠️ [{log_prefix}] chips not loaded yet")

    result = []
    for i in range(chips.count()):
        result.append(chips.nth(i).inner_text().strip())
    log.info(f"🏷️ [{log_prefix}] selected chips={result}")
    return result


def _multiselect_remove_label(multiselect, label: str) -> bool:
    chips = multiselect.locator(".multiselect__tag")
    for i in range(chips.count()):
        chip = chips.nth(i)
        text = chip.inner_text().strip()
        if _partner_matches_chip(text, label):
            chip.locator(".multiselect__tag-icon").click()
            log.info(f"✅ [Multiselect] removed chip={text}")
            return True
    return False


def _multiselect_add_option(page: Page, multiselect, option: str, log_prefix: str) -> bool:
    multiselect.scroll_into_view_if_needed()
    multiselect.wait_for(state="visible", timeout=3000)

    try:
        multiselect.click()
    except Exception:
        log.warning(f"⚠️ [{log_prefix}] click failed → force")
        multiselect.click(force=True)

    dropdown = page.locator(".multiselect__content-wrapper:visible")
    dropdown.wait_for(state="visible", timeout=5000)

    options = dropdown.locator("li")
    log.info(f"📜 [{log_prefix}] start scrolling search")

    found = None
    last_count = -1
    option_norm = (option or "").strip()

    for _ in range(25):
        visible_count = options.count()
        log.info(f"📂 [{log_prefix}] visible options={visible_count}")

        for i in range(visible_count):
            option_el = options.nth(i)
            text = option_el.inner_text().strip()
            if option_norm.lower() in text.lower():
                found = option_el
                log.info(f"🎯 [{log_prefix}] found option={text}")
                break

        if found:
            break

        if visible_count == last_count:
            log.info(f"⛔ [{log_prefix}] reached end of list")
            break

        last_count = visible_count
        dropdown.evaluate("el => el.scrollTop = el.scrollHeight")
        page.wait_for_timeout(300)

    if not found:
        log.warning(f"⚠️ [{log_prefix}] not found after scroll option={option}")
        return False

    found.scroll_into_view_if_needed()
    found.wait_for(state="visible", timeout=3000)

    try:
        found.click()
    except Exception:
        log.warning(f"⚠️ [{log_prefix}] option click failed → force")
        found.click(force=True)

    log.info(f"✅ [{log_prefix}] option added={option}")
    return True


def _group_already_selected(labels: list[str], group: str) -> bool:
    return _partner_already_selected(labels, group)


def _normalized_group_set(labels: list[str]) -> set[str]:
    return {_normalize_label_text(label) for label in labels if (label or "").strip()}


def _get_group_multiselect(page: Page):
    multiselect = _find_multiselect_by_label(page, *GROUP_LABELS)
    if not multiselect:
        raise Exception("multiselect группы не найден")
    return multiselect


def get_partner_chips(page: Page):
    modal = page.locator(MODAL_BODY)
    input_el = modal.locator("input[placeholder='Партнеры']")
    multiselect = input_el.locator("xpath=ancestor::div[contains(@class,'multiselect')]")
    chips = multiselect.locator(".multiselect__tag")

    # 🔥 ЖДЁМ, ПОКА ОНИ ПОЯВЯТСЯ
    try:
        chips.first.wait_for(timeout=3000)
    except:
        log.warning("⚠️ [Partners] chips not loaded yet")

    result = []
    for i in range(chips.count()):
        chip = chips.nth(i)
        text = chip.inner_text().strip()
        result.append((chip, text))

    log.info(f"🏷️ [Partners] selected chips={[text for _, text in result]}")
    return result


def remove_partner_by_chip(page: Page, partner: str):
    chips = get_partner_chips(page)
    log.info(f"➖ [Partners] removing partner={partner}")

    for chip, text in chips:
        if _partner_matches_chip(text, partner):
            chip.locator(".multiselect__tag-icon").click()
            log.info(f"✅ [Partners] removed partner chip={text}")
            return True

    log.info(f"ℹ️ [Partners] partner not selected partner={partner}")
    return False


def ensure_partner_removed(page: Page, partner: str, cfg: RunConfig):
    log.info(f"🧩 [Action] remove_partner value={partner}")

    if cfg.dry_run:
        log.info("ℹ️ [Action] dry_run remove_partner -> skip")
        return "skip"

    modal = page.locator(MODAL_BODY)

    removed = remove_partner_by_chip(page, partner)

    if not removed:
        log.info("ℹ️ [Partners] partner not selected, checking remaining")

        input_el = modal.locator("input[placeholder='Партнеры']")
        multiselect = input_el.locator(
            "xpath=ancestor::div[contains(@class,'multiselect')]"
        )

        chips = multiselect.locator(".multiselect__tag")
        remaining = chips.count()

        log.info(f"🧩 [Partners] remaining={remaining}")

        return "skip: not selected"

    # 🔥 проверяем сколько осталось партнёров
    input_el = modal.locator("input[placeholder='Партнеры']")
    multiselect = input_el.locator(
        "xpath=ancestor::div[contains(@class,'multiselect')]"
    )

    chips = multiselect.locator(".multiselect__tag")
    remaining = chips.count()

    log.info(f"🧩 [Partners] remaining after removal={remaining}")

    return "removed"


def ensure_partner_added(page: Page, partner: str, cfg: RunConfig):
    log.info(f"🧩 [Action] add_partner value={partner}")

    if cfg.dry_run:
        log.info("ℹ️ [Action] dry_run add_partner -> skip")
        return "skip"

    modal = page.locator(MODAL_BODY)

    chip_pairs = get_partner_chips(page)
    chip_texts = [text for _, text in chip_pairs]
    log.info(f"🏷️ [Partners] current chips count={len(chip_texts)}")

    if _partner_already_selected(chip_texts, partner):
        log.info(f"ℹ️ [Partners] partner already attached → skip add partner={partner}")
        return f"skip: partner already added: {partner}"

    log.info(f"➕ [Partners] partner not attached → adding partner={partner}")

    # 🔥 ИЩЕМ ИМЕННО ПОЛЕ "Партнеры"
    input_el = modal.locator("input[placeholder='Партнеры']")

    multiselect = input_el.locator(
        "xpath=ancestor::div[contains(@class,'multiselect') and not(contains(@class,'multiselect__tags'))]"
    ).first

    log.info("🎯 [Partner] using multiselect 'Партнеры'")

    # 🔓 ОТКРЫТЬ DROPDOWN
    multiselect.scroll_into_view_if_needed()
    multiselect.wait_for(state="visible", timeout=3000)

    try:
        multiselect.click()
    except:
        log.warning("⚠️ click failed → force")
        multiselect.click(force=True)

    # 🔥 ВАЖНО: dropdown берём ГЛОБАЛЬНО (Vue рисует вне блока)
    dropdown = page.locator(".multiselect__content-wrapper:visible")
    dropdown.wait_for(state="visible", timeout=5000)

    options = dropdown.locator("li")

    log.info("📜 [Partner] start scrolling search")

    found = None
    last_count = -1

    # 🔥 SCROLL ПО СПИСКУ
    for _ in range(25):
        visible_count = options.count()
        log.info(f"📂 [Partner] visible options={visible_count}")

        for i in range(visible_count):
            option = options.nth(i)
            text = option.inner_text().strip()

            if partner.lower() in text.lower():
                found = option
                log.info(f"🎯 [Partner] found option={text}")
                break

        if found:
            break

        if visible_count == last_count:
            log.info("⛔ [Partner] reached end of list")
            break

        last_count = visible_count

        dropdown.evaluate("el => el.scrollTop = el.scrollHeight")
        page.wait_for_timeout(300)

    if not found:
        log.warning(f"⚠️ [Partner] not found after scroll partner={partner}")
        return "skip: option not found"

    # 🔥 КЛИК ПО ПАРТНЕРУ
    found.scroll_into_view_if_needed()
    found.wait_for(state="visible", timeout=3000)

    try:
        found.click()
    except:
        log.warning("⚠️ option click failed → force")
        found.click(force=True)

    log.info(f"✅ [Partner] partner added={partner}")
    return "added"


def ensure_group_added(page: Page, group: str, cfg: RunConfig):
    log.info(f"🧩 [Action] add_group value={group}")

    normalized = (group or "").strip()
    if not normalized:
        raise Exception("пустое значение для add_group")

    if cfg.dry_run:
        log.info("ℹ️ [Action] dry_run add_group -> skip")
        return f"skip: set {normalized}"

    multiselect = _get_group_multiselect(page)
    selected = _get_selected_multiselect_labels(multiselect, "Group")

    if _group_already_selected(selected, normalized):
        log.info(f"ℹ️ [Group] already selected → skip group={normalized}")
        return "skip: group already selected"

    if not _multiselect_add_option(page, multiselect, normalized, "Group"):
        raise Exception(f"Группа не найдена: {group}")

    return f"added: {normalized}"


def ensure_group_set(page: Page, group: str, cfg: RunConfig):
    log.info(f"🧩 [Action] set_group value={group}")

    normalized = (group or "").strip()
    if not normalized:
        raise Exception("пустое значение для set_group")

    if cfg.dry_run:
        log.info("ℹ️ [Action] dry_run set_group -> skip")
        return f"skip: set {normalized}"

    multiselect = _get_group_multiselect(page)
    target_norm = _normalize_label_text(normalized)

    selected = _get_selected_multiselect_labels(multiselect, "Group")
    if _normalized_group_set(selected) == {target_norm}:
        log.info(f"ℹ️ [Group] only target selected → skip group={normalized}")
        return "skip: group already set"

    if not _group_already_selected(selected, normalized):
        if not _multiselect_add_option(page, multiselect, normalized, "Group"):
            raise Exception(f"Группа не найдена: {group}")

    for _ in range(10):
        selected = _get_selected_multiselect_labels(multiselect, "Group")
        extras = [
            label for label in selected if not _partner_matches_chip(label, normalized)
        ]
        if not extras:
            break
        for extra in extras:
            _multiselect_remove_label(multiselect, extra)
        page.wait_for_timeout(200)

    final_selected = _get_selected_multiselect_labels(multiselect, "Group")
    final_set = _normalized_group_set(final_selected)
    if final_set != {target_norm}:
        raise Exception(
            f"финальное состояние групп не совпало: {sorted(final_set)} != {{{normalized}}}"
        )

    log.info(f"✅ [Group] group set to {normalized}")
    return f"set: {normalized}"


def ensure_groups_cleared(page: Page, value: str, cfg: RunConfig):
    log.info(f"🧩 [Action] clear_groups value={value!r}")

    if cfg.dry_run:
        log.info("ℹ️ [Action] dry_run clear_groups -> skip")
        return "skip: clear groups"

    multiselect = _get_group_multiselect(page)
    selected = _get_selected_multiselect_labels(multiselect, "Group")
    if not _normalized_group_set(selected):
        log.info("ℹ️ [Group] no groups selected → skip clear_groups")
        return "skip: no groups selected"

    for _ in range(10):
        selected = _get_selected_multiselect_labels(multiselect, "Group")
        remaining = [label for label in selected if (label or "").strip()]
        if not remaining:
            break
        for label in list(remaining):
            _multiselect_remove_label(multiselect, label)
        page.wait_for_timeout(200)

    final_selected = _get_selected_multiselect_labels(multiselect, "Group")
    if _normalized_group_set(final_selected):
        raise Exception(
            f"группы не удалены полностью: {sorted(_normalized_group_set(final_selected))}"
        )

    log.info("✅ [Group] all groups cleared")
    return "cleared: groups"


def ensure_status_set(page: Page, status: str, cfg: RunConfig):
    log.info(f"🧩 [Action] set_status value={status}")

    normalized = (status or "").strip()

    if normalized not in ALLOWED_STATUSES:
        log.error(f"❌ [Status] unknown status={status}")
        raise Exception(f"Недопустимый статус: {status}")

    if cfg.dry_run:
        log.info("ℹ️ [Action] dry_run set_status -> skip")
        return f"skip: set {normalized}"

    # 🔍 ищем нужный select по option тексту
    selects = page.locator(f"{MODAL_BODY} select")
    count = selects.count()

    log.info(f"🎯 [Status] total selects={count}")

    select = None

    for i in range(count):
        s = selects.nth(i)
        options = s.locator("option")

        texts = []
        for j in range(options.count()):
            txt = options.nth(j).inner_text().strip()
            texts.append(txt)

        # ключевая проверка
        if normalized in texts:
            select = s
            log.info(f"✅ [Status] found select index={i}")
            break

    if not select:
        raise Exception("❌ select статуса не найден")

    current_value = select.input_value()
    log.info(f"📌 [Status] current_value={current_value} target={normalized}")

    # 🔄 меняем статус через label
    select.select_option(label=normalized)

    log.info(f"✅ [Status] status changed to {normalized}")
    return f"set: {normalized}"


def ensure_direction_set(page: Page, direction: str, cfg: RunConfig):
    log.info(f"🧩 [Action] set_direction value={direction}")

    normalized = (direction or "").strip()
    if not normalized:
        raise Exception("пустое значение для set_direction")

    if cfg.dry_run:
        log.info("ℹ️ [Action] dry_run set_direction -> skip")
        return f"skip: set {normalized}"

    select = _find_direction_select(page)
    if not select:
        raise Exception("select направления не найден")

    options = _get_select_options(select)
    resolved = _resolve_direction_option(options, normalized)
    if not resolved:
        log.error(f"❌ [Direction] unknown direction={direction}")
        raise Exception(f"Направление не найдено: {direction}")

    option_value, option_text = resolved
    current_value, current_text = _get_selected_option(select)
    log.info(
        "📌 [Direction] "
        f"current_value={current_value!r} current_text={current_text!r} "
        f"target={normalized!r} resolved_value={option_value!r} resolved_text={option_text!r}"
    )

    if _selection_matches_direction(
        current_value, current_text, option_value, option_text
    ):
        log.info(f"ℹ️ [Direction] already selected → skip direction={normalized}")
        return f"skip: direction already {normalized}"

    if option_value:
        select.select_option(value=option_value)
    else:
        select.select_option(label=option_text)

    log.info(f"✅ [Direction] direction changed to {normalized}")
    return f"set: {normalized}"


def _validate_set_direction_pre_playwright(df: pd.DataFrame) -> int:
    empty_value_messages = {
        "set_direction": "пустое значение для set_direction",
        "add_group": "пустое значение для add_group",
        "set_group": "пустое значение для set_group",
    }
    fail_count = 0
    for idx, row in df.iterrows():
        if str(row.get("status", "")).strip() == "FAIL":
            continue
        message = empty_value_messages.get(row["action"])
        if not message:
            continue
        if str(row["value"]).strip():
            continue
        df.at[idx, "status"] = "FAIL"
        df.at[idx, "comment"] = message
        fail_count += 1
        log.error(f"❌ [Input] row={idx} empty {row['action']} value")
    return fail_count


def save(page: Page, cfg: RunConfig):
    log.info("💾 [Save] saving card changes")
    if cfg.dry_run:
        log.info("ℹ️ [Save] dry_run -> skip")
        return "skip"

    page.click(SAVE_BUTTON)
    page.wait_for_selector(MODAL_BODY, state="hidden", timeout=10000)
    log.info("✅ [Save] modal closed after save")
    return "saved"


def _prepare_df(file_path: str) -> pd.DataFrame:
    log.info(f"📥 [Input] reading excel file={file_path}")
    df = pd.read_excel(file_path)

    required = {"card", "action"}
    missing = required - set(df.columns)
    if missing:
        raise Exception(f"Отсутствуют обязательные колонки: {sorted(missing)}")

    if "value" not in df.columns:
        df["value"] = ""

    if "status" not in df.columns:
        df["status"] = ""
    if "comment" not in df.columns:
        df["comment"] = ""

    df["card"] = df["card"].astype(str).str.strip()
    df["action"] = df["action"].astype(str).str.strip().str.lower()

    values = []
    for idx, row in df.iterrows():
        action = row["action"]
        raw = row["value"]
        if action in GROUP_VALUE_ACTIONS:
            value, error = _coerce_group_excel_value(raw)
            if error:
                df.at[idx, "status"] = "FAIL"
                df.at[idx, "comment"] = error
            values.append(value)
        elif pd.isna(raw):
            values.append("")
        else:
            values.append(str(raw).strip())
    df["value"] = values

    bad_actions = sorted({a for a in df["action"].unique() if a not in ALLOWED_ACTIONS})
    if bad_actions:
        raise Exception(f"Недопустимые action: {bad_actions}")

    log.info(f"✅ [Input] dataframe loaded rows={len(df)} columns={list(df.columns)}")
    return df


def _validate_delete_conflicts(df: pd.DataFrame) -> int:
    """Reject cards where delete is mixed with other actions (or duplicated)."""
    fail_count = 0
    for card, group in df.groupby(df["card"].astype(str), sort=False):
        actions = [str(a).strip().lower() for a in group["action"].tolist()]
        if "delete" not in actions:
            continue
        if len(group) == 1 and actions == ["delete"]:
            continue
        for idx in group.index:
            if _row_already_resolved(df.at[idx, "status"]):
                continue
            df.at[idx, "status"] = RESULT_FAIL_DELETE_CONFLICT
            df.at[idx, "comment"] = (
                "delete должен быть единственным действием для карты в одном запуске"
            )
            fail_count += 1
            log.error(
                "❌ [Input] delete conflict card=%s row=%s actions=%s",
                mask_card(str(card)),
                idx,
                actions,
            )
    return fail_count


def _row_already_resolved(status: object) -> bool:
    s = str(status or "").strip().upper()
    if not s:
        return False
    return s in {"FAIL", "SKIP"} or s.startswith("FAIL_") or s.startswith("SKIP_")


def _count_pre_playwright_fails(df: pd.DataFrame) -> int:
    return int(
        df["status"]
        .astype(str)
        .map(lambda s: str(s).strip().upper().startswith("FAIL"))
        .sum()
    )


def _ensure_result_date_columns(df: pd.DataFrame) -> None:
    for col_idx, col in enumerate((OPERATION_DATE_COLUMN, DISABLE_DATE_COLUMN)):
        if col in df.columns:
            series = df.pop(col)
            df.insert(col_idx, col, series)
        else:
            df.insert(col_idx, col, "")


def _apply_result_row_dates(
    df: pd.DataFrame,
    idx: int,
    *,
    action: str,
    status: str,
    processed_at,
) -> None:
    operation_date, disable_date = result_row_dates(action, status, processed_at)
    df.at[idx, OPERATION_DATE_COLUMN] = operation_date
    df.at[idx, DISABLE_DATE_COLUMN] = disable_date


def _apply_add_partner_hold_precheck(
    df: pd.DataFrame,
    hold_snapshot: HoldPairsSnapshot,
    stats: Stats,
) -> None:
    now = now_msk()
    operation_date = now.strftime(EXCEL_DATE_FORMAT)
    for idx, row in df.iterrows():
        status = str(row.get("status", "")).strip().upper()
        if _row_already_resolved(status):
            continue
        if str(row.get("action", "")).strip().lower() != "add_partner":
            continue

        card = str(row.get("card", "")).strip()
        partner = str(row.get("value", "")).strip()

        if not hold_snapshot.available:
            log.error(
                "[Hold] blocked add_partner card=%s partner=%s reason=hold_check_failed error=%s",
                mask_card(card),
                partner,
                hold_snapshot.error,
            )
            df.at[idx, "status"] = "SKIP"
            df.at[idx, "comment"] = HOLD_CHECK_FAILED_MANUAL_COMMENT
            df.at[idx, OPERATION_DATE_COLUMN] = operation_date
            df.at[idx, DISABLE_DATE_COLUMN] = ""
            stats.skip += 1
            continue

        if is_card_partner_on_hold(card, partner, hold_snapshot.pairs):
            log.info(
                "[Hold] blocked add_partner card=%s partner=%s reason=on_hold",
                mask_card(card),
                partner,
            )
            df.at[idx, "status"] = "SKIP"
            df.at[idx, "comment"] = HOLD_SKIP_COMMENT
            df.at[idx, OPERATION_DATE_COLUMN] = operation_date
            df.at[idx, DISABLE_DATE_COLUMN] = ""
            stats.skip += 1


def _group_actions(df: pd.DataFrame):
    grouped = defaultdict(list)
    for idx, row in df.iterrows():
        if _row_already_resolved(row.get("status", "")):
            continue
        grouped[str(row["card"])].append((idx, row["action"], row["value"]))
    log.info(f"🗂️ [Input] grouped cards={len(grouped)}")
    return grouped


def _write_result(df: pd.DataFrame, out_path: str | None = None) -> str:
    if out_path is None:
        fd, out_path = tempfile.mkstemp(prefix="wallet_editor_result_", suffix=".xlsx", dir=BASE_DIR)
        os.close(fd)
    else:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out_path, index=False)
    log.info(f"📤 [Output] result file written path={out_path}")
    return out_path


def run(file_path: str, cfg: RunConfig):
    profile = (cfg.operator_profile or "unknown").strip()
    log.info(f"🚀 [Run] wallet editor started file={file_path} profile={profile}")
    require_wallet_editor_antares_credentials(cfg)

    with log_step_duration(profile=profile, scope="disable", step="run_total"):
        stats = Stats()
        df = _prepare_df(file_path)

        # гарантируем, что колонки дат есть и они первые
        _ensure_result_date_columns(df)

        _validate_set_direction_pre_playwright(df)
        _validate_delete_conflicts(df)
        hold_snapshot = load_hold_pairs_snapshot()
        _apply_add_partner_hold_precheck(df, hold_snapshot, stats)
        stats.fail += _count_pre_playwright_fails(df)

        grouped = _group_actions(df)

        slow_mo = wallet_editor_playwright_slow_mo_ms()
        if cfg.slow_mo_ms is not None:
            slow_mo = int(cfg.slow_mo_ms)
        log.info("[WalletEditor] playwright slow_mo_ms=%s profile=%s scope=disable", slow_mo, profile)

        with sync_playwright() as p:
            browser = None
            context = None
            page = None
            try:
                log.info(f"🌐 [Browser] launching chromium headless={cfg.headless}")
                browser = p.chromium.launch(
                    headless=cfg.headless,
                    slow_mo=slow_mo,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )

                if os.path.exists(cfg.auth_state_path):
                    log.info(f"🔐 [Browser] using storage_state={cfg.auth_state_path}")
                    context = browser.new_context(storage_state=cfg.auth_state_path)
                else:
                    log.info("🔐 [Browser] storage_state not found, using clean context")
                    context = browser.new_context()

                page = context.new_page()
                _ensure_logged_in(page, context, cfg)

                for card, actions in grouped.items():
                    log.info(f"🃏 [Card] start card={card} actions={len(actions)}")

                    if not actions:
                        log.info(f"ℹ️ [Card] skip card={card} — no runnable actions")
                        continue

                    is_delete_card = len(actions) == 1 and actions[0][1] == "delete"

                    card_failed = False
                    card_mutated = False

                    with log_step_duration(
                        profile=profile, scope="disable", step="card", card=card
                    ) as card_timing:
                        if is_delete_card:
                            idx, action, _value = actions[0]
                            processed_at = now_msk()
                            log.info(
                                "➡️ [Card] processing row=%s card=%s action=delete",
                                idx,
                                card,
                            )
                            try:
                                with log_step_duration(
                                    profile=profile,
                                    scope="disable",
                                    step="action:delete",
                                    card=card,
                                ) as action_timing:
                                    result = ensure_wallet_deleted(page, card, cfg)
                                    action_timing.outcome = timing_outcome_from_result(
                                        result
                                    )
                                status = _status_from_delete_result(result)
                                df.at[idx, "status"] = status
                                df.at[idx, "comment"] = result
                                _apply_result_row_dates(
                                    df,
                                    idx,
                                    action=action,
                                    status=status,
                                    processed_at=processed_at,
                                )
                                stats.inc(result)
                                card_timing.outcome = timing_outcome_from_result(result)
                                log.info("✅ [Card] row=%s result=%s", idx, result)
                            except Exception as e:
                                df.at[idx, "status"] = RESULT_FAIL_TECHNICAL
                                df.at[idx, "comment"] = str(e)
                                _apply_result_row_dates(
                                    df,
                                    idx,
                                    action=action,
                                    status=RESULT_FAIL_TECHNICAL,
                                    processed_at=processed_at,
                                )
                                stats.fail += 1
                                card_timing.outcome = "fail"
                                log.exception(
                                    "❌ [Card] delete failed card=%s: %s", card, e
                                )
                            # Leave page clean for the next card (no leftover form).
                            if not cfg.stop_before_delete:
                                try:
                                    ensure_wallet_search_ready(page, allow_goto=True)
                                except Exception as cleanup_exc:
                                    log.warning(
                                        "⚠️ [Delete] post-row UI cleanup failed card=%s: %s",
                                        mask_card(card),
                                        cleanup_exc,
                                    )
                            log.info(f"✅ [Card] finished card={card}")
                            continue

                        try:
                            with log_step_duration(
                                profile=profile,
                                scope="disable",
                                step="open_card",
                                card=card,
                            ):
                                retry(
                                    lambda: open_card(page, card),
                                    cfg.retries,
                                    cfg.delay,
                                    step_name=f"open_card:{card}",
                                )

                            for idx, action, value in actions:
                                log.info(f"➡️ [Card] processing row={idx} card={card} action={action} value={value}")

                                # единая точка времени (MSK, время обработки строки)
                                processed_at = now_msk()

                                try:
                                    with log_step_duration(
                                        profile=profile,
                                        scope="disable",
                                        step=f"action:{action}",
                                        card=card,
                                    ) as action_timing:
                                        if action == "remove_partner":
                                            result = ensure_partner_removed(page, value, cfg)
                                        elif action == "add_partner":
                                            result = ensure_partner_added(page, value, cfg)
                                        elif action == "set_status":
                                            result = ensure_status_set(page, value, cfg)
                                        elif action == "set_direction":
                                            result = ensure_direction_set(page, value, cfg)
                                        elif action == "add_group":
                                            result = ensure_group_added(page, value, cfg)
                                        elif action == "set_group":
                                            result = ensure_group_set(page, value, cfg)
                                        elif action == "clear_groups":
                                            result = ensure_groups_cleared(page, value, cfg)
                                        elif action == "delete":
                                            # Conflict validation should prevent this path.
                                            result = RESULT_FAIL_DELETE_CONFLICT
                                        else:
                                            result = "skip: unsupported action"
                                        action_timing.outcome = timing_outcome_from_result(
                                            result
                                        )

                                    if action == "delete":
                                        status = _status_from_delete_result(result)
                                    else:
                                        status = "OK" if not result.startswith("skip") else "SKIP"
                                    if status == "OK":
                                        card_mutated = True

                                    df.at[idx, "status"] = status
                                    df.at[idx, "comment"] = result
                                    _apply_result_row_dates(
                                        df,
                                        idx,
                                        action=action,
                                        status=status,
                                        processed_at=processed_at,
                                    )

                                    stats.inc(result)

                                    log.info(f"✅ [Card] row={idx} result={result}")

                                    # delete is terminal for the card
                                    if action == "delete" and status == RESULT_OK_DELETED:
                                        break

                                except Exception as e:
                                    df.at[idx, "status"] = "FAIL"
                                    df.at[idx, "comment"] = str(e)
                                    _apply_result_row_dates(
                                        df,
                                        idx,
                                        action=action,
                                        status="FAIL",
                                        processed_at=processed_at,
                                    )

                                    stats.fail += 1
                                    log.exception(f"❌ [Card] row={idx} failed card={card}: {e}")

                            explicit_status_in_excel = any(
                                action == "set_status" for _, action, _ in actions
                            )
                            if _apply_auto_no_partners_status_after_actions(
                                page, cfg, explicit_status_in_excel
                            ):
                                card_mutated = True

                            if card_mutated:
                                with log_step_duration(
                                    profile=profile,
                                    scope="disable",
                                    step="save",
                                    card=card,
                                ):
                                    retry(
                                        lambda: save(page, cfg),
                                        cfg.retries,
                                        cfg.delay,
                                        step_name=f"save:{card}",
                                    )
                            else:
                                log.info(f"ℹ️ [Card] skip save card={card} — no mutations")

                            log.info(f"✅ [Card] finished card={card}")

                        except Exception as e:
                            card_failed = True
                            card_timing.outcome = "fail"

                            log.exception(f"❌ [Card] fatal failure card={card}: {e}")

                            processed_at = now_msk()

                            for idx, action, _ in actions:
                                if not str(df.at[idx, "status"]).strip():
                                    df.at[idx, "status"] = "FAIL"
                                    df.at[idx, "comment"] = str(e)
                                    _apply_result_row_dates(
                                        df,
                                        idx,
                                        action=action,
                                        status="FAIL",
                                        processed_at=processed_at,
                                    )

                                    stats.fail += 1

                    if card_failed:
                        log.warning(f"⚠️ [Card] card marked as failed card={card}")
            finally:
                close_playwright_stack(page=page, context=context, browser=browser)
                log.info("🛑 [Browser] playwright stack closed")

        out_path = _write_result(df, cfg.result_file_path)

    log.info(f"🏁 [Run] completed summary={stats.summary()}")

    return out_path, stats
