# analyzers/payout.py
import os
import yaml
from utils.logger import logger
import pandas as pd
from utils.excel_utils import style_worksheet, write_df_to_sheet
from openpyxl import Workbook
from datetime import datetime

# путь к конфигу
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "..", "config", "payout_config.yaml")

# загрузка конфига
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f) or {}

def run(payout_file: str, card_files: list, *args, **kwargs):
    logger.info(f"[payout] 🚀 Начало анализа: {os.path.basename(payout_file)}")

    # Проверяем, что есть файл направлений (cd)
    if not card_files:
        logger.warning("[payout] ⚠️ Не найден cd-файл (направления карт).")
        return {}

    cd_file = card_files[0]

    # === 1️⃣ Загрузка payout-файла ===
    try:
        df_payout = pd.read_excel(payout_file, dtype=str)
        logger.info(f"[payout] Загружен payout-файл: {os.path.basename(payout_file)} ({len(df_payout)} строк)")
    except Exception as e:
        logger.exception(f"[payout] ❌ Ошибка чтения payout-файла: {e}")
        return {}

    # === 2️⃣ Загрузка cd-файла ===
    try:
        df_cd = pd.read_excel(cd_file, dtype=str)
        df_cd.columns = df_cd.columns.str.strip()
        df_cd.rename(columns=lambda c: c.strip().lower(), inplace=True)
        logger.info(f"[payout] Загружен cd-файл: {os.path.basename(cd_file)} ({len(df_cd)} строк)")
    except Exception as e:
        logger.exception(f"[payout] ❌ Ошибка чтения cd-файла: {e}")
        return {}

    # === 3️⃣ Нормализация столбцов payout ===
    df_payout.columns = df_payout.columns.str.strip()
    df_payout.rename(columns=lambda c: c.strip().lower(), inplace=True)

    rename_map = {
        "статус": "status",
        "инфо": "info",
        "дата/время создания": "дата/время создания",
        "телефон": "телефон",
        "выделено": "выделено",
        "карта": "карта"
    }
    df_payout.rename(columns=rename_map, inplace=True)

    # приводим дату/время в формат datetime
    if "дата/время создания".lower() in df_payout.columns:
        col = "дата/время создания".lower()
        df_payout[col] = pd.to_datetime(df_payout[col], format="%d.%m.%Y %H:%M:%S", errors="coerce")

    # убираем лишние пробелы в "карта"
    if "карта" in df_payout.columns:
        df_payout["карта"] = df_payout["карта"].astype(str).str.strip()

    # убираем лишние пробелы в "карта" cd-файла
    if "карта" in df_cd.columns:
        df_cd["карта"] = df_cd["карта"].astype(str).str.strip()

    logger.info("[payout] ✅ Данные загружены и нормализованы.")

    # === 4️⃣ Исключаем карты с направлением IN ===
    if "направление" in df_cd.columns and "карта" in df_cd.columns:
        in_cards = set(df_cd.loc[df_cd["направление"].str.upper() == "IN", "карта"])
        before = len(df_payout)
        df_payout = df_payout[~df_payout["карта"].isin(in_cards)].copy()
        logger.info(f"[payout] 🧭 Исключено {before - len(df_payout)} строк по направлению IN.")
    else:
        logger.warning("[payout] ⚠️ В cd-файле отсутствуют нужные колонки: 'Карта' и 'Направление'.")

    # === 5️⃣ Загружаем настройки из YAML ===
    payouts_errors = CONFIG.get("PayoutsErrors", {}) or {}
    ignore_errors = CONFIG.get("IgnoreErrors", []) or []

    # Преобразуем в нижний регистр для удобства поиска
    payouts_errors_norm = {k.lower().strip(): v for k, v in payouts_errors.items()}
    ignore_errors_norm = [x.lower().strip() for x in ignore_errors]

    logger.info(f"[payout] 📘 Загружено {len(payouts_errors_norm)} известных ошибок и {len(ignore_errors_norm)} игнорируемых.")

    # === 6️⃣ Фильтрация строк payout-файла ===

    # интересуют только статусы "ошибка" и "оплачен"
    valid_statuses = {"ошибка", "оплачен"}
    df_payout["status"] = df_payout["status"].astype(str).str.lower().str.strip()

    before = len(df_payout)
    df_payout = df_payout[df_payout["status"].isin(valid_statuses)].copy()
    logger.info(f"[payout] 🧮 Оставлено {len(df_payout)} строк (из {before}), где статус ошибка/оплачен.")

    # фильтруем игнорируемые ошибки (IgnoreErrors, частичное совпадение)
    if "info" in df_payout.columns:
        before = len(df_payout)
        df_payout = df_payout[
            ~df_payout["info"].astype(str).str.lower().apply(
                lambda x: any(phrase in x for phrase in ignore_errors_norm)
            )
        ].copy()
        logger.info(f"[payout] 🔍 Исключено {before - len(df_payout)} строк по IgnoreErrors.")

    # === 7️⃣ Анализ последовательностей ошибок ===

    problem_cards = []   # для списка "Перевести в IN"
    check_cards = set()  # для списка "Проверить"

    # Проверяем, что нужные столбцы есть
    required_cols = {"карта", "status", "info", "дата/время создания", "телефон", "выделено"}
    if not required_cols.issubset(df_payout.columns):
        logger.warning(f"[payout] ⚠️ В payout-файле отсутствуют нужные колонки: {required_cols - set(df_payout.columns)}")
        return {}

    # Группируем по карте
    for card, group in df_payout.groupby("карта"):
        group = group.sort_values("дата/время создания", ascending=False)

        consecutive = 0
        last_info = None
        last_datetime = None
        phone = str(group["телефон"].iloc[0]) if "телефон" in group.columns else ""
        pool = str(group["выделено"].iloc[0]) if "выделено" in group.columns else ""

        for _, row in group.iterrows():
            status = str(row["status"]).lower().strip()
            info = str(row["info"]).lower().strip()

            # Если статус "оплачен" → серия обрывается
            if status == "оплачен":
                consecutive = 0
                last_info = None
                continue

            # Если статус "ошибка"
            if status == "ошибка":
                # Проверяем, известная ли ошибка (частичное совпадение)
                matched_error = next((err for err in payouts_errors_norm if err in info), None)

                if matched_error:
                    threshold = payouts_errors_norm[matched_error].get("threshold", 1)
                    # если продолжается та же ошибка
                    if last_info == matched_error:
                        consecutive += 1
                    else:
                        consecutive = 1
                        last_info = matched_error
                    last_datetime = row["дата/время создания"]

                    if consecutive >= threshold:
                        problem_cards.append({
                            "Карта": card,
                            "Телефон": phone,
                            "Выделено": pool,
                            "Info": matched_error,
                            "Количество подряд ошибок": consecutive,
                            "Последняя дата ошибки": last_datetime
                        })
                        break  # карта уже попала в список — дальше не анализируем
                else:
                    # неизвестная ошибка → в список "Проверить"
                    check_cards.add((card, phone, pool, info))
    # === 8️⃣ Формирование итоговых таблиц ===

    # Преобразуем списки в DataFrame
    df_problem = pd.DataFrame(problem_cards)
    df_check = pd.DataFrame(list(check_cards), columns=["Карта", "Телефон", "Выделено", "Info"])

    # Удаляем дубликаты (на всякий случай)
    if not df_problem.empty:
        df_problem.drop_duplicates(subset=["Карта"], inplace=True)
    if not df_check.empty:
        df_check.drop_duplicates(subset=["Карта"], inplace=True)

    # === 9️⃣ Формирование Excel-отчёта ===
    wb = Workbook()
    wb.remove(wb.active)

    if not df_problem.empty:
        write_df_to_sheet(wb, "Перевести в IN", df_problem)
    else:
        ws = wb.create_sheet("Перевести в IN")
        ws.append(["Нет карт для перевода в IN"])

    if not df_check.empty:
        write_df_to_sheet(wb, "Проверить", df_check)
    else:
        ws = wb.create_sheet("Проверить")
        ws.append(["Нет карт для проверки"])

    # Сохраняем отчёт во временный файл
    tmp_name = f"report_{os.path.basename(payout_file)}"
    tmp_path = os.path.join(os.getenv("TMP", "/tmp"), tmp_name)
    wb.save(tmp_path)
    logger.info(f"[payout] 📁 Отчёт сохранён: {tmp_path}")

    # === 🔟 Отправляем файл в Telegram и перемещаем ===
    from integrations.telegram_bot import send_message_sync, send_file_sync
    from integrations.dropbox_watcher import move_file

    try:
        # 1️⃣ Отправляем отчёт в Telegram
        if os.path.exists(tmp_path):
            caption = (
                f"📊 Отчёт по {os.path.basename(payout_file)}\n"
                f"Карты на перевод: {len(df_problem)}\n"
                f"Карты на проверку: {len(df_check)}"
            )
            send_file_sync(tmp_path, caption=caption)
            logger.info(f"[payout] 📤 Отчёт отправлен в Telegram: {tmp_path}")

        # 2️⃣ Перемещаем исходный payout-файл в /processed с добавлением даты
        DROPBOX_PROCESSED_PATH = os.getenv("DROPBOX_PROCESSED_PATH")
        today_str = datetime.now().strftime("(%d.%m.%Y)")
        base_name = os.path.basename(payout_file)
        new_name = (
            base_name[:-5] + f"_{today_str}.xlsx"
            if base_name.lower().endswith(".xlsx")
            else base_name + f"_{today_str}.xlsx"
        )

        src_dropbox_path = os.path.join(os.getenv("DROPBOX_INPUT_PATH"), base_name)
        dest_dropbox_path = os.path.join(DROPBOX_PROCESSED_PATH, new_name)

        move_file(src_dropbox_path, dest_dropbox_path)
        logger.info(f"[payout] ✅ Файл {base_name} перемещён в /processed как {new_name}")

    except Exception as e:
        logger.exception(f"[payout] ⚠️ Ошибка при отправке/перемещении файла: {e}")

    # === 11️⃣ Возвращаем сводку ===
    logger.info(
        f"[payout] ✅ Анализ завершён: перевод={len(df_problem)}, проверка={len(df_check)}"
    )
    return {
        "workbook": wb,
        "report_path": tmp_path,
        "summary": {
            "Карты на перевод": len(df_problem),
            "Карты на проверку": len(df_check)
        }
    }
