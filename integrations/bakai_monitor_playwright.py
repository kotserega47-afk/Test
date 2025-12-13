# integrations/bakai_monitor_playwright.py
import os
from datetime import datetime, time
from playwright.sync_api import sync_playwright
from utils.logger import logger
from integrations.telegram_bot import send_message_sync
from zoneinfo import ZoneInfo

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
if not CHAT_ID:
    raise RuntimeError("Не задан TELEGRAM_CHAT_ID")

BAKAI_CHAT_ID = os.getenv("BAKAI_CHAT_ID")
if not BAKAI_CHAT_ID:
    raise RuntimeError("Не задан BAKAI_CHAT_ID")

URL = "https://bakai.kg/ru/"
LAST_RATE_FILE = "/tmp/bakai_last_buy_rate.txt"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

MSK = ZoneInfo("Europe/Moscow")


# ------------------------ вспомогательные ------------------------

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
        send_message_sync(f"⚠️ Ошибка мониторинга курса: {e}", chat_id=chat_id)
        return

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
        target_chat = BAKAI_CHAT_ID or chat_id
        send_message_sync(msg, chat_id=target_chat)
        _save_rate(buy_rate)
    else:
        msg = f"💤 Курс без изменений: {buy_rate}"
        send_message_sync(msg, chat_id=chat_id)
        logger.info(f"[rate_monitor] Курс без изменений ({buy_rate}).")


# --------------------------------------------------------------------------

if __name__ == "__main__":
    check_bakai_rate()
