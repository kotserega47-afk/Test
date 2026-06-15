# integrations/bakai_monitor_playwright.py
import os
import re
from datetime import datetime, time
from core.datetime_utils import now_msk
from playwright.sync_api import sync_playwright
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from core.playwright_cleanup import close_playwright_stack
from integrations.telegram_bot import send_message_sync
from integrations.telegram_routes import (
    ENV_BAKAI_RATE_ALERT_LEGACY,
    ENV_BAKAI_RATE_CURRENT_LEGACY,
    ROUTE_BAKAI_RATE_ALERT,
    ROUTE_BAKAI_RATE_CURRENT,
    routes_from_rules_v2_enabled,
    send_file_to_route,
    send_message_to_route,
)
from zoneinfo import ZoneInfo
import time as time_module
import random

icon, name = LOG_PROFILES["RATE"]
logger = get_logger(name, icon)

URL = "https://bakai.kg/ru/"
LAST_RATE_FILE = "/tmp/bakai_last_buy_rate.txt"
MAX_SCROLL_STEPS = 60
SCROLL_DELTA_PX = 250
SCROLL_WAIT_MS = 350
RATE_FLOAT_PATTERN = re.compile(r"\d+[.,]\d+")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

MSK = ZoneInfo("Europe/Moscow")


def _legacy_env_chat(env_name: str) -> str | None:
    raw = os.getenv(env_name, "").strip()
    return raw if raw else None


def _send_to_current_route(text: str, *, override_chat_id: str | None = None) -> None:
    if override_chat_id and not routes_from_rules_v2_enabled():
        send_message_sync(text, chat_id=override_chat_id)
        return
    if routes_from_rules_v2_enabled():
        send_message_to_route(ROUTE_BAKAI_RATE_CURRENT, text)
        return
    chat_id = _legacy_env_chat(ENV_BAKAI_RATE_CURRENT_LEGACY)
    if not chat_id:
        logger.warning(
            "[rate_monitor] %s not set — skip current-rate send",
            ENV_BAKAI_RATE_CURRENT_LEGACY,
        )
        return
    send_message_sync(text, chat_id=chat_id)


def _send_to_alert_route(text: str) -> None:
    if routes_from_rules_v2_enabled():
        send_message_to_route(ROUTE_BAKAI_RATE_ALERT, text)
        return
    chat_id = _legacy_env_chat(ENV_BAKAI_RATE_ALERT_LEGACY)
    if not chat_id:
        logger.warning(
            "[rate_monitor] %s not set — skip rate-alert send",
            ENV_BAKAI_RATE_ALERT_LEGACY,
        )
        return
    send_message_sync(text, chat_id=chat_id)


def _send_file_to_current_route(path: str, caption: str | None) -> None:
    if routes_from_rules_v2_enabled():
        send_file_to_route(ROUTE_BAKAI_RATE_CURRENT, path, caption)
        return
    chat_id = _legacy_env_chat(ENV_BAKAI_RATE_CURRENT_LEGACY)
    if not chat_id:
        logger.warning(
            "[rate_monitor] %s not set — skip screenshot send",
            ENV_BAKAI_RATE_CURRENT_LEGACY,
        )
        return
    from integrations.telegram_bot import send_file_sync

    send_file_sync(path, caption, chat_id=chat_id)


# ------------------------ вспомогательные ------------------------
class RateMonitorError(Exception):
    def __init__(self, message, screenshot_path=None):
        super().__init__(message)
        self.screenshot_path = screenshot_path

def _load_rate():
    """Загружает последний сохранённый курс"""
    try:
        if not os.path.exists(LAST_RATE_FILE):
            return None
        return float(open(LAST_RATE_FILE).read().strip())
    except Exception:
        return None


def _save_rate(v: float):
    """Сохраняет текущий курс"""
    try:
        with open(LAST_RATE_FILE, "w") as f:
            f.write(str(v))
    except Exception as e:
        logger.error(f"[rate_monitor] Ошибка при сохранении курса: {e}")


def _in_time_window() -> bool:
    """Проверяет, входит ли текущее время в окно 08:00–23:55"""
    now = datetime.now(MSK).time()
    return time(8, 0) <= now <= time(23, 55)


def normalize_rate_text(text: str) -> str:
    """Strip NBSP/spaces and normalize decimal separator for rate parsing."""
    return text.replace("\xa0", "").replace(" ", "").replace(",", ".")


def parse_buy_rate_text(buy_text: str) -> float:
    """Parse buy-rate float from raw cell text."""
    normalized = normalize_rate_text(buy_text)
    match = RATE_FLOAT_PATTERN.search(normalized)
    if not match:
        raise RuntimeError(f"buy rate parse failed: cannot extract rate from {buy_text!r}")
    return float(match.group().replace(",", "."))


def _find_visible_transfer_select(page):
    """Scroll until a visible select with option[value=transfer] appears."""
    operation_select = None
    last_count = 0
    for step in range(MAX_SCROLL_STEPS):
        transfer_selects = page.locator("select:visible").filter(
            has=page.locator("option[value='transfer']")
        )
        last_count = transfer_selects.count()
        logger.info(
            "[rate_monitor] scroll step=%s, visible transfer selects=%s",
            step,
            last_count,
        )
        if last_count > 0:
            operation_select = transfer_selects.first
            return operation_select, step, last_count
        page.mouse.wheel(0, SCROLL_DELTA_PX)
        page.wait_for_timeout(SCROLL_WAIT_MS)
    return None, MAX_SCROLL_STEPS, last_count


def _find_rub_row(rates_table, rows_count: int):
    """Locate RUB row inside scoped rates table."""
    rub_rows = rates_table.locator("tbody tr:has(img[src*='rub'])")
    if rub_rows.count() > 0:
        return rub_rows.first
    if rows_count >= 3:
        return rates_table.locator("tbody tr").nth(2)
    raise RuntimeError(
        f"RUB row not found: rub image row missing and only {rows_count} table rows"
    )


def _extract_rub_buy_rate_from_loaded_page(page) -> float:
    """Extract RUB buy rate from an already-loaded Bakai page."""
    operation_select, scroll_steps, visible_count = _find_visible_transfer_select(page)
    if operation_select is None:
        raise RuntimeError(
            f"transfer select not found after {scroll_steps} scroll steps "
            f"(last visible transfer selects={visible_count})"
        )

    operation_select.wait_for(state="visible", timeout=15000)
    logger.info(
        "[rate_monitor] operation_select enabled=%s",
        operation_select.is_enabled(),
    )
    operation_select.select_option(value="transfer", timeout=15000)
    page.wait_for_timeout(1500)
    logger.info("[rate_monitor] Выбран режим Онлайн переводы")

    rates_table = operation_select.locator(
        "xpath=ancestor::div[contains(@class, 'CurrencyWidget_widget_content')][1]//table"
    ).first
    rates_table.wait_for(state="visible", timeout=15000)

    rows = rates_table.locator("tbody tr")
    rows_count = rows.count()
    logger.info("[rate_monitor] Найдено строк курсов: %s", rows_count)
    if rows_count < 2:
        raise RuntimeError(f"RUB row not found: insufficient table rows ({rows_count})")

    rub_row = _find_rub_row(rates_table, rows_count)
    row_text = rub_row.inner_text().strip()
    logger.info("[rate_monitor] Строка RUB: %r", row_text)

    cells = rub_row.locator("th, td")
    cells_count = cells.count()
    logger.info("[rate_monitor] Количество ячеек RUB: %s", cells_count)
    if cells_count < 2:
        raise RuntimeError(f"RUB row not found: invalid row structure: {row_text!r}")

    buy_text = cells.nth(1).inner_text().strip()
    logger.info("[rate_monitor] Сырой курс покупки RUB: %r", buy_text)

    buy_rate = parse_buy_rate_text(buy_text)
    logger.info("[rate_monitor] Курс покупки RUB: %s", buy_rate)
    return buy_rate


def _scrape_rub_buy_rate_from_page(page) -> float:
    """Scrape RUB buy rate using Platform_2.0 scroll + scoped widget logic."""
    page.goto(URL, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)

    logger.info("[rate_monitor] page url: %s", page.url)
    logger.info("[rate_monitor] title: %s", page.title())

    return _extract_rub_buy_rate_from_loaded_page(page)


# -------------------------- основной мониторинг --------------------------

def check_bakai_rate(chat_id: str | None = None):
    if not _in_time_window():
        logger.info("[rate_monitor] Вне окна 08:00–23:55 — пропускаем.")
        return

    logger.info("[rate_monitor] === Проверка курса покупки RUB ===")

    browser = None
    context = None
    page = None
    buy_rate = None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )

            context = browser.new_context(
                user_agent=UA,
                viewport={"width": 1440, "height": 900},
                locale="ru-RU",
            )

            # маскировка под обычный браузер
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)

            page = context.new_page()
            buy_rate = _scrape_rub_buy_rate_from_page(page)
    except Exception as e:
        logger.error(f"[rate_monitor] Ошибка мониторинга: {e}")

        screenshot_path = None
        try:
            if page is not None and not page.is_closed():
                ts = now_msk().strftime("%Y%m%d_%H%M%S")
                screenshot_path = f"/tmp/bakai_error_{ts}.png"
                page.screenshot(path=screenshot_path, full_page=True)
        except Exception:
            screenshot_path = None

        raise RateMonitorError(str(e), screenshot_path=screenshot_path)
    finally:
        close_playwright_stack(page=page, context=context, browser=browser)

    # ---------------- обработка результата ----------------

    last = _load_rate()

    if last is None:
        msg = f"💱 Текущий курс покупки RUB: {buy_rate}"
        _send_to_current_route(msg, override_chat_id=chat_id)
        _save_rate(buy_rate)
        return

    if buy_rate != last:
        diff = buy_rate - last
        arrow = "⬆️" if diff > 0 else "⬇️"
        msg = (
            f"⚡ *Внимание!* Новый курс покупки RUB: {buy_rate}\n"
            f"{arrow} Было: {last} (Δ {diff:+.3f})"
        )
        _send_to_alert_route(msg)
        _save_rate(buy_rate)
    else:
        msg = f"💤 Курс без изменений: {buy_rate}"
        _send_to_current_route(msg, override_chat_id=chat_id)
        logger.info(f"[rate_monitor] Курс без изменений ({buy_rate}).")

def run_rate_monitor_safe():
    attempts = 3
    delays = (5, 15, 30)

    for i in range(attempts):
        try:
            logger.info(f"[rate_monitor] Попытка {i + 1}/{attempts}")
            check_bakai_rate()
            logger.info("[rate_monitor] ✅ Проверка завершена успешно")
            return

        except RateMonitorError as e:
            # если это не последняя попытка — ждём и пробуем снова
            if i < attempts - 1:
                delay = delays[i] + random.uniform(0, 3)
                logger.warning(
                    f"[rate_monitor] Ошибка: {e}. Повтор через {delay:.1f} сек"
                )
                time_module.sleep(delay)
                continue

            # ===== ПОСЛЕДНЯЯ ПОПЫТКА =====
            logger.error("[rate_monitor] ❌ Все попытки исчерпаны")

            _send_to_current_route(
                f"⚠️ RateMonitor недоступен после {attempts} попыток.\nОшибка: {e}",
            )

            if e.screenshot_path and os.path.exists(e.screenshot_path):
                _send_file_to_current_route(
                    e.screenshot_path,
                    caption="📸 Скриншот ошибки RateMonitor",
                )

            return
# --------------------------------------------------------------------------

if __name__ == "__main__":
    check_bakai_rate()
