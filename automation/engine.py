from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import os
import re
import tempfile

import pandas as pd
from playwright.sync_api import Page, sync_playwright

from core.playwright_cleanup import close_playwright_stack

from automation.audit import Stats, log, log_step_duration
from automation.runtime import (
    RunConfig,
    require_wallet_editor_antares_credentials,
    retry,
    wallet_editor_playwright_slow_mo_ms,
)
from core.datetime_utils import EXCEL_DATETIME_FORMAT, now_msk


BASE_DIR = "/tmp"

CARD_INPUT = 'input[placeholder="Карта"]'
APPLY_BUTTON = 'button:has-text("Применить")'
ROW_SELECTOR = "tr.pointer"
MODAL_BODY = "#wallet-add-modal___BV_modal_body_"
MODAL_CLOSE_BUTTON = "#wallet-add-modal___BV_modal_header_ button.close"
SAVE_BUTTON = 'button:has-text("Сохранить")'

_WALLET_UI_READY_TIMEOUT_MS = 15_000
_AUTH_NETWORKIDLE_TIMEOUT_MS = 60_000

ALLOWED_ACTIONS = {
    "remove_partner",
    "add_partner",
    "set_status",
    "set_direction",
    "add_group",
    "set_group",
    "clear_groups",
}
DIRECTION_LABEL = "Направление"
GROUP_LABELS = ("Группа", "Группы")
GROUP_VALUE_ACTIONS = frozenset({"add_group", "set_group"})
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
    page.goto("https://antares.plus/lkcard/#/wallet")

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
    return "".join(c for c in (value or "") if c.isdigit())


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


def open_card(page: Page, card: str) -> None:
    modal = page.locator(MODAL_BODY)

    _close_stale_modal(page)

    log.info(f"🔎 [Card] searching card={card}")
    page.fill(CARD_INPUT, "")
    page.fill(CARD_INPUT, card)

    log.info(f"🖱️ [Card] applying filter for card={card}")
    page.click(APPLY_BUTTON)
    page.wait_for_selector(ROW_SELECTOR, timeout=10000)

    rows = page.locator(ROW_SELECTOR)
    row_count = rows.count()
    log.info(f"📋 [Card] rows found={row_count} for card={card}")

    for i in range(row_count):
        row = rows.nth(i)
        row_text = row.inner_text()
        if card in row_text:
            log.info(f"✅ [Card] matched row index={i} for card={card}")
            row.click()
            break
    else:
        log.error(f"❌ [Card] not found in table card={card}")
        raise Exception("Карта не найдена")

    modal.wait_for(state="visible", timeout=10000)

    modal_card_value = _try_get_modal_card_value(page)
    if modal_card_value:
        card_digits = _digits_only(card)
        modal_digits = _digits_only(modal_card_value)
        if card_digits and modal_digits and card_digits != modal_digits:
            log.error(
                f"❌ [Card] modal card mismatch expected={card} modal={modal_card_value}"
            )
            raise Exception(f"Модалка не соответствует карте: {card}")
        if card_digits and modal_digits:
            log.info(f"✅ [Card] modal card verified card={card}")
        else:
            log.warning(
                "⚠️ [Card] unable to verify modal card value; continuing after stale modal close"
            )
    else:
        log.warning(
            "⚠️ [Card] unable to verify modal card value; continuing after stale modal close"
        )

    log.info(f"✅ [Card] card modal opened card={card}")


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

    required = {"card", "action", "value"}
    missing = required - set(df.columns)
    if missing:
        raise Exception(f"Отсутствуют обязательные колонки: {sorted(missing)}")

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


def _group_actions(df: pd.DataFrame):
    grouped = defaultdict(list)
    for idx, row in df.iterrows():
        if str(row.get("status", "")).strip() == "FAIL":
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

        # 🔥 гарантируем, что колонка есть и она первая
        if "Дата отключения" not in df.columns:
            df.insert(0, "Дата отключения", "")
        else:
            col = df.pop("Дата отключения")
            df.insert(0, "Дата отключения", col)

        _validate_set_direction_pre_playwright(df)
        stats.fail += int((df["status"] == "FAIL").sum())

        grouped = _group_actions(df)

        slow_mo = wallet_editor_playwright_slow_mo_ms()
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

                    card_failed = False
                    card_mutated = False

                    with log_step_duration(
                        profile=profile, scope="disable", step="card", card=card
                    ):
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

                                # 🔥 единая точка времени (MSK, время обработки строки)
                                now_str = now_msk().strftime(EXCEL_DATETIME_FORMAT)

                                try:
                                    with log_step_duration(
                                        profile=profile,
                                        scope="disable",
                                        step=f"action:{action}",
                                        card=card,
                                    ):
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
                                        else:
                                            result = "skip: unsupported action"

                                    status = "OK" if not result.startswith("skip") else "SKIP"
                                    if status == "OK":
                                        card_mutated = True

                                    df.at[idx, "status"] = status
                                    df.at[idx, "comment"] = result

                                    # 🔥 ВСЕГДА ставим дату
                                    df.at[idx, "Дата отключения"] = now_str

                                    stats.inc(result)

                                    log.info(f"✅ [Card] row={idx} result={result}")

                                except Exception as e:
                                    df.at[idx, "status"] = "FAIL"
                                    df.at[idx, "comment"] = str(e)

                                    # 🔥 ВСЕГДА ставим дату
                                    df.at[idx, "Дата отключения"] = now_str

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

                            log.exception(f"❌ [Card] fatal failure card={card}: {e}")

                            now_str = now_msk().strftime(EXCEL_DATETIME_FORMAT)

                            for idx, _, _ in actions:
                                if not str(df.at[idx, "status"]).strip():
                                    df.at[idx, "status"] = "FAIL"
                                    df.at[idx, "comment"] = str(e)

                                    # 🔥 ВСЕГДА ставим дату
                                    df.at[idx, "Дата отключения"] = now_str

                                    stats.fail += 1

                    if card_failed:
                        log.warning(f"⚠️ [Card] card marked as failed card={card}")
            finally:
                close_playwright_stack(page=page, context=context, browser=browser)
                log.info("🛑 [Browser] playwright stack closed")

        out_path = _write_result(df, cfg.result_file_path)

    log.info(f"🏁 [Run] completed summary={stats.summary()}")

    return out_path, stats
