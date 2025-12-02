# wallet_analyzer_test.py

import os
import sys
import argparse
from datetime import datetime
import yaml
from dotenv import load_dotenv

# Подгружаем .env для запуска из консоли
load_dotenv()

# 0) Получаем тестовый чат (три способа: --chat-id > TELEGRAM_CHAT_ID > ошибка)
def _resolve_test_chat(cli_chat: str | None) -> str:
    if cli_chat:
        return str(cli_chat)
    env_chat = os.getenv("TELEGRAM_CHAT_ID")
    if env_chat:
        return env_chat
    raise RuntimeError("Укажите тестовый чат: --chat-id или TELEGRAM_CHAT_ID в .env")

# 1) Патчим телеграм: очередь → direct
import integrations.telegram_bot as tg
tg.send_message_sync = tg.send_message_direct  # без очереди, мгновенно

# 2) Импортируем остальное
from playwright.sync_api import sync_playwright
from integrations.downloader_wallets import (
    _ensure_logged_in, _download_payin, _download_payout,
    MSK_TZ, AUTH_STATE_FILE, DOWNLOAD_DIR, HEADLESS
)
from analyzers.wallet_analyzer import analyze_wallets

import yaml
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent

def load_cfg():
    cfg_path = ROOT / "config" / "wallet_config.yaml"
    if not cfg_path.exists():
        raise RuntimeError(f"Не найден конфиг: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def download_pair(payin_days_back: int, payout_days_back: int) -> tuple[str, str]:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    ts = datetime.now(MSK_TZ).strftime("%H.%M")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox"])
        if os.path.exists(AUTH_STATE_FILE):
            context = browser.new_context(storage_state=AUTH_STATE_FILE, accept_downloads=True)
        else:
            context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        _ensure_logged_in(page, context)
        payin_path = _download_payin(page, ts, payin_days_back)
        payout_path = _download_payout(page, ts, payout_days_back)
        browser.close()
    return payin_path, payout_path

def main():
    parser = argparse.ArgumentParser(
        description="Локальный тест Wallet Analyzer: скачивание PayIn+Payout и отправка отчёта в ТЕСТОВЫЙ Telegram."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--download", action="store_true", help="Скачать PayIn+Payout по конфигу.")
    mode.add_argument("--files", nargs=2, metavar=("PAYIN", "PAYOUT"), help="Использовать уже скачанные файлы.")

    parser.add_argument("--chat-id", help="Тестовый чат (перекрывает TELEGRAM_CHAT_ID).")
    parser.add_argument("--payin-days", type=int, help="Override download_periods.payin_days_back.")
    parser.add_argument("--payout-days", type=int, help="Override download_periods.payout_days_back.")

    args = parser.parse_args()

    # Определяем ТЕСТОВЫЙ чат и временно подставляем его как 'WALLET' для анализатора
    test_chat = _resolve_test_chat(args.chat_id)
    os.environ["TELEGRAM_CHAT_ID_WALLET"] = test_chat  # анализатор возьмёт его как свой модульный чат

    cfg = load_cfg()
    dl_cfg = cfg.get("download_periods", {})

    payin_days_back = args.payin_days if args.payin_days is not None else dl_cfg.get("payin_days_back", 2)
    payout_days_back = args.payout_days if args.payout_days is not None else dl_cfg.get("payout_days_back", 7)

    if args.download:
        payin_path, payout_path = download_pair(payin_days_back, payout_days_back)
    else:
        payin_path, payout_path = args.files
        if not os.path.exists(payin_path):
            raise FileNotFoundError(f"Не найден PayIn файл: {payin_path}")
        if not os.path.exists(payout_path):
            raise FileNotFoundError(f"Не найден Payout файл: {payout_path}")

    analyze_wallets(payin_path, payout_path)
    print(f"Готово: анализ отправлен в тестовый Telegram ({test_chat}).")

if __name__ == "__main__":
    os.environ.setdefault("TZ", "Europe/Moscow")
    main()