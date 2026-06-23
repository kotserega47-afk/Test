"""Antares «Кошельки» export download — isolated helper for script jobs.

Logic mirrors integrations/downloader.py::_download_wallet_export without
touching the download job pipeline or run_download() semantics.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from core.playwright_cleanup import close_playwright_stack
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DOWNLOADER"]
logger = get_logger(name, icon)

MSK_TZ = ZoneInfo("Europe/Moscow")
WALLET_URL = "https://antares.plus/lkcard/#/wallet"
LOGIN_URL = "https://antares.plus/lkcard/#/login"

BASE_DIR = "/tmp"
DEFAULT_DOWNLOAD_DIR = os.path.join(BASE_DIR, "script_jobs", "operator_wallets_ready")
AUTH_STATE_FILE = os.path.join(BASE_DIR, "auth_state.json")

LOGIN = (os.getenv("ANTARES_LOGIN") or "").strip()
PASSWORD = (os.getenv("ANTARES_PASSWORD") or "").strip()
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1").strip().lower() in {"1", "true", "yes", "y"}


def _timestamp() -> str:
    return datetime.now(MSK_TZ).strftime("%H.%M")


def _ensure_logged_in(page, context) -> None:
    if os.path.exists(AUTH_STATE_FILE):
        page.goto(WALLET_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("networkidle")
        if "login" not in page.url.lower():
            logger.info("[operator_wallets_export] session active via auth_state.json")
            return

    if not LOGIN or not PASSWORD:
        raise RuntimeError("ANTARES_LOGIN / ANTARES_PASSWORD не заданы")

    logger.info("[operator_wallets_export] logging in to Antares")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
    page.fill("input[type='text']", LOGIN)
    page.fill("input[type='password']", PASSWORD)
    page.click("button:has-text('Войти')")
    page.wait_for_load_state("networkidle")
    time.sleep(2)

    if "login" in page.url.lower():
        raise RuntimeError("Antares login failed")

    context.storage_state(path=AUTH_STATE_FILE)
    logger.info("[operator_wallets_export] session saved")


def _download_wallet_page_export(page, timestamp: str, download_dir: str) -> str:
    logger.info("[operator_wallets_export] wallet page export start")
    page.goto(WALLET_URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_load_state("networkidle")

    export_btn = page.locator("button.btn-primary:has-text('Экспорт')").first
    export_btn.wait_for(state="visible", timeout=30_000)
    page.wait_for_timeout(10_000)

    with page.expect_download(timeout=300_000) as download_info:
        export_btn.click(force=True)

    local_path = os.path.join(download_dir, f"wallets_{timestamp}.xlsx")
    download_info.value.save_as(local_path)
    logger.info("[operator_wallets_export] saved path=%s", os.path.basename(local_path))
    return local_path


def download_antares_wallets_export(*, download_dir: str | None = None) -> str:
    """Download full wallets export from Antares «Кошельки». Returns local .xlsx path."""
    if not LOGIN or not PASSWORD:
        raise RuntimeError("ANTARES_LOGIN / ANTARES_PASSWORD не заданы")

    target_dir = download_dir or DEFAULT_DOWNLOAD_DIR
    os.makedirs(target_dir, exist_ok=True)
    timestamp = _timestamp()

    with sync_playwright() as playwright:
        browser = None
        context = None
        page = None
        try:
            browser = playwright.chromium.launch(
                headless=HEADLESS,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            if os.path.exists(AUTH_STATE_FILE):
                context = browser.new_context(
                    storage_state=AUTH_STATE_FILE,
                    accept_downloads=True,
                )
            else:
                context = browser.new_context(accept_downloads=True)

            page = context.new_page()
            _ensure_logged_in(page, context)
            return _download_wallet_page_export(page, timestamp, target_dir)
        finally:
            close_playwright_stack(page=page, context=context, browser=browser)
