import os
import time
from datetime import datetime
from playwright.sync_api import sync_playwright
import zoneinfo

from core.playwright_cleanup import close_playwright_stack
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["HOURLY"]
logger = get_logger(name, icon)

BASE_DIR = "/tmp/hourly_raccoon"
AUTH_STATE = "/tmp/hourly_raccoon_auth.json"

os.makedirs(BASE_DIR, exist_ok=True)

LOGIN = os.getenv("RACCOON_LOGIN")
PASSWORD = os.getenv("RACCOON_PASSWORD")
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1").lower() in {"1", "true"}
MSK = zoneinfo.ZoneInfo("Europe/Moscow")


def _ensure_logged_in(page, context):
    """Используем сохранённую сессию или логинимся."""
    if os.path.exists(AUTH_STATE):
        return

    logger.info("[hourly_dl] Логинимся…")
    page.goto("https://raccoon.it.com/partner/#/login", timeout=5000)
    page.fill("input[type='email']", LOGIN)
    page.fill("input[type='password']", PASSWORD)
    page.click("button:has-text('Войти')")
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    context.storage_state(path=AUTH_STATE)
    logger.info("[hourly_dl] Сессия сохранена")


def _download_payin(page):
    """PayIn за сегодня."""
    tz_now = datetime.now(MSK).strftime("%Y-%m-%d")

    logger.info("[hourly_dl] PayIn → выбираем дату…")

    page.goto("https://raccoon.it.com/partner/#/payin")
    page.wait_for_load_state("networkidle")

    page.click("label.form-control")
    page.wait_for_selector(".b-calendar")

    try:
        page.click(f"[data-date='{tz_now}']")
    except:
        logger.warning("[hourly_dl] Не удалось выбрать дату")

    page.click("button:has-text('Применить')")
    page.wait_for_load_state("networkidle")
    time.sleep(1.3)

    with page.expect_download(timeout=90_000) as d:
        page.click("button:has-text('Экспорт')")
    dl = d.value

    path = os.path.join(BASE_DIR, "payin.xlsx")
    dl.save_as(path)
    logger.info(f"[hourly_dl] PayIn сохранён: {path}")

    return path


def run_hourly_raccoon_cycle():
    """Скачивание PayIn + Payout за сегодня."""
    with sync_playwright() as p:
        browser = None
        context = None
        page = None
        try:
            browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
            context = browser.new_context(
                accept_downloads=True,
                storage_state=AUTH_STATE if os.path.exists(AUTH_STATE) else None,
                timezone_id="Europe/Moscow",
            )
            page = context.new_page()

            _ensure_logged_in(page, context)

            _download_payin(page)
        finally:
            close_playwright_stack(page=page, context=context, browser=browser)

    logger.info("[hourly_dl] Скачивание завершено")
    return True
