# integrations/downloader_wallets.py
import os
import sys
import time
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
import pytz

# Добавляем корень проекта в пути
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.telegram_bot import send_message_sync, send_file_sync
from utils.logger import logger
from analyzers.wallet_analyzer import analyze_wallets


LOGIN = os.getenv("ANTARES_LOGIN")
PASSWORD = os.getenv("ANTARES_PASSWORD")
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1").lower() in {"1", "true", "yes", "y"}

BASE_DIR = "/tmp"
DOWNLOAD_DIR = os.path.join(BASE_DIR, "wallet_handler")
AUTH_STATE_FILE = os.path.join(BASE_DIR, "auth_state_wallets.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

MSK_TZ = pytz.timezone("Europe/Moscow")


def _ensure_logged_in(page, context):
    """Авторизация на Antares"""
    if os.path.exists(AUTH_STATE_FILE):
        logger.info("🔐 Используем сохранённую сессию")
        return

    logger.info("🔑 Логинимся в Antares…")
    page.goto("https://antares.plus/lkcard/#/login")
    page.fill("input[type='text']", LOGIN)
    page.fill("input[type='password']", PASSWORD)
    page.click("button:has-text('Войти')")
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    context.storage_state(path=AUTH_STATE_FILE)
    logger.info("✅ Сессия сохранена")


def _download_payin(page, ts: str) -> str:
    """Скачивание файла PayIn"""
    logger.info("⬇️ PayIn → экспорт…")

    page.goto("https://antares.plus/lkcard/#/payin")
    page.wait_for_load_state("networkidle")

    # дата = сегодня/вчера по МСК
    use_yesterday = os.getenv("USE_YESTERDAY", "true").lower() == "true"
    days_back = 1 if use_yesterday else 0

    target_date = (datetime.now(MSK_TZ) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    logger.info(f"📅 Устанавливаем дату (МСК): {target_date}")

    # Выбор даты
    page.click("label.form-control")
    page.wait_for_selector(".b-calendar")
    try:
        page.click(f"[data-date='{target_date}']")
        logger.info(f"✅ Дата выбрана: {target_date}")
    except:
        logger.warning("⚠️ Не удалось выбрать дату")

    page.click("button:has-text('Применить')")
    page.wait_for_load_state("networkidle")
    time.sleep(1.3)

    # Загрузка файла
    with page.expect_download(timeout=300000) as d:
        page.click("button:has-text('Экспорт')")
    download = d.value
    path = os.path.join(DOWNLOAD_DIR, f"payin_{ts}.xlsx")
    download.save_as(path)

    logger.info(f"✅ PayIn сохранён: {path}")
    return path


def run_wallet_cycle():
    """Основной цикл – скачивает PayIn и запускает анализ"""
    if not LOGIN or not PASSWORD:
        raise RuntimeError("ANTARES_LOGIN / ANTARES_PASSWORD не заданы")

    CHAT_ID_WALLET = os.getenv("TELEGRAM_CHAT_ID_WALLET") or os.getenv("TELEGRAM_CHAT_ID")

    ts = datetime.now(MSK_TZ).strftime("%H.%M")
    logger.info(f"🕒 WalletHandler стартовал (ts={ts})")


    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
        context = browser.new_context(accept_downloads=True)

        if os.path.exists(AUTH_STATE_FILE):
            context = browser.new_context(storage_state=AUTH_STATE_FILE, accept_downloads=True)

        page = context.new_page()
        _ensure_logged_in(page, context)

        payin_path = _download_payin(page, ts)
        browser.close()

    analyze_wallets(payin_path)
    send_file_sync(
        file_path=payin_path,
        caption=f"📥 PayIn файл ({ts})",
        chat_id=CHAT_ID_WALLET
    )



if __name__ == "__main__":
    try:
        run_wallet_cycle()
    except Exception as e:
        logger.exception(f"❌ Ошибка в wallet-handler: {e}")
        CHAT_ID_WALLET = os.getenv("TELEGRAM_CHAT_ID_WALLET") or os.getenv("TELEGRAM_CHAT_ID")
        send_message_sync(f"❌ Ошибка в wallet-handler: {e}", chat_id=CHAT_ID_WALLET)