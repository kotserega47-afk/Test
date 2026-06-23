"""Antares «Кошельки» export download — isolated helper for script jobs.

Logic mirrors integrations/downloader.py::_download_wallet_export without
touching the download job pipeline or run_download() semantics.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, sync_playwright

from core.playwright_cleanup import close_playwright_stack
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DOWNLOADER"]
logger = get_logger(name, icon)

MSK_TZ = ZoneInfo("Europe/Moscow")
WALLET_URL = "https://antares.plus/lkcard/#/wallet"
LOGIN_URL = "https://antares.plus/lkcard/#/login"
EXPORT_BTN_SELECTOR = "button.btn-primary:has-text('Экспорт')"

BASE_DIR = "/tmp"
DEFAULT_DOWNLOAD_DIR = os.path.join(BASE_DIR, "script_jobs", "operator_wallets_ready")
DEBUG_DIR = os.path.join(BASE_DIR, "script_jobs", "operator_wallets_ready", "debug")
AUTH_STATE_FILE = os.path.join(BASE_DIR, "auth_state.json")

LOGIN = (os.getenv("ANTARES_LOGIN") or "").strip()
PASSWORD = (os.getenv("ANTARES_PASSWORD") or "").strip()
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1").strip().lower() in {"1", "true", "yes", "y"}


def _timestamp() -> str:
    return datetime.now(MSK_TZ).strftime("%H.%M")


def format_login_failure_message(
    *,
    url: str,
    login_env_set: bool,
    password_env_set: bool,
    page_hint: str = "",
    screenshot: str = "",
    html: str = "",
    reason: str = "",
) -> str:
    """Build actionable login failure text without leaking credentials."""
    parts = ["Antares login failed", f"url={url}"]
    parts.append(f"login_env_set={login_env_set}")
    parts.append(f"password_env_set={password_env_set}")
    if page_hint:
        parts.append(page_hint)
    if reason:
        parts.append(f"reason={reason}")
    if screenshot:
        parts.append(f"screenshot={screenshot}")
    if html:
        parts.append(f"html={html}")
    return " ".join(parts)


def _safe_page_hint(page: Page) -> str:
    hints: list[str] = []
    try:
        title = (page.title() or "").strip()
        if title:
            hints.append(f"title={title[:120]}")
    except Exception:
        pass

    for selector in (".alert", ".error", "[role='alert']", ".invalid-feedback"):
        try:
            loc = page.locator(selector).first
            if loc.count() == 0 or not loc.is_visible():
                continue
            text = loc.inner_text(timeout=1_000).strip().replace("\n", " ")
            if text:
                hints.append(f"alert={text[:200]}")
                break
        except Exception:
            continue

    return " ".join(hints)


def _ensure_debug_dir() -> str:
    os.makedirs(DEBUG_DIR, exist_ok=True)
    return DEBUG_DIR


def _capture_login_debug(page: Page, tag: str) -> tuple[str, str]:
    debug_dir = _ensure_debug_dir()
    stamp = datetime.now(MSK_TZ).strftime("%Y%m%d_%H%M%S")
    screenshot_path = os.path.join(debug_dir, f"{tag}_{stamp}.png")
    html_path = os.path.join(debug_dir, f"{tag}_{stamp}.html")

    saved_screenshot = ""
    saved_html = ""
    try:
        page.screenshot(path=screenshot_path, full_page=True)
        saved_screenshot = screenshot_path
    except Exception as exc:
        logger.warning("[operator_wallets_export] screenshot capture failed: %s", exc)

    try:
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(page.content())
        saved_html = html_path
    except Exception as exc:
        logger.warning("[operator_wallets_export] html capture failed: %s", exc)

    return saved_screenshot, saved_html


def _wallet_export_ready(page: Page, *, timeout: int = 30_000) -> bool:
    """Confirm session by wallet page export button (same locator as downloader.py)."""
    try:
        page.goto(WALLET_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("networkidle")
        export_btn = page.locator(EXPORT_BTN_SELECTOR).first
        export_btn.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False


def _raise_login_failed(
    page: Page,
    *,
    login_env_set: bool,
    password_env_set: bool,
    reason: str,
    debug_tag: str = "login_failed",
) -> None:
    screenshot, html_path = _capture_login_debug(page, debug_tag)
    message = format_login_failure_message(
        url=page.url,
        login_env_set=login_env_set,
        password_env_set=password_env_set,
        page_hint=_safe_page_hint(page),
        screenshot=screenshot,
        html=html_path,
        reason=reason,
    )
    logger.error("[operator_wallets_export] %s", message)
    raise RuntimeError(message)


def _perform_fresh_login(page: Page) -> None:
    logger.info("[operator_wallets_export] logging in to Antares")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
    page.fill("input[type='text']", LOGIN)
    page.fill("input[type='password']", PASSWORD)
    page.click("button:has-text('Войти')")
    page.wait_for_load_state("networkidle")
    time.sleep(2)


def _ensure_logged_in(page: Page, context) -> None:
    login_env_set = bool(LOGIN)
    password_env_set = bool(PASSWORD)
    if not login_env_set or not password_env_set:
        raise RuntimeError("ANTARES_LOGIN / ANTARES_PASSWORD не заданы")

    if os.path.exists(AUTH_STATE_FILE):
        if _wallet_export_ready(page):
            logger.info("[operator_wallets_export] session active via auth_state.json")
            return
        logger.warning(
            "[operator_wallets_export] auth_state.json present but wallet export not ready; re-login"
        )

    _perform_fresh_login(page)
    if _wallet_export_ready(page):
        context.storage_state(path=AUTH_STATE_FILE)
        logger.info("[operator_wallets_export] session saved")
        return

    _raise_login_failed(
        page,
        login_env_set=login_env_set,
        password_env_set=password_env_set,
        reason="export button not visible after login",
    )


def _download_wallet_page_export(page, timestamp: str, download_dir: str) -> str:
    logger.info("[operator_wallets_export] wallet page export start")
    page.goto(WALLET_URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_load_state("networkidle")

    export_btn = page.locator(EXPORT_BTN_SELECTOR).first
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
