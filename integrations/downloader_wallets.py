# integrations/downloader_wallets.py
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzers.wallet_analyzer import build_wallet_stats_dto
from core.config_manager import get_job_param, get_job_params_overrides
from core.event_log import append_event
from core.job_progress import record_progress
from core.playwright_cleanup import close_playwright_stack
from core.state_store import state_get, state_update
from reporters.wallet_reporter import render_wallet
from integrations.telegram_routes import (
    ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT,
    routes_from_rules_v2_enabled,
    send_message_to_route,
)
from transport.telegram_transport import send_text
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES


icon, name = LOG_PROFILES["WALLET"]
logger = get_logger(name, icon)

MSK_TZ = ZoneInfo("Europe/Moscow")

LOGIN = (os.getenv("ANTARES_LOGIN") or "").strip()
PASSWORD = (os.getenv("ANTARES_PASSWORD") or "").strip()
HEADLESS = (os.getenv("PLAYWRIGHT_HEADLESS", "1").strip().lower() in {"1", "true", "yes", "y"})

BASE_DIR = "/tmp"
DOWNLOAD_DIR = os.path.join(BASE_DIR, "wallet_handler")
AUTH_STATE_FILE = os.path.join(BASE_DIR, "auth_state_wallets.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Playwright timeouts (aligned with hourly_downloader; no env knobs in this patch).
GOTO_TIMEOUT_MS = 60_000
CALENDAR_SELECTOR_TIMEOUT_MS = 15_000
NETWORKIDLE_TIMEOUT_MS = 60_000

_WALLET_JOB_TYPE = "wallet"


def _wallet_stage(stage: str) -> None:
    logger.info(stage)
    record_progress(_WALLET_JOB_TYPE, stage)


@dataclass(frozen=True)
class WalletJobParams:
    payin_days_back: int = 0
    payout_days_back: int = 0


def _load_wallet_params() -> WalletJobParams:
    overrides = get_job_params_overrides(force_sync=False)
    payin_days = get_job_param(overrides, job="wallet", key="payin_days_back", default=0)
    payout_days = get_job_param(overrides, job="wallet", key="payout_days_back", default=0)

    try:
        payin_days = max(0, int(payin_days))
    except Exception:
        payin_days = 0

    try:
        payout_days = max(0, int(payout_days))
    except Exception:
        payout_days = 0

    return WalletJobParams(payin_days_back=payin_days, payout_days_back=payout_days)


def _ensure_logged_in(page, context) -> None:
    logger.info("🔑 Проверяем авторизацию в Antares…")

    page.goto(
        "https://antares.plus/lkcard/#/payin",
        wait_until="domcontentloaded",
        timeout=GOTO_TIMEOUT_MS,
    )
    page.wait_for_timeout(3000)

    if "login" not in page.url.lower():
        logger.info("✅ Сессия активна")
        return

    logger.info("🔑 Сессия недействительна, логинимся заново…")
    page.goto(
        "https://antares.plus/lkcard/#/login",
        wait_until="domcontentloaded",
        timeout=GOTO_TIMEOUT_MS,
    )

    login_input = page.locator("input.form-control[type='text']").first
    password_input = page.locator("input.form-control[type='password']").first
    submit_btn = page.locator("button[type='submit']").first

    login_input.wait_for(state="visible", timeout=15000)
    password_input.wait_for(state="visible", timeout=15000)
    submit_btn.wait_for(state="visible", timeout=15000)

    login_input.click(force=True)
    login_input.fill(LOGIN)

    password_input.click(force=True)
    password_input.fill(PASSWORD)

    submit_btn.click(force=True)
    page.wait_for_timeout(5000)

    if "login" in page.url.lower():
        page.screenshot(path=os.path.join(DOWNLOAD_DIR, "login_failed.png"), full_page=True)
        raise RuntimeError("Логин не выполнен. Сохранил login_failed.png")

    context.storage_state(path=AUTH_STATE_FILE)
    logger.info("✅ Новая сессия сохранена")


def _find_and_pick_date(page, target_date: str) -> bool:
    selector = f"[data-date='{target_date}']"
    for _ in range(12):
        if page.locator(selector).count() > 0:
            page.locator(selector).click()
            logger.info(f"✅ Дата выбрана: {target_date}")
            return True

        prev_btn = page.locator("button[aria-label='Previous month']")
        if prev_btn.count() == 0:
            logger.warning("⚠️ Кнопка 'Previous month' не найдена")
            return False
        prev_btn.click()
        page.wait_for_timeout(180)
    logger.warning(f"⚠️ Дата {target_date} не найдена в пределах 12 месяцев")
    return False


def _download_payin(page, ts: str, days_back: int) -> str:
    logger.info("⬇️ PayIn → экспорт…")
    _wallet_stage("payin_goto_start")
    page.goto("https://antares.plus/lkcard/#/payin", timeout=GOTO_TIMEOUT_MS)
    _wallet_stage("payin_goto_done")
    page.wait_for_load_state("networkidle", timeout=NETWORKIDLE_TIMEOUT_MS)
    _wallet_stage("payin_networkidle_done")

    target_date = (datetime.now(MSK_TZ) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    logger.info(f"📅 PayIn дата (МСК): {target_date}")

    page.click("label.form-control")
    page.wait_for_selector(".b-calendar", timeout=CALENDAR_SELECTOR_TIMEOUT_MS)
    _wallet_stage("payin_calendar_open")
    _find_and_pick_date(page, target_date)
    page.locator("button:has-text('Применить')").click()
    page.wait_for_load_state("networkidle", timeout=NETWORKIDLE_TIMEOUT_MS)
    _wallet_stage("payin_apply_done")
    time.sleep(2)

    _wallet_stage("payin_export_click")
    with page.expect_download(timeout=180000) as d:
        page.click("button:has-text('Экспорт')")
    download = d.value

    path = os.path.join(DOWNLOAD_DIR, f"payin_{ts}.xlsx")
    download.save_as(path)
    logger.info(f"✅ PayIn сохранён: {path}")
    return path


def _download_payout(page, ts: str, days_back: int) -> str:
    logger.info("⬇️ Payout → экспорт…")
    _wallet_stage("payout_goto_start")
    page.goto("https://antares.plus/lkcard/#/vyplaty", timeout=GOTO_TIMEOUT_MS)
    _wallet_stage("payout_goto_done")
    page.wait_for_load_state("networkidle", timeout=NETWORKIDLE_TIMEOUT_MS)
    _wallet_stage("payout_networkidle_done")

    target_date = (datetime.now(MSK_TZ) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    logger.info(f"📅 Payout дата (МСК): {target_date}")

    page.click("label.form-control")
    page.wait_for_selector(".b-calendar", timeout=CALENDAR_SELECTOR_TIMEOUT_MS)
    _wallet_stage("payout_calendar_open")
    _find_and_pick_date(page, target_date)
    page.locator("button:has-text('Применить')").click()
    page.wait_for_load_state("networkidle", timeout=NETWORKIDLE_TIMEOUT_MS)
    _wallet_stage("payout_apply_done")
    time.sleep(2)

    _wallet_stage("payout_export_click")
    with page.expect_download(timeout=180000) as d:
        page.locator("button:has-text('Экспорт')").click()
    download = d.value

    path = os.path.join(DOWNLOAD_DIR, f"payout_{ts}.xlsx")
    download.save_as(path)
    logger.info(f"✅ Payout сохранён: {path}")
    return path


def _file_meta(p: str) -> dict:
    pp = Path(p)
    st = pp.stat()
    return {"path": str(pp), "size": st.st_size, "mtime": st.st_mtime}


def _calc_wallet_fingerprint(payin_path: str, payout_path: str) -> str:
    payload = {"payin": _file_meta(payin_path), "payout": _file_meta(payout_path)}
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _download_wallet_files(ts: str, params: WalletJobParams) -> Tuple[str, str]:
    _wallet_stage("wallet_playwright_start")
    with sync_playwright() as p:
        browser = None
        context = None
        page = None
        try:
            browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
            if os.path.exists(AUTH_STATE_FILE):
                context = browser.new_context(storage_state=AUTH_STATE_FILE, accept_downloads=True)
            else:
                context = browser.new_context(accept_downloads=True)

            page = context.new_page()
            _ensure_logged_in(page, context)

            payin_path = _download_payin(page, ts, params.payin_days_back)
            payout_path = _download_payout(page, ts, params.payout_days_back)
        finally:
            close_playwright_stack(page=page, context=context, browser=browser)

    _wallet_stage("wallet_playwright_done")
    return payin_path, payout_path


def run_wallet_cycle() -> None:
    if not LOGIN or not PASSWORD:
        raise RuntimeError("ANTARES_LOGIN / ANTARES_PASSWORD не заданы")

    ts = datetime.now(MSK_TZ).strftime("%H.%M")
    params = _load_wallet_params()

    logger.info(f"💼 WalletHandler стартовал (ts={ts})")
    logger.info(
        f"⚙️ wallet params: payin_days_back={params.payin_days_back}, "
        f"payout_days_back={params.payout_days_back}"
    )

    payin_path, payout_path = _download_wallet_files(ts, params)
    fp = _calc_wallet_fingerprint(payin_path, payout_path)

    last = state_get("wallet", "last_fingerprint")
    if last == fp:
        logger.info("🕒 [wallet] no changes -> skip analyzer")
        append_event(type="job_skipped_no_changes", job_type="wallet", payload={"fingerprint": fp[:10]})
        _wallet_stage("wallet_cycle_done")
        return

    _wallet_stage("wallet_analyze_start")
    dto = build_wallet_stats_dto(
        payin_path=payin_path,
        payout_path=payout_path,
        analyzer="wallet",
        rules_force_sync=False,
    )

    rendered = render_wallet(dto)

    main_text = (rendered.main_text or "").strip()
    alerts_text = (rendered.alerts_text or "").strip()

    if not main_text:
        raise RuntimeError("wallet: rendered main report is empty")

    _wallet_stage("wallet_send_start")
    if routes_from_rules_v2_enabled():
        send_message_to_route(ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT, main_text)
        if alerts_text:
            send_message_to_route(ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT, alerts_text)
    else:
        chat_id = os.getenv("TELEGRAM_CHAT_ID_WALLET", "").strip()
        if not chat_id:
            logger.warning("TELEGRAM_CHAT_ID_WALLET не задан — отправка отключена")
        else:
            send_text(text=main_text, chat_id=chat_id)
            if alerts_text:
                send_text(text=alerts_text, chat_id=chat_id)

    state_update("wallet", {
        "last_fingerprint": fp,
        "last_sent_ts": int(time.time()),
    })
    _wallet_stage("wallet_cycle_done")


if __name__ == "__main__":
    run_wallet_cycle()
