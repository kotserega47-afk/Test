# integrations/rate_monitor_playwright.py
import os
from datetime import datetime, time
from playwright.sync_api import sync_playwright
from utils.logger import logger
from integrations.telegram_bot import send_message_sync

URL = "https://bakai.kg/ru/"
CHAT_ID = "-1003281664794"

LAST_RATE_FILE = "/tmp/bakai_last_buy_rate.txt"
FLAG_855 = "/tmp/bakai_sent_855.txt"
FLAG_1030 = "/tmp/bakai_sent_1030.txt"
FLAG_CHANGED = "/tmp/bakai_changed_today.txt"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ------------------------ вспомогательные функции ------------------------

def _load_rate():
    if not os.path.exists(LAST_RATE_FILE):
        return None
    try:
        return float(open(LAST_RATE_FILE).read().strip())
    except:
        return None


def _save_rate(v: float):
    try:
        with open(LAST_RATE_FILE, "w") as f:
            f.write(str(v))
    except:
        pass


def _flag_today(path) -> bool:
    """Проверяет, установлен ли флаг на сегодня"""
    if not os.path.exists(path):
        return False
    try:
        saved = open(path).read().strip()
        return saved == datetime.now().strftime("%Y-%m-%d")
    except:
        return False


def _set_flag(path):
    """Устанавливает флаг на сегодняшний день"""
    try:
        with open(path, "w") as f:
            f.write(datetime.now().strftime("%Y-%m-%d"))
    except:
        pass


def _in_time_window():
    now = datetime.now().time()
    return time(8, 55) <= now <= time(10, 30)


def _is_855_now():
    t = datetime.now().time()
    return t.hour == 8 and t.minute >= 55


def _is_1030_now():
    t = datetime.now().time()
    return t.hour == 10 and t.minute == 30


# -------------------------- основной мониторинг --------------------------

def check_bakai_rate(chat_id: str = None):

    chat_id = chat_id or CHAT_ID

    # вне окна — ничего не делаем
    if not _in_time_window():
        logger.info("[rate_monitor] Вне окна 08:55–10:30 — выходим.")
        return

    logger.info("[rate_monitor] === Старт проверки курса покупки RUB ===")

    # если сегодня курс уже менялся — больше не проверяем
    if _flag_today(FLAG_CHANGED):
        logger.info("[rate_monitor] Курс сегодня уже менялся — ждём завтра.")
        return

    # запускаем браузер
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
                locale="ru-RU"
            )

            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)

            page = context.new_page()

            logger.info("[rate_monitor] Открываю https://bakai.kg/ru/ ...")
            page.goto(URL, timeout=60000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)

            logger.info("[rate_monitor] Выбираю 'Онлайн переводы'...")
            page.select_option("select", value="transfer")
            page.wait_for_timeout(1200)

            # ищем строку RUB по флагу
            logger.info("[rate_monitor] Ищу строку RUB по img[src*='rub'] ...")
            rub_row = None

            for step in range(7):
                rows = page.locator("tr:has(img[src*='rub'])")
                if rows.count() > 0:
                    rub_row = rows.first
                    break
                logger.info(f"[rate_monitor] Прокручиваю вниз (шаг {step})...")
                page.mouse.wheel(0, 700)
                page.wait_for_timeout(600)

            if rub_row is None:
                raise RuntimeError("Строка RUB не найдена.")

            cells = rub_row.locator("th,td")
            if cells.count() < 2:
                raise RuntimeError("Неверная структура строки RUB.")

            buy_text = cells.nth(1).inner_text().strip()
            buy_rate = float(buy_text.replace(",", "."))

            logger.info(f"[rate_monitor] Покупка RUB: {buy_rate}")

            browser.close()

    except Exception as e:
        logger.error(f"[rate_monitor] Ошибка мониторинга: {e}")
        send_message_sync(f"⚠️ Ошибка мониторинга курса: {e}", chat_id=chat_id)
        return

    # ---------------- логика уведомлений ----------------

    last = _load_rate()

    # === 08:55 — старт мониторинга
    if _is_855_now() and not _flag_today(FLAG_855):

        if last is None:
            msg = f"🕗 Старт мониторинга.\nПокупка RUB: {buy_rate}"
        else:
            diff = buy_rate - last
            arrow = "⬆️" if diff > 0 else "⬇️"
            msg = (
                "🕗 Старт мониторинга.\n"
                f"Вчера: {last}\n"
                f"Сегодня: {buy_rate} ({arrow}{diff:+.3f})"
            )

        send_message_sync(msg, chat_id=chat_id)
        _set_flag(FLAG_855)

    # === изменение курса в окне
    if last is not None and buy_rate != last:

        diff = buy_rate - last
        arrow = "⬆️" if diff > 0 else "⬇️"

        msg = (
            "💱 *Изменение курса покупки RUB*\n"
            f"{arrow} {last} → {buy_rate} (Δ {diff:+.3f})"
        )

        send_message_sync(msg, chat_id=chat_id)

        _save_rate(buy_rate)
        _set_flag(FLAG_CHANGED)
        return

    # === 10:30 и курс не менялся
    if _is_1030_now() and not _flag_today(FLAG_1030):

        msg = f"🕚 Мониторинг завершён. Курс покупки RUB не изменился {buy_rate}"
        send_message_sync(msg, chat_id=chat_id)

        _set_flag(FLAG_1030)
        _save_rate(buy_rate)

    # если курс одинаковый — просто молчим
# --------------------------------------------------------------------------

if __name__ == "__main__":
    check_bakai_rate()
