# integrations/downloader_wallets.py
from __future__ import annotations

import os
import sys
import time
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple

from playwright.sync_api import sync_playwright
from zoneinfo import ZoneInfo

# Добавляем корень проекта в пути (как у тебя было)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from analyzers.wallet_analyzer import analyze_wallets

from core.event_log import append_event
from core.config_manager import get_job_params_overrides, get_job_param
from core.state_provider import get_job_value, set_job_value

icon, name = LOG_PROFILES["WALLET"]
logger = get_logger(name, icon)

MSK_TZ = ZoneInfo("Europe/Moscow")

LOGIN = os.getenv("ANTARES_LOGIN")
PASSWORD = os.getenv("ANTARES_PASSWORD")
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1").lower() in {"1", "true", "yes", "y"}

BASE_DIR = "/tmp"
DOWNLOAD_DIR = os.path.join(BASE_DIR, "wallet_handler")
AUTH_STATE_FILE = os.path.join(BASE_DIR, "auth_state_wallets.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


@dataclass(frozen=True)
class WalletJobParams:
    payin_days_back: int = 2
    payout_days_back: int = 7


def _load_wallet_params() -> WalletJobParams:
    """
    Источник истины: rules.xlsx → sheet job_params (job=wallet)
    YAML оставлен только как fallback, чтобы не ломать прод при миграции.
    """
    overrides = get_job_params_overrides(force_sync=False)
    payin_days = get_job_param(overrides, job="wallet", key="payin_days_back", default=2)
    payout_days = get_job_param(overrides, job="wallet", key="payout_days_back", default=7)

    # YAML fallback (временно)
    try:
        import yaml

        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "wallet_config.yaml",
        )
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            payin_days = int(cfg.get("download_periods", {}).get("payin_days_back", payin_days))
            payout_days = int(cfg.get("download_periods", {}).get("payout_days_back", payout_days))
    except Exception:
        pass

    # guardrails
    try:
        payin_days = int(payin_days)
    except Exception:
        payin_days = 2

    try:
        payout_days = int(payout_days)
    except Exception:
        payout_days = 7

    if payin_days < 0:
        payin_days = 0
    if payout_days < 0:
        payout_days = 0

    return WalletJobParams(payin_days_back=payin_days, payout_days_back=payout_days)


def _ensure_logged_in(page, context) -> None:
    """Авторизация в Antares UI. Если есть storage_state — используем его."""
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


def _find_and_pick_date(page, target_date: str) -> bool:
    """
    target_date: YYYY-MM-DD (как в data-date)
    """
    selector = f"[data-date='{target_date}']"

    for _ in range(12):
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

    page.goto("https://antares.plus/lkcard/#/payin")
    page.wait_for_load_state("networkidle")

    target_date = (datetime.now(MSK_TZ) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    logger.info(f"📅 PayIn дата (МСК): {target_date}")

    page.click("label.form-control")
    page.wait_for_selector(".b-calendar")

    _find_and_pick_date(page, target_date)

    page.locator("button:has-text('Применить')").click()
    page.wait_for_load_state("networkidle")
    time.sleep(2)

    with page.expect_download(timeout=180000) as d:
        page.click("button:has-text('Экспорт')")
    download = d.value

    path = os.path.join(DOWNLOAD_DIR, f"payin_{ts}.xlsx")
    download.save_as(path)

    logger.info(f"✅ PayIn сохранён: {path}")
    return path


def _download_payout(page, ts: str, days_back: int) -> str:
    logger.info("⬇️ Payout → экспорт…")

    page.goto("https://antares.plus/lkcard/#/vyplaty")
    page.wait_for_load_state("networkidle")

    target_date = (datetime.now(MSK_TZ) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    logger.info(f"📅 Payout дата (МСК): {target_date}")

    page.click("label.form-control")
    page.wait_for_selector(".b-calendar")

    _find_and_pick_date(page, target_date)

    page.locator("button:has-text('Применить')").click()
    page.wait_for_load_state("networkidle")
    time.sleep(2)

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
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])

        if os.path.exists(AUTH_STATE_FILE):
            context = browser.new_context(storage_state=AUTH_STATE_FILE, accept_downloads=True)
        else:
            context = browser.new_context(accept_downloads=True)

        page = context.new_page()
        _ensure_logged_in(page, context)

        payin_path = _download_payin(page, ts, params.payin_days_back)
        payout_path = _download_payout(page, ts, params.payout_days_back)

        browser.close()

    return payin_path, payout_path


def run_wallet_cycle() -> None:
    """
    Wallet-цикл:
      1) скачать payin/payout
      2) fp = sha256(meta(files))
      3) если fp == state.jobs.wallet.last_fingerprint → skip + event_log
      4) иначе → analyze_wallets
      5) записать fp в state только после успешного анализа
    """
    if not LOGIN or not PASSWORD:
        raise RuntimeError("ANTARES_LOGIN / ANTARES_PASSWORD не заданы")

    ts = datetime.now(MSK_TZ).strftime("%H.%M")
    params = _load_wallet_params()

    logger.info(f"🕒 WalletHandler стартовал (ts={ts})")
    logger.info(f"⚙️ wallet params: payin_days_back={params.payin_days_back}, payout_days_back={params.payout_days_back}")

    payin_path, payout_path = _download_wallet_files(ts, params)

    fp = _calc_wallet_fingerprint(payin_path, payout_path)
    last = get_job_value("wallet", "last_fingerprint", default=None, force_sync=False)

    if last == fp:
        logger.info("🟨 [wallet] no changes -> skip analyzer")
        append_event(type="job_skipped_no_changes", job_type="wallet", payload={"fingerprint": fp[:10]})
        return

    # анализ (если упадёт — fp НЕ сохранится, и следующий запуск не будет ошибочно skipped)
    analyze_wallets(payin_path, payout_path)

    # фиксируем fp только после успешного анализа
    set_job_value("wallet", "last_fingerprint", fp)


if __name__ == "__main__":
    run_wallet_cycle()