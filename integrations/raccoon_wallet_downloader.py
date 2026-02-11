# integrations/raccoon_wallet_downloader.py
import os
import sys
import time
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
from zoneinfo import ZoneInfo
import yaml

# Добавляем корень проекта в пути
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.telegram_bot import send_message_sync, send_file_sync
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from analyzers.raccoon_wallet_analyzer import analyze_raccoon_wallets

icon, name = LOG_PROFILES["WALLET"]
logger = get_logger(name, icon)

LOGIN = os.getenv("RACCOON_LOGIN")
PASSWORD = os.getenv("RACCOON_PASSWORD")
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1").lower() in {"1", "true", "yes", "y"}

BASE_DIR = "/tmp"
DOWNLOAD_DIR = os.path.join(BASE_DIR, "raccoon_wallet")
AUTH_STATE_FILE = os.path.join(BASE_DIR, "auth_state_raccoon.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

MSK_TZ = ZoneInfo("Europe/Moscow")


def _ensure_logged_in(page, context):
    """Авторизация на Raccoon"""
    if os.path.exists(AUTH_STATE_FILE):
        logger.info("🔐 Используем сохранённую сессию")
        return

    logger.info("🔑 Логинимся в Raccoon…")
    page.goto("https://raccoon.it.com/partner/#/login")
    page.fill("input[type='email']", LOGIN)
    page.fill("input[type='password']", PASSWORD)
    page.click("button:has-text('Войти')")
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    context.storage_state(path=AUTH_STATE_FILE)
    logger.info("✅ Сессия сохранена")

def _find_and_pick_date(page, target_date: str):
    """
    Выбирает дату target_date (формат YYYY-MM-DD) в календаре Raccoon.
    Листает назад, если дата не найдена в текущем месяце.
    """
    selector = f"[data-date='{target_date}']"

    for _ in range(12):     # максимум 12 месяцев назад
        if page.locator(selector).count() > 0:
            page.locator(selector).click()
            logger.info(f"✅ Дата выбрана: {target_date}")
            return True

        prev_btn = page.locator("button[aria-label='Previous month']")
        if prev_btn.count() == 0:
            logger.warning("⚠️ Кнопка 'Previous month' не найдена!")
            return False

        prev_btn.click()
        page.wait_for_timeout(180)

    logger.warning(f"⚠️ Дата {target_date} не найдена в пределах 12 месяцев")
    return False


def _download_payin(page, ts: str, days_back: int) -> str:
    logger.info("⬇️ PayIn → экспорт…")

    page.goto("https://raccoon.it.com/partner/#/payin")
    page.wait_for_load_state("networkidle")

    target_date = (datetime.now(MSK_TZ) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    logger.info(f"📅 PayIn дата (МСК): {target_date}")

    # открываем календарь
    page.click("label.form-control")
    page.wait_for_selector(".b-calendar")

    # выбираем дату
    _find_and_pick_date(page, target_date)

    # применить
    page.locator("button:has-text('Применить')").click()
    page.wait_for_load_state("networkidle")
    time.sleep(2)

    # скачивание
    with page.expect_download(timeout=180000) as d:
        page.click("button:has-text('Экспорт')")
    download = d.value

    path = os.path.join(DOWNLOAD_DIR, f"payin_{ts}.xlsx")
    download.save_as(path)

    logger.info(f"✅ PayIn сохранён: {path}")
    return path



    #       def _download_payout(page, ts: str, days_back: int) -> str:
    #           """Скачивание файла Payout: только одна дата, как в PayIn."""
    #      logger.info("⬇️ Payout → экспорт…")
    #
    #       page.goto("https://raccoon.it.com/partner/#/vyplaty")
    #      page.wait_for_load_state("networkidle")
    #
    #       target_date = (datetime.now(MSK_TZ) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    #       logger.info(f"📅 Payout дата (МСК): {target_date}")
    #
    #       # Открываем календарь
    #       page.click("label.form-control")
    #       page.wait_for_selector(".b-calendar")
    #
    #       # Выбираем дату (навигация назад если надо)
    #       _find_and_pick_date(page, target_date)
    #
    #      # Применяем
    #       page.locator("button:has-text('Применить')").click()
    #       page.wait_for_load_state("networkidle")
    #       time.sleep(2)
    #
    #       # Скачивание
    #       with page.expect_download(timeout=180000) as d:
    #           page.locator("button:has-text('Экспорт')").click()
    #       download = d.value
    #
    #        path = os.path.join(DOWNLOAD_DIR, f"payout_{ts}.xlsx")
    #       download.save_as(path)
    #
    #       logger.info(f"✅ Payout сохранён: {path}")
    #       return path


def run_raccoon_wallet_cycle():
    if not LOGIN or not PASSWORD:
        raise RuntimeError("RACCOON_LOGIN / RACCOON_PASSWORD не заданы")

    CHAT_ID_WALLET = os.getenv("TELEGRAM_CHAT_ID_RACCOON_WALLET") or os.getenv("TELEGRAM_CHAT_ID")

    ts = datetime.now(MSK_TZ).strftime("%H.%M")
    logger.info(f"🕒 WalletHandler стартовал (ts={ts})")

    # Загружаем конфиг для таймингов и периодов выгрузки
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "raccoon_wallet_config.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    payin_days = cfg.get("download_periods", {}).get("payin_days_back", 2)
    payout_days = cfg.get("download_periods", {}).get("payout_days_back", 7)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
        context = browser.new_context(accept_downloads=True)

        if os.path.exists(AUTH_STATE_FILE):
            context = browser.new_context(storage_state=AUTH_STATE_FILE, accept_downloads=True)

        page = context.new_page()
        _ensure_logged_in(page, context)

        payin_path = _download_payin(page, ts, payin_days)
        #payout_path = _download_payout(page, ts, payout_days)

        browser.close()

    analyze_raccoon_wallets(payin_path, payout_path=None)




if __name__ == "__main__":
    try:
        run_raccoon_wallet_cycle()
    except Exception as e:
        logger.exception(f"❌ Ошибка в wallet-handler: {e}")
        CHAT_ID_WALLET = os.getenv("TELEGRAM_CHAT_ID_RACCOON_WALLET") or os.getenv("TELEGRAM_CHAT_ID")
        send_message_sync(f"❌ Ошибка в wallet-handler: {e}", chat_id=CHAT_ID_WALLET)