# integrations/downloader.py
import os, sys, subprocess, time
from datetime import datetime, timedelta
from utils.logger import logger
from playwright.sync_api import sync_playwright
from integrations.dropbox_watcher import upload_file
from main import process_file
from run_once_guard import acquire_lock, release_lock
from integrations.telegram_bot import send_message_sync
import zoneinfo  # встроено в Python 3.9+

# === Устанавливаем системную таймзону для всех логов и времени ===
os.environ["TZ"] = "Europe/Moscow"
time.tzset()

# Добавляем корень проекта в PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Проверим, установлен ли Chromium Playwright
playwright_cache = "/root/.cache/ms-playwright/chromium_headless_shell-1194/chrome-linux/headless_shell"
if not os.path.exists(playwright_cache):
    print("⚙️ Chromium не найден, устанавливаем...")
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True
        )
        print("✅ Chromium успешно установлен.")
    except Exception as e:
        print(f"❌ Не удалось установить Chromium: {e}")
        raise

# === Вспомогательная функция времени ===
def _ts() -> str:
    """Возвращает локальное время (Москва) для имен файлов."""
    tz = zoneinfo.ZoneInfo("Europe/Moscow")
    return datetime.now(tz).strftime("%H.%M")


LOGIN = os.getenv("ANTARES_LOGIN")
PASSWORD = os.getenv("ANTARES_PASSWORD")
DROPBOX_INPUT_PATH = os.getenv("DROPBOX_INPUT_PATH")
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1").strip().lower() in {"1", "true", "yes", "y"}

BASE_DIR = "/tmp"
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
AUTH_STATE_FILE = os.path.join(BASE_DIR, "auth_state.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def _upload_local_to_dropbox(local_path: str, dropbox_name: str) -> str:
    """Заливает локальный файл в DROPBOX_INPUT_PATH и возвращает имя."""
    if not DROPBOX_INPUT_PATH:
        raise RuntimeError("DROPBOX_INPUT_PATH не задан в .env")
    dropbox_path = f"{DROPBOX_INPUT_PATH}/{dropbox_name}"
    ok = upload_file(local_path, dropbox_path)
    if not ok:
        raise RuntimeError(f"Не удалось загрузить {local_path} в {dropbox_path}")
    logger.info(f"📤 Загружено в Dropbox: {dropbox_path}")
    return dropbox_name

def _ensure_logged_in(page, context):
    if os.path.exists(AUTH_STATE_FILE):
        logger.info("🔐 Используем сохранённую сессию")
        return
    logger.info("🔑 Логинимся в Antares")
    page.goto("https://antares.plus/lkcard/#/login")
    page.fill("input[type='text']", LOGIN)
    page.fill("input[type='password']", PASSWORD)
    page.click("button:has-text('Войти')")
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    context.storage_state(path=AUTH_STATE_FILE)
    logger.info("✅ Сессия сохранена")

def _download_wallet_export(page, timestamp: str) -> str:
    logger.info("⬇️ Wallet → экспорт…")
    page.goto("https://antares.plus/lkcard/#/wallet")
    page.wait_for_load_state("networkidle")
    with page.expect_download() as d1:
        page.click("button:has-text('Экспорт')")
    download1 = d1.value
    local_path = os.path.join(DOWNLOAD_DIR, f"card_{timestamp}.xlsx")
    download1.save_as(local_path)
    logger.info(f"✅ Сохранено локально: {local_path}")
    return local_path

def _download_payin_export(page, timestamp: str) -> str:
    logger.info("⚙️ PayIn → выбираем дату (сегодня − 2) и экспорт…")
    page.goto("https://antares.plus/lkcard/#/payin")
    page.wait_for_load_state("networkidle")
    tz = zoneinfo.ZoneInfo("Europe/Moscow")
    target_date = (datetime.now(tz) - timedelta(days=2)).strftime("%Y-%m-%d")
    logger.info(f"📅 Дата для выбора: {target_date}")

    page.click("label.form-control")
    page.wait_for_selector(".b-calendar", timeout=10000)
    try:
        page.click(f"[data-date='{target_date}']")
        logger.info(f"✅ Выбрана дата {target_date}")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось выбрать дату {target_date}: {e}")

    page.click("button:has-text('Применить')")
    page.wait_for_load_state("networkidle")
    time.sleep(1.5)

    with page.expect_download(timeout=300000) as d2:
        page.click("button:has-text('Экспорт')")
    download2 = d2.value
    local_path = os.path.join(DOWNLOAD_DIR, f"conversion_{timestamp}.xlsx")
    download2.save_as(local_path)
    logger.info(f"✅ Сохранено локально: {local_path}")
    return local_path

def _download_extra_files(page, timestamp: str) -> list[str]:
    local_paths = []
    tz = zoneinfo.ZoneInfo("Europe/Moscow")

    # CD-файл
    try:
        logger.info("⬇️ Wallet → экспорт (для cd)...")
        page.goto("https://antares.plus/lkcard/#/wallet")
        page.wait_for_load_state("networkidle")
        with page.expect_download() as d_cd:
            page.click("button:has-text('Экспорт')")
        dl_cd = d_cd.value
        cd_local = os.path.join(DOWNLOAD_DIR, f"cd_{timestamp}.xlsx")
        dl_cd.save_as(cd_local)
        logger.info(f"✅ Сохранено локально (cd): {cd_local}")
        local_paths.append(cd_local)
    except Exception as e:
        logger.warning(f"⚠️ Ошибка при скачивании cd: {e}")

    # Payout-файл
    try:
        logger.info("⬇️ Payout → выставляем календарь на неделю и экспортируем...")
        page.goto("https://antares.plus/lkcard/#/vyplaty")
        page.wait_for_load_state("networkidle")
        page.click("label.form-control")
        page.wait_for_selector(".b-calendar", timeout=10000)
        start_date = (datetime.now(tz) - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date = datetime.now(tz).strftime("%Y-%m-%d")
        logger.info(f"📅 Диапазон дат: {start_date} → {end_date}")
        try:
            page.click(f"[data-date='{start_date}']")
            page.click(f"[data-date='{end_date}']")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось выбрать диапазон дат: {e}")
        page.click("button:has-text('Применить')")
        page.wait_for_load_state("networkidle")
        time.sleep(1.5)
        with page.expect_download(timeout=300000) as d_pay:
            page.click("button:has-text('Экспорт')")
        dl_pay = d_pay.value
        payout_local = os.path.join(DOWNLOAD_DIR, f"payout_{timestamp}.xlsx")
        dl_pay.save_as(payout_local)
        logger.info(f"✅ Сохранено локально (payout): {payout_local}")
        local_paths.append(payout_local)
    except Exception as e:
        logger.warning(f"⚠️ Ошибка при скачивании payout: {e}")

    return local_paths

def run_download():
    if not LOGIN or not PASSWORD:
        raise RuntimeError("ANTARES_LOGIN/ANTARES_PASSWORD не заданы в .env")

    timestamp = _ts()
    logger.info(f"🕒 downloader стартовал, ts={timestamp}")
    send_message_sync(f"🕒 Запущен выгрузчик данных (ts={timestamp})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            context = browser.new_context(accept_downloads=True)
            if os.path.exists(AUTH_STATE_FILE):
                context = browser.new_context(storage_state=AUTH_STATE_FILE, accept_downloads=True)
            page = context.new_page()
            _ensure_logged_in(page, context)
            card_local = _download_wallet_export(page, timestamp)
            send_message_sync(f"✅ Скачан card_{timestamp}.xlsx")
            conversion_local = _download_payin_export(page, timestamp)
            send_message_sync(f"✅ Скачан conversion_{timestamp}.xlsx")
            extra_locals = _download_extra_files(page, timestamp)
            for pth in extra_locals:
                send_message_sync(f"✅ Скачан {os.path.basename(pth)}")
        except Exception as e:
            msg = f"❌ Ошибка во время скачивания: {e}"
            logger.exception(msg)
            send_message_sync(msg)
            raise
        finally:
            browser.close()

    try:
        card_name = f"card_{timestamp}.xlsx"
        conv_name = f"conversion_{timestamp}.xlsx"
        _upload_local_to_dropbox(card_local, card_name)
        _upload_local_to_dropbox(conversion_local, conv_name)
        uploaded_extra = []
        for path in extra_locals:
            base = os.path.basename(path)
            _upload_local_to_dropbox(path, base)
            uploaded_extra.append(base)
        send_message_sync("📤 Все файлы успешно загружены в Dropbox.")
        if not acquire_lock(timeout=600):
            logger.info("⏳ Анализ уже идёт, пропускаем мгновенный запуск.")
            send_message_sync("⏳ Анализ уже выполняется — выгрузчик ждёт следующего часа.")
            return
        try:
            logger.info(f"🚀 process_file(conversion): {conv_name} + aux={card_name}")
            process_file(conv_name, aux_filename=card_name)
            send_message_sync(f"🚀 Запущен анализ Conversion ({conv_name})")
            if any(s.startswith("cd_") for s in uploaded_extra) and any(s.startswith("payout_") for s in uploaded_extra):
                cd_name = next(s for s in uploaded_extra if s.startswith("cd_"))
                payout_name = next(s for s in uploaded_extra if s.startswith("payout_"))
                logger.info(f"🚀 process_file(payout): {payout_name} + aux={cd_name}")
                process_file(payout_name, aux_filename=cd_name)
                send_message_sync(f"🚀 Запущен анализ Payout ({payout_name})")
            else:
                logger.info("ℹ️ Файлы cd_/payout_ не найдены — Payout пропущен.")
                send_message_sync("ℹ️ Файлы cd_/payout_ не найдены — Payout пропущен.")
        finally:
            release_lock()
            send_message_sync("✅ Downloader завершил цикл успешно.")
    except Exception as e:
        msg = f"❌ Ошибка при загрузке или анализе: {e}"
        logger.exception(msg)
        send_message_sync(msg)
        raise

if __name__ == "__main__":
    try:
        logger.info("🚀 Запуск run_download() из контейнера Railway")
        send_message_sync("🚀 Downloader запущен вручную на Railway")
        run_download()
    except Exception as e:
        msg = f"❌ Downloader завершился с ошибкой: {e}"
        logger.exception(msg)
        send_message_sync(msg)
        raise
