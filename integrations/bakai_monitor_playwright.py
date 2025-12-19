# integrations/bakai_monitor_playwright.py
import os
from datetime import datetime, time
from playwright.sync_api import sync_playwright
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from integrations.telegram_bot import send_message_sync
from zoneinfo import ZoneInfo
import time as time_module
import random

icon, name = LOG_PROFILES["RATE"]
logger = get_logger(name, icon)

# Текущий курс
CHAT_ID = os.getenv("CURRENT_RATE_BAKAI_CHAT_ID")
if not CHAT_ID:
    raise RuntimeError("Не задан CURRENT_RATE_BAKAI_CHAT_ID")

# Новый курс
ALERT_CHAT_ID = os.getenv("NEW_RATE_BAKAI_CHAT_ID")
if not ALERT_CHAT_ID:
    raise RuntimeError("Не задан NEW_RATE_BAKAI_CHAT_ID")

URL = "https://bakai.kg/ru/"
LAST_RATE_FILE = "/tmp/bakai_last_buy_rate.txt"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

MSK = ZoneInfo("Europe/Moscow")


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


# -------------------------- основной мониторинг --------------------------

def check_bakai_rate(chat_id: str = None):
    chat_id = chat_id or CHAT_ID

    if not _in_time_window():
        logger.info("[rate_monitor] Вне окна 08:00–23:55 — пропускаем.")
        return

    logger.info("[rate_monitor] === Проверка курса покупки RUB ===")

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
            page.goto(URL, timeout=60000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)

            page.select_option("select", value="transfer")
            page.wait_for_timeout(1200)

            # поиск строки RUB
            rub_row = None
            for step in range(7):
                rows = page.locator("tr:has(img[src*='rub'])")
                if rows.count() > 0:
                    rub_row = rows.first
                    break
                page.mouse.wheel(0, 700)
                page.wait_for_timeout(600)

            if rub_row is None:
                raise RuntimeError("Строка RUB не найдена.")

            cells = rub_row.locator("th,td")
            if cells.count() < 2:
                raise RuntimeError("Неверная структура строки RUB.")

            buy_text = cells.nth(1).inner_text().strip()
            buy_rate = float(buy_text.replace(",", "."))

            browser.close()
    except Exception as e:
        logger.error(f"[rate_monitor] Ошибка мониторинга: {e}")

        screenshot_path = None
        try:
            if 'page' in locals() and not page.is_closed():
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                screenshot_path = f"/tmp/bakai_error_{ts}.png"
                page.screenshot(path=screenshot_path, full_page=True)
        except Exception:
            screenshot_path = None

        raise RateMonitorError(str(e), screenshot_path=screenshot_path)

    # ---------------- обработка результата ----------------

    last = _load_rate()

    if last is None:
        msg = f"💱 Текущий курс покупки RUB: {buy_rate}"
        send_message_sync(msg, chat_id=chat_id)
        _save_rate(buy_rate)
        return

    if buy_rate != last:
        diff = buy_rate - last
        arrow = "⬆️" if diff > 0 else "⬇️"
        msg = (
            f"⚡ *Внимание!* Новый курс покупки RUB: {buy_rate}\n"
            f"{arrow} Было: {last} (Δ {diff:+.3f})"
        )
        # 👇 отправляем в ALERT чат, если задан
        target_chat = ALERT_CHAT_ID or chat_id
        send_message_sync(msg, chat_id=target_chat)
        _save_rate(buy_rate)
    else:
        msg = f"💤 Курс без изменений: {buy_rate}"
        send_message_sync(msg, chat_id=chat_id)
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

            # одно сообщение в Telegram
            send_message_sync(
                f"⚠️ RateMonitor недоступен после {attempts} попыток.\nОшибка: {e}",
                chat_id=CHAT_ID
            )

            # отправляем скриншот, если он был сделан
            if e.screenshot_path and os.path.exists(e.screenshot_path):
                from integrations.telegram_bot import send_file_sync
                send_file_sync(
                    e.screenshot_path,
                    caption="📸 Скриншот ошибки RateMonitor",
                    chat_id=CHAT_ID
                )

            return
# --------------------------------------------------------------------------

if __name__ == "__main__":
    check_bakai_rate()
