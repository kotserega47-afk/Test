# integrations/downloader.py

import os, sys, subprocess, time
# === Добавляем корень проекта в PYTHONPATH ===
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from playwright.sync_api import sync_playwright
from integrations.dropbox_watcher import upload_file
from main import process_file
from run_once_guard import acquire_lock, release_lock
from integrations.telegram_bot import send_message_sync
import zoneinfo  # встроено в Python 3.9+

icon, name = LOG_PROFILES["DOWNLOADER"]
logger = get_logger(name, icon)

# === Устанавливаем системную таймзону для всех логов и времени ===

tz = zoneinfo.ZoneInfo("Europe/Moscow")
datetime.now(tz)

CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID_ANALIZ") or "").strip()


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


def _ts() -> str:
    tz = zoneinfo.ZoneInfo("Europe/Moscow")
    return datetime.now(tz).strftime("%H.%M")

def _send_tg(text: str) -> None:
    if not CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID_ANALIZ не задан — отправка отключена")
        return
    send_message_sync(text, chat_id=CHAT_ID)

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

def _ensure_logged_in(page, context) -> None:
    logger.info("🔑 Проверяем авторизацию в Antares…")

    page.goto("https://antares.plus/lkcard/#/wallet", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    if "login" not in page.url.lower():
        logger.info("✅ Сессия активна")
        return

    logger.info("🔑 Сессия недействительна, логинимся заново…")
    page.goto("https://antares.plus/lkcard/#/login", wait_until="domcontentloaded")

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
        raise RuntimeError("Логин не выполнен")

    context.storage_state(path=AUTH_STATE_FILE)
    logger.info("✅ Новая сессия сохранена")

def _download_wallet_export(page, timestamp: str) -> str:
    logger.info("⬇️ Wallet → экспорт…")
    try:
        # Открываем страницу и ждём полной загрузки DOM
        page.goto("https://antares.plus/lkcard/#/wallet", wait_until="domcontentloaded", timeout=20000)

        # Ждём появления кнопки "Экспорт"
        page.wait_for_selector("button.btn-primary:has-text('Экспорт')", state="visible", timeout=60000)

        # Нажимаем экспорт и ждём скачивание
        with page.expect_download(timeout=360000) as d1:
            page.click("button.btn-primary:has-text('Экспорт')")

        download1 = d1.value
        local_path = os.path.join(DOWNLOAD_DIR, f"card_{timestamp}.xlsx")
        download1.save_as(local_path)
        logger.info(f"✅ Сохранено локально: {local_path}")
        return local_path

    except Exception as e:
        screenshot_path = os.path.join(DOWNLOAD_DIR, f"payin_error_{timestamp}.png")
        html_path = os.path.join(DOWNLOAD_DIR, f"payin_error_{timestamp}.html")
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            logger.error(f"❌ Ошибка при экспорте: {e}. Скриншот: {screenshot_path}, HTML: {html_path}")
        except Exception as inner:
            logger.error(f"⚠️ Не удалось сохранить скриншот/HTML: {inner}")
        raise

def _find_and_pick_date(page, target_date: str) -> bool:
    selector = f"[data-date='{target_date}']"

    for _ in range(12):
        if page.locator(selector).count() > 0:
            page.locator(selector).first.click(force=True)
            logger.info(f"✅ Дата выбрана: {target_date}")
            return True

        prev_btn = page.locator("button[aria-label='Previous month']")
        if prev_btn.count() == 0:
            logger.warning("⚠️ Кнопка Previous month не найдена")
            return False

        prev_btn.first.click(force=True)
        page.wait_for_timeout(200)

    logger.warning(f"⚠️ Дата {target_date} не найдена в пределах 12 месяцев")
    return False

def _download_payin_export(page, timestamp: str) -> str:
    logger.info("⚙️ PayIn → выбираем дату (сегодня − 2) и экспорт…")

    try:
        tz = zoneinfo.ZoneInfo("Europe/Moscow")
        target_date = (datetime.now(tz) - timedelta(days=2)).strftime("%Y-%m-%d")
        logger.info(f"📅 Дата для выбора: {target_date}")

        page.goto("https://antares.plus/lkcard/#/payin", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)

        # 1) Сначала используем старый рабочий триггер из твоего файла
        calendar_opened = False

        try:
            trigger_btn = page.locator("button.btn.h-auto").nth(0)
            trigger_btn.wait_for(state="visible", timeout=10000)
            trigger_btn.click(force=True)
            page.wait_for_selector(".b-calendar", state="visible", timeout=10000)
            calendar_opened = True
            logger.info("✅ Календарь открыт через button.btn.h-auto")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось открыть календарь через button.btn.h-auto: {e}")

        # 2) fallback на label.form-control
        if not calendar_opened:
            try:
                trigger_label = page.locator("label.form-control").first
                trigger_label.wait_for(state="visible", timeout=10000)
                trigger_label.click(force=True)
                page.wait_for_selector(".b-calendar", state="visible", timeout=10000)
                calendar_opened = True
                logger.info("✅ Календарь открыт через label.form-control")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось открыть календарь через label.form-control: {e}")

        # 3) fallback на input.form-control
        if not calendar_opened:
            trigger_input = page.locator("input.form-control").first
            trigger_input.wait_for(state="visible", timeout=10000)
            trigger_input.click(force=True)
            page.wait_for_selector(".b-calendar", state="visible", timeout=10000)
            calendar_opened = True
            logger.info("✅ Календарь открыт через input.form-control")

        # 4) Пробуем прямой выбор даты — как в старом рабочем коде
        date_selected = False
        try:
            page.click(f"[data-date='{target_date}']", timeout=5000)
            logger.info(f"✅ Выбрана дата {target_date} прямым кликом")
            date_selected = True
        except Exception as e:
            logger.warning(f"⚠️ Прямой клик по дате не сработал: {e}")

        # 5) Если прямой клик не сработал — fallback через пролистывание месяцев
        if not date_selected:
            ok = _find_and_pick_date(page, target_date)
            if not ok:
                raise RuntimeError(f"Не удалось выбрать дату {target_date} в календаре PayIn")

        apply_btn = page.locator("button:has-text('Применить')").first
        apply_btn.wait_for(state="visible", timeout=10000)
        apply_btn.click(force=True)

        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)

        export_btn = page.locator("button:has-text('Экспорт')").first
        export_btn.wait_for(state="visible", timeout=20000)

        with page.expect_download(timeout=300000) as d2:
            export_btn.click(force=True)

        download2 = d2.value
        local_path = os.path.join(DOWNLOAD_DIR, f"conversion_{timestamp}.xlsx")
        download2.save_as(local_path)

        logger.info(f"✅ Сохранено локально: {local_path}")
        return local_path

    except Exception as e:
        screenshot_path = os.path.join(DOWNLOAD_DIR, f"payin_error_{timestamp}.png")
        html_path = os.path.join(DOWNLOAD_DIR, f"payin_error_{timestamp}.html")

        try:
            page.screenshot(path=screenshot_path, full_page=True)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            logger.error(
                f"❌ Ошибка PayIn: {e}. "
                f"Скриншот: {screenshot_path}, HTML: {html_path}"
            )
        except Exception as inner:
            logger.error(f"⚠️ Не удалось сохранить диагностику PayIn: {inner}")

        raise

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
        calendar_trigger = page.locator("input.form-control").first
        calendar_trigger.wait_for(state="visible", timeout=20000)
        calendar_trigger.click(force=True)

        page.wait_for_selector(".b-calendar", timeout=20000)
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
    _send_tg(f"🕒 Запущен выгрузчик данных (ts={timestamp})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
        extra_locals = []
        try:
            if os.path.exists(AUTH_STATE_FILE):
                context = browser.new_context(storage_state=AUTH_STATE_FILE, accept_downloads=True)
            else:
                context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(20000)
            _ensure_logged_in(page, context)
            card_local = _download_wallet_export(page, timestamp)
            _send_tg(f"✅ Скачан card_{timestamp}.xlsx")
            conversion_local = _download_payin_export(page, timestamp)
            _send_tg(f"✅ Скачан conversion_{timestamp}.xlsx")
            extra_locals = _download_extra_files(page, timestamp)
            for pth in extra_locals:
                _send_tg(f"✅ Скачан {os.path.basename(pth)}")
        except Exception as e:
            msg = f"❌ Ошибка во время скачивания: {e}"
            logger.exception(msg)
            _send_tg(msg)
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
        _send_tg("📤 Все файлы успешно загружены в Dropbox.")
        if not acquire_lock(timeout=600):
            logger.info("⏳ Анализ уже идёт, пропускаем мгновенный запуск.")
            _send_tg("⏳ Анализ уже выполняется — выгрузчик ждёт следующего часа.")
            return
        try:
            logger.info(f"🚀 process_file(conversion): {conv_name} + aux={card_name}")
            process_file(conv_name, aux_filename=card_name)
            _send_tg(f"🚀 Запущен анализ Conversion ({conv_name})")
            if any(s.startswith("cd_") for s in uploaded_extra) and any(
                    s.startswith("payout_") for s in uploaded_extra):
                cd_name = next((s for s in uploaded_extra if s.startswith("cd_")), None)
                payout_name = next((s for s in uploaded_extra if s.startswith("payout_")), None)

                if cd_name and payout_name:
                    logger.info(f"🚀 process_file(payout): {payout_name} + aux={cd_name}")
                    process_file(payout_name, aux_filename=cd_name)
                    _send_tg(f"🚀 Запущен анализ Payout ({payout_name})")
            else:
                logger.info("ℹ️ Файлы cd_/payout_ не найдены — Payout пропущен.")
                _send_tg("ℹ️ Файлы cd_/payout_ не найдены — Payout пропущен.")
        finally:
            release_lock()
            _send_tg("✅ Downloader завершил цикл успешно.")
    except Exception as e:
        msg = f"❌ Ошибка при загрузке или анализе: {e}"
        logger.exception(msg)
        _send_tg(msg)
        raise

if __name__ == "__main__":
    try:
        logger.info("🚀 Запуск run_download() из контейнера Railway")
        _send_tg("🚀 Downloader запущен вручную на Railway")
        run_download()
    except Exception as e:
        msg = f"❌ Downloader завершился с ошибкой: {e}"
        logger.exception(msg)
        _send_tg(msg)
        raise
