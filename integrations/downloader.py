# integrations/downloader.py
import os
import sys
import subprocess
import time
from datetime import datetime, timedelta
import zoneinfo  # встроено в Python 3.9+

# === Добавляем корень проекта в PYTHONPATH ===
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from integrations.conversion_pipeline import run_conversion_pipeline
from integrations.dropbox_watcher import upload_file
from integrations.telegram_bot import send_message_sync
from main import process_file
from run_once_guard import acquire_lock, release_lock


icon, name = LOG_PROFILES["DOWNLOADER"]
logger = get_logger(name, icon)

# === Устанавливаем системную таймзону для всех логов и времени ===
os.environ["TZ"] = "Europe/Moscow"
if hasattr(time, "tzset"):
    time.tzset()

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_ANALIZ")
if not CHAT_ID:
    raise RuntimeError("Не задан TELEGRAM_CHAT_ID_ANALIZ")

# Проверим / установим Chromium для Playwright
try:
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
except Exception as e:
    logger.warning(f"⚠️ Не удалось проверить/установить Chromium: {e}")

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
    screenshot_path = f"/app/logs/error_wallet_{timestamp}.png"
    html_path = f"/app/logs/error_wallet_{timestamp}.html"

    try:
        page.goto(
            "https://antares.plus/lkcard/#/wallet",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        page.wait_for_load_state("networkidle")

        export_btn = page.locator("button.btn-primary:has-text('Экспорт')").first
        export_btn.wait_for(state="visible", timeout=30000)

        page.wait_for_timeout(10000)

        with page.expect_download(timeout=300000) as d1:
            export_btn.click(force=True)

        download1 = d1.value
        local_path = os.path.join(DOWNLOAD_DIR, f"card_{timestamp}.xlsx")
        download1.save_as(local_path)

        logger.info(f"✅ Сохранено локально: {local_path}")
        return local_path

    except Exception as e:
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            logger.error(
                f"❌ Ошибка при экспорте Wallet: {e}. "
                f"Скриншот: {screenshot_path}, HTML: {html_path}"
            )
        except Exception as inner:
            logger.error(f"⚠️ Не удалось сохранить скриншот/HTML Wallet: {inner}")
        raise


def _download_payin_export(page, timestamp: str) -> str:
    logger.info("⚙️ PayIn → выбираем дату (сегодня − 2) и экспорт…")
    page.goto("https://antares.plus/lkcard/#/payin")
    page.wait_for_load_state("networkidle")
    tz = zoneinfo.ZoneInfo("Europe/Moscow")
    target_date = (datetime.now(tz) - timedelta(days=2)).strftime("%Y-%m-%d")
    logger.info(f"📅 Дата для выбора: {target_date}")

    page.locator("button.btn.h-auto").nth(0).click()
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

        page.goto("https://antares.plus/lkcard/#/wallet", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

        export_selector = "button:has-text('Экспорт')"
        page.wait_for_selector(export_selector, state="visible", timeout=30_000)

        logger.info("🟡 Кнопка 'Экспорт' найдена, запускаю скачивание cd...")

        with page.expect_download(timeout=90_000) as download_info:
            page.click(export_selector)

        dl_cd = download_info.value
        suggested_name = dl_cd.suggested_filename
        cd_local = os.path.join(DOWNLOAD_DIR, f"cd_{timestamp}.xlsx")

        dl_cd.save_as(cd_local)

        logger.info(f"✅ Скачивание cd успешно завершено: {suggested_name}")
        logger.info(f"✅ Сохранено локально (cd): {cd_local}")

        local_paths.append(cd_local)

    except Exception:
        logger.exception("⚠️ Ошибка при скачивании cd")

        try:
            error_png = os.path.join(DOWNLOAD_DIR, f"cd_error_{timestamp}.png")
            page.screenshot(path=error_png, full_page=True)
            logger.info(f"🖼 Скриншот сохранён: {error_png}")
        except Exception:
            logger.exception("Не удалось сохранить скриншот при ошибке скачивания cd")

        try:
            error_html = os.path.join(DOWNLOAD_DIR, f"cd_error_{timestamp}.html")
            with open(error_html, "w", encoding="utf-8") as f:
                f.write(page.content())
            logger.info(f"📝 HTML страницы сохранён: {error_html}")
        except Exception:
            logger.exception("Не удалось сохранить HTML страницы при ошибке скачивания cd")

    # Payout-файл
    try:
        logger.info("⬇️ Payout → выставляем левую дату на неделю назад и экспортируем...")

        page.goto("https://antares.plus/lkcard/#/vyplaty", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

        apply_button_selector = "button:has-text('Применить')"
        export_button_selector = "button:has-text('Экспорт')"

        # Левая дата = today - 7
        start_dt = datetime.now(tz) - timedelta(days=7)
        start_day = str(start_dt.day)
        logger.info(f"📅 Левая дата должна быть: {start_dt.strftime('%d.%m.%Y')}")

        # На странице два datepicker.
        # Берём кнопку первого (левого) datepicker, а не общий label.form-control.
        left_date_button = page.locator("div.b-form-datepicker button").nth(0)
        left_date_button.wait_for(state="visible", timeout=30_000)
        left_date_button.click()

        # Ждём открытие popup календаря первого datepicker
        calendar_popup = page.locator("div.b-form-datepicker div.dropdown-menu[role='dialog']").nth(0)
        calendar_popup.wait_for(state="visible", timeout=10_000)

        # В этом календаре день рендерится как span/button с текстом числа, а не как [data-date=...]
        day_locator = calendar_popup.locator(f"span.btn:has-text('{start_day}')").first
        day_locator.wait_for(state="visible", timeout=10_000)
        day_locator.click()

        logger.info(f"✅ В левом календаре выбрано число: {start_day}")

        page.wait_for_selector(apply_button_selector, state="visible", timeout=10_000)
        page.click(apply_button_selector)

        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)

        page.wait_for_selector(export_button_selector, state="visible", timeout=30_000)
        logger.info("🟡 Кнопка 'Экспорт' найдена, запускаю скачивание payout...")

        with page.expect_download(timeout=300_000) as d_pay:
            page.click(export_button_selector)

        dl_pay = d_pay.value
        payout_local = os.path.join(DOWNLOAD_DIR, f"payout_{timestamp}.xlsx")
        dl_pay.save_as(payout_local)

        logger.info(f"✅ Скачивание payout успешно завершено: {dl_pay.suggested_filename}")
        logger.info(f"✅ Сохранено локально (payout): {payout_local}")
        local_paths.append(payout_local)

    except Exception:
        logger.exception("⚠️ Ошибка при скачивании payout")

        try:
            error_png = os.path.join(DOWNLOAD_DIR, f"payout_error_{timestamp}.png")
            page.screenshot(path=error_png, full_page=True)
            logger.info(f"🖼 Скриншот сохранён: {error_png}")
        except Exception:
            logger.exception("Не удалось сохранить скриншот при ошибке скачивания payout")

        try:
            error_html = os.path.join(DOWNLOAD_DIR, f"payout_error_{timestamp}.html")
            with open(error_html, "w", encoding="utf-8") as f:
                f.write(page.content())
            logger.info(f"📝 HTML страницы сохранён: {error_html}")
        except Exception:
            logger.exception("Не удалось сохранить HTML страницы при ошибке скачивания payout")

        raise

    return local_paths


def _build_downloader_final_message(*, conv_ok: bool, payout_ok: bool, payout_attempted: bool) -> str:
    conv_line = f"Conversion: {'OK' if conv_ok else 'FAILED'}"
    if payout_attempted:
        payout_line = f"Payout: {'OK' if payout_ok else 'FAILED'}"
    else:
        payout_line = "Payout: not attempted"

    if conv_ok and payout_ok:
        headline = "✅ Downloader завершил цикл успешно"
    elif not conv_ok and payout_attempted and not payout_ok:
        headline = "❌ Downloader завершил цикл с ошибками"
    else:
        headline = "⚠️ Downloader завершил цикл с частичной ошибкой"

    return f"{headline}\n{conv_line}\n{payout_line}"


def run_download():
    if not LOGIN or not PASSWORD:
        raise RuntimeError("ANTARES_LOGIN/ANTARES_PASSWORD не заданы в .env")

    timestamp = _ts()
    logger.info(f"🕒 downloader стартовал, ts={timestamp}")
    send_message_sync(f"🕒 Запущен выгрузчик данных (ts={timestamp})", chat_id=CHAT_ID)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            context = browser.new_context(accept_downloads=True)
            if os.path.exists(AUTH_STATE_FILE):
                context = browser.new_context(
                    storage_state=AUTH_STATE_FILE,
                    accept_downloads=True,
                )

            page = context.new_page()
            _ensure_logged_in(page, context)

            card_local = _download_wallet_export(page, timestamp)
            send_message_sync(f"✅ Скачан card_{timestamp}.xlsx", chat_id=CHAT_ID)

            conversion_local = _download_payin_export(page, timestamp)
            send_message_sync(f"✅ Скачан conversion_{timestamp}.xlsx", chat_id=CHAT_ID)

            extra_locals = _download_extra_files(page, timestamp)
            for pth in extra_locals:
                send_message_sync(f"✅ Скачан {os.path.basename(pth)}", chat_id=CHAT_ID)

        except Exception as e:
            msg = f"❌ Ошибка во время скачивания: {e}"
            logger.exception(msg)
            send_message_sync(msg, chat_id=CHAT_ID)
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

        send_message_sync("📤 Все файлы успешно загружены в Dropbox.", chat_id=CHAT_ID)

        if not acquire_lock(timeout=600):
            logger.info("⏳ Анализ уже идёт, пропускаем мгновенный запуск.")
            send_message_sync(
                "⏳ Анализ уже выполняется — выгрузчик ждёт следующего часа.",
                chat_id=CHAT_ID,
            )
            return

        conv_ok = False
        payout_ok = True
        payout_attempted = False
        try:
            logger.info(f"▶️ Старт обработки Conversion: {conv_name} + aux={card_name}")
            send_message_sync(f"🚀 Запущен анализ Conversion ({conv_name})", chat_id=CHAT_ID)
            conv_ok = run_conversion_pipeline(
                conv_name,
                card_filename=card_name,
                source="downloader",
            )

            if any(s.startswith("cd_") for s in uploaded_extra) and any(
                    s.startswith("payout_") for s in uploaded_extra
            ):
                cd_name = next(s for s in uploaded_extra if s.startswith("cd_"))
                payout_name = next(s for s in uploaded_extra if s.startswith("payout_"))

                logger.info(f"▶️ Старт обработки Payout: {payout_name} + aux={cd_name}")
                send_message_sync(f"🚀 Запущен анализ Payout ({payout_name})", chat_id=CHAT_ID)
                payout_attempted = True
                payout_ok = process_file(payout_name, aux_filename=cd_name)
            else:
                logger.info("ℹ️ Файлы cd_/payout_ не найдены — Payout пропущен.")
                send_message_sync(
                    "ℹ️ Файлы cd_/payout_ не найдены — Payout пропущен.",
                    chat_id=CHAT_ID,
                )

        finally:
            release_lock()

        send_message_sync(
            _build_downloader_final_message(
                conv_ok=conv_ok,
                payout_ok=payout_ok,
                payout_attempted=payout_attempted,
            ),
            chat_id=CHAT_ID,
        )

    except Exception as e:
        msg = f"❌ Ошибка при загрузке или анализе: {e}"
        logger.exception(msg)
        send_message_sync(msg, chat_id=CHAT_ID)
        raise


if __name__ == "__main__":
    try:
        logger.info("🚀 Запуск run_download() из контейнера Railway")
        send_message_sync("🚀 Downloader запущен вручную на Railway", chat_id=CHAT_ID)
        run_download()
    except Exception as e:
        msg = f"❌ Downloader завершился с ошибкой: {e}"
        logger.exception(msg)
        send_message_sync(msg, chat_id=CHAT_ID)
        raise