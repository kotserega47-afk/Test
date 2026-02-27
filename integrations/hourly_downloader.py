# integrations/hourly_downloader.py
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple

from playwright.sync_api import sync_playwright
from zoneinfo import ZoneInfo

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES


icon, name = LOG_PROFILES["HOURLY"]
logger = get_logger(name, icon)

MSK = ZoneInfo("Europe/Moscow")

LOGIN = (os.getenv("ANTARES_LOGIN") or "").strip()
PASSWORD = (os.getenv("ANTARES_PASSWORD") or "").strip()
HEADLESS = (os.getenv("PLAYWRIGHT_HEADLESS", "1").strip().lower() in {"1", "true", "yes", "y"})

BASE_DIR = "/tmp/hourly"
AUTH_STATE = "/tmp/hourly/auth_state.json"

Path(BASE_DIR).mkdir(parents=True, exist_ok=True)
Path(os.path.dirname(AUTH_STATE)).mkdir(parents=True, exist_ok=True)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _target_date_str_msk() -> str:
    """
    Contract:
      - During 00:xx (MSK) we download previous day as "final daily".
      - Otherwise download today.
    Returned format: YYYY-MM-DD (matches Antares calendar data-date)
    """
    now = datetime.now(MSK)
    if now.hour == 0:
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def _ensure_logged_in(page, context) -> None:
    """
    Uses saved storage_state if available; otherwise performs login and saves it.
    """
    if os.path.exists(AUTH_STATE):
        logger.info("[hourly_dl] session exists -> reuse")
        return

    if not LOGIN or not PASSWORD:
        raise RuntimeError("ANTARES_LOGIN / ANTARES_PASSWORD is not set")

    logger.info("[hourly_dl] login…")
    page.goto("https://antares.plus/lkcard/#/login", timeout=60_000)
    page.fill("input[type='text']", LOGIN)
    page.fill("input[type='password']", PASSWORD)
    page.click("button:has-text('Войти')")
    page.wait_for_load_state("networkidle")
    time.sleep(2)

    context.storage_state(path=AUTH_STATE)
    logger.info("[hourly_dl] session saved")


def _find_and_pick_date(page, target_date: str) -> bool:
    """
    Antares calendar uses data-date='YYYY-MM-DD'.
    If date not visible, tries going back month-by-month (up to 12).
    """
    selector = f"[data-date='{target_date}']"

    for _ in range(12):
        if page.locator(selector).count() > 0:
            page.locator(selector).click()
            return True

        prev_btn = page.locator("button[aria-label='Previous month']")
        if prev_btn.count() == 0:
            return False

        prev_btn.click()
        page.wait_for_timeout(200)

    return False


def _download_table(page, *, url: str, kind: str, out_path: str) -> str:
    """
    Downloads export for payin/payout for selected date.
    """
    target_date = _target_date_str_msk()
    logger.info(f"[hourly_dl] {kind} date (MSK): {target_date}")

    page.goto(url, timeout=60_000)
    page.wait_for_load_state("networkidle")

    page.click("label.form-control")
    page.wait_for_selector(".b-calendar", timeout=15_000)

    ok = _find_and_pick_date(page, target_date)
    if not ok:
        raise RuntimeError(f"[hourly_dl] can't pick date in calendar: {target_date}")

    page.click("button:has-text('Применить')")
    page.wait_for_load_state("networkidle")
    time.sleep(1.3)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    with page.expect_download(timeout=120_000) as d:
        page.click("button:has-text('Экспорт')")
    dl = d.value
    dl.save_as(out_path)

    logger.info(f"[hourly_dl] {kind} saved: {out_path}")
    return out_path


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def run_hourly_cycle() -> Tuple[str, str]:
    """
    Downloader only.
    Saves:
      /tmp/hourly/payin.xlsx
      /tmp/hourly/payout.xlsx
    Returns (payin_path, payout_path).
    """
    payin_path = os.path.join(BASE_DIR, "payin.xlsx")
    payout_path = os.path.join(BASE_DIR, "payout.xlsx")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])

        context = browser.new_context(
            accept_downloads=True,
            storage_state=AUTH_STATE if os.path.exists(AUTH_STATE) else None,
        )
        page = context.new_page()

        _ensure_logged_in(page, context)

        _download_table(
            page,
            url="https://antares.plus/lkcard/#/payin",
            kind="PayIn",
            out_path=payin_path,
        )
        _download_table(
            page,
            url="https://antares.plus/lkcard/#/vyplaty",
            kind="Payout",
            out_path=payout_path,
        )

        browser.close()

    logger.info("[hourly_dl] done")
    return payin_path, payout_path


if __name__ == "__main__":
    run_hourly_cycle()