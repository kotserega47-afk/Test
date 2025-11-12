# integrations/downloader_wallets.py
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
from integrations.telegram_bot import send_message_sync
from utils.logger import logger
from analyzers.wallet_analyzer import analyze_wallets

LOGIN = os.getenv("ANTARES_LOGIN")
PASSWORD = os.getenv("ANTARES_PASSWORD")
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1").strip().lower() in {"1", "true", "yes", "y"}

BASE_DIR = "/tmp"
DOWNLOAD_DIR = os.path.join(BASE_DIR, "wallet_downloads")
AUTH_STATE_FILE = os.path.join(BASE_DIR, "auth_state_wallets.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def _ensure_logged_in(page, context):
    """Авторизация в Antares"""
    if os.path.exists(AUTH_STATE_FILE):
        logger.info("🔐 Используем сохранённую сессию (кошельки)")
        return
    logger.info("🔑 Логинимся в Antares (кошельки)")
    page.goto("https://antares.plus/lkcard/#/login")
    page.fill("input[type='text']", LOGIN)
    page.fill("input[type='password']", PASSWORD)
    page.click("button:has-text('Войти')")
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    context.storage_state(path=AUTH_STATE_FILE)
    logger.info("✅ Сессия сохранена (кошельки)")


def _download_wallet(page, ts: str) -> str:
    logger.info("⬇️ Wallet → экспорт...")
    page.goto("https://antares.plus/lkcard/#/wallet")
    page.wait_for_load_state("networkidle")
    with page.expect_download() as d1:
        page.click("button:has-text('Экспорт')")
    download1 = d1.value
    path = os.path.join(DOWNLOAD_DIR, f"wallet_{ts}.xlsx")
    download1.save_as(path)
    logger.info(f"✅ Wallet сохранён: {path}")
    return path


def _download_payin(page, ts: str) -> str:
    logger.info("⬇️ PayIn → экспорт (последние сутки)...")
    page.goto("https://antares.plus/lkcard/#/payin")
    page.wait_for_load_state("networkidle")
    tz = datetime.now().astimezone().tzinfo
    target_date = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
    logger.info(f"📅 Выбираем дату: {target_date}")

    page.click("label.form-control")
    page.wait_for_selector(".b-calendar", timeout=10000)
    try:
        page.click(f"[data-date='{target_date}']")
        logger.info(f"✅ Дата выбрана: {target_date}")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось выбрать дату {target_date}: {e}")

    page.click("button:has-text('Применить')")
    page.wait_for_load_state("networkidle")
    time.sleep(1.5)

    with page.expect_download(timeout=300000) as d2:
        page.click("button:has-text('Экспорт')")
    download2 = d2.value
    path = os.path.join(DOWNLOAD_DIR, f"payin_{ts}.xlsx")
    download2.save_as(path)
    logger.info(f"✅ PayIn сохранён: {path}")
    return path


def run_wallet_cycle():
    if not LOGIN or not PASSWORD:
        raise RuntimeError("ANTARES_LOGIN/ANTARES_PASSWORD не заданы")

    ts = datetime.now().strftime("%H.%M")
    logger.info(f"🕒 WalletDownloader стартовал (ts={ts})")
    send_message_sync(f"🕒 Запущен выгрузчик кошельков ({ts})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
        context = browser.new_context(accept_downloads=True)
        if os.path.exists(AUTH_STATE_FILE):
            context = browser.new_context(storage_state=AUTH_STATE_FILE, accept_downloads=True)
        page = context.new_page()
        _ensure_logged_in(page, context)
        wallet_path = _download_wallet(page, ts)
        payin_path = _download_payin(page, ts)
        browser.close()

    send_message_sync("📊 Начинаю анализ выгруженных файлов...")
    analyze_wallets(wallet_path, payin_path)
    send_message_sync("✅ Анализ кошельков завершён успешно.")


if __name__ == "__main__":
    try:
        run_wallet_cycle()
    except Exception as e:
        logger.exception(f"❌ Ошибка в wallet_downloader: {e}")
        send_message_sync(f"❌ Ошибка при выгрузке кошельков: {e}")
