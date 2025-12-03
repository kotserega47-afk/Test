# integrations/downloader.py
import os
import re
import yaml
import tempfile
import time
import pandas as pd
from openpyxl import Workbook
from datetime import datetime
from utils.logger import logger
from utils.excel_utils import flatten_lists_in_df, write_df_to_sheet
from integrations.telegram_bot import send_message_sync, send_file_sync
from integrations.dropbox_watcher import download_file

# -----------------------------
# Загрузка конфигурации
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "config", "conversion_config.yaml"))

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f) or {}

COLUMNS = CONFIG.get("columns", {})
VALID_STATUSES = [s.strip().lower() for s in CONFIG.get("valid_statuses", [])]
POOLS = CONFIG.get("pools", {})

CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_ANALIZ")
if not CHAT_ID:
    logger.warning("⚠️ TELEGRAM_CHAT_ID_ANALIZ не задан — Telegram-уведомления не будут отправлены.")

# -----------------------------
# Безопасные обёртки Telegram
# -----------------------------
def send(text: str):
    """Безопасная отправка текста в Telegram."""
    if not CHAT_ID:
        logger.warning("⚠️ CHAT_ID не задан, сообщение не отправлено.")
        return
    try:
        send_message_sync(text, CHAT_ID)
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке сообщения: {e}")

def send_file(path: str, caption: str | None = None):
    """Безопасная отправка файла в Telegram."""
    if not CHAT_ID:
        logger.warning("⚠️ CHAT_ID не задан, файл не отправлен.")
        return
    if not os.path.exists(path):
        logger.warning(f"⚠️ Файл не найден для отправки: {path}")
        return
    try:
        send_file_sync(path, caption, CHAT_ID)
        time.sleep(3)  # даём очереди Telegram успеть отправить
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке файла: {e}")

# -----------------------------
# Вспомогательные функции
# -----------------------------
def normalize_colname(name: str) -> str:
    return str(name).strip().lower().replace("ё", "е")

def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    name = name.lower().strip().replace("ё", "е").replace("амобайл", "а-мобайл")
    name = re.sub(r"\(\d+\)$", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip(", ")

def normalize_partners_list(partners_str: str) -> list:
    partners = str(partners_str).split(",")
    return [normalize_name(p) for p in partners if p.strip()]

def load_data(filepath, col_mapping: dict):
    """Универсальная загрузка CSV/XLSX и нормализация колонок по mapping."""
    if filepath.endswith((".xlsx", ".xls")):
        df = pd.read_excel(filepath, dtype=str)
    else:
        df = pd.read_csv(filepath, sep=None, engine="python", encoding="utf-8", dtype=str)

    logger.info(f"[load_data] Загружен файл {filepath} с колонками: {list(df.columns)}")

    norm_cols = {normalize_colname(c): c for c in df.columns}
    new_cols = {}
    for key, expected_name in col_mapping.items():
        expected_norm = normalize_colname(expected_name)
        if expected_norm not in norm_cols:
            raise ValueError(f"❌ Нет колонки '{expected_name}' (ожидали для '{key}') в {filepath}")
        new_cols[norm_cols[expected_norm]] = key

    df.rename(columns=new_cols, inplace=True)

    for c in ["card", "status", "partner"]:
        if c in df:
            df[c] = df[c].astype(str).str.strip().str.lower()

    if "datetime" in df:
        df["datetime"] = pd.to_datetime(df["datetime"], format="%d.%m.%Y %H:%M:%S", errors="coerce")
        df.dropna(subset=["datetime"], inplace=True)
        df.sort_values("datetime", ascending=False, inplace=True)

    return df

def init_partner_settings():
    """Загрузка порогов и исключений из YAML."""
    partners = {}
    for raw_name, settings in CONFIG.get("partners", {}).items():
        norm_name = normalize_name(raw_name)
        exclude_periods = []
        for period in settings.get("exclude", []):
            start = pd.to_datetime(period.get("start"), format="%d.%m.%Y %H:%M:%S", errors="coerce")
            end = pd.to_datetime(period.get("end"), format="%d.%m.%Y %H:%M:%S", errors="coerce")
            if pd.notna(start) and pd.notna(end):
                exclude_periods.append((start, end))
        partners[norm_name] = {
            "threshold": settings.get("threshold", 4),
            "exclude": exclude_periods,
        }
    return partners

PARTNER_SETTINGS = init_partner_settings()

def _send_problem_cards_to_telegram(problem_df: pd.DataFrame) -> None:
    """Отправляет список карт на отключение батчами."""
    if problem_df.empty:
        return

    if not all(c in problem_df.columns for c in ["card", "partner", "max_consecutive_errors"]):
        send("⚠️ Пропущено формирование списка: отсутствуют нужные колонки.")
        return

    msg_lines = [
        f"{row['card']} {row['partner']} {row['max_consecutive_errors']}"
        for _, row in problem_df.dropna(subset=["card", "partner"]).iterrows()
    ]
    total = len(msg_lines)
    if total == 0:
        return

    BATCH_SIZE = 500
    for i in range(0, total, BATCH_SIZE):
        chunk = msg_lines[i:i + BATCH_SIZE]
        send("🚫 Карты на отключение:\n" + "\n".join(chunk))
    logger.info(f"[telegram] Отправлен список {total} карт на отключение.")

def count_last_error_streak(df: pd.DataFrame) -> pd.DataFrame:
    """Подсчёт последних непрерывных серий ошибок для каждой пары."""
    results = []
    if df.empty:
        return pd.DataFrame(columns=["card", "partner_norm", "max_consecutive_errors"])
    for (card, partner), group in df.groupby(["card", "partner_norm"], sort=False):
        statuses = group.sort_values("datetime", ascending=False)["status"].tolist()
        streak = 0
        for s in statuses:
            if s == "ошибка":
                streak += 1
            else:
                break
        results.append({"card": card, "partner_norm": partner, "max_consecutive_errors": streak})
    return pd.DataFrame(results)

# -----------------------------
# Основная функция анализа
# -----------------------------
def run(conv_file: str, card_files: list, col_mapping: dict, *,
        generate_excel: bool = True, send_telegram: bool = True) -> dict:

    logger.info(f"[run] 🚀 Начало анализа: {os.path.basename(conv_file)}")

    # === 1. Загрузка conversion ===
    usecols = list(col_mapping.values())
    try:
        conv_df = pd.read_excel(conv_file, dtype=str, usecols=usecols) \
            if conv_file.endswith((".xlsx", ".xls")) \
            else pd.read_csv(conv_file, dtype=str, usecols=usecols, sep=None, engine="python")
    except ValueError:
        conv_df = pd.read_excel(conv_file, dtype=str) \
            if conv_file.endswith((".xlsx", ".xls")) \
            else pd.read_csv(conv_file, dtype=str, sep=None, engine="python")

    conv_df.rename(columns={v: k for k, v in col_mapping.items()}, inplace=True)
    conv_df["status"] = conv_df["status"].astype(str).str.strip().str.lower()
    conv_df["partner_norm"] = conv_df["partner"].apply(normalize_name)
    conv_df["datetime"] = pd.to_datetime(conv_df["datetime"], format="%d.%m.%Y %H:%M:%S", errors="coerce")
    conv_df.dropna(subset=["card", "datetime", "status"], inplace=True)
    conv_df = conv_df[conv_df["status"].isin(["ошибка", "оплачен"] + VALID_STATUSES)]
    logger.info(f"[run] 📄 Загружено {len(conv_df)} строк из conversion.")

    # === 2. Загрузка card-файлов ===
    card_df_list = []
    for f in card_files or []:
        try:
            card_df_list.append(load_data(f, {"card": "Карта", "partner": "Партнёр", "status": "Статус", "pool": "Пул"}))
        except Exception as e:
            logger.warning(f"[run] ⚠️ Пропускаю card-файл {os.path.basename(f)}: {e}")
    card_df = pd.concat(card_df_list, ignore_index=True) if card_df_list else pd.DataFrame(columns=["card", "partner", "status"])
    if not card_df.empty:
        card_df["partner_list"] = card_df["partner"].apply(normalize_partners_list)
        card_df["status"] = card_df["status"].astype(str).str.strip().str.lower()
    else:
        card_df["partner_list"] = []

    logger.info(f"[run] 🧩 Загружено {len(card_df)} карт из card-файлов.")

    # === 3. Подсчёт серий ошибок ===
    conv_df.sort_values(["card", "partner_norm", "datetime"], inplace=True)
    max_errors = count_last_error_streak(conv_df)

    settings_df = pd.DataFrame(
        [{"partner_norm": p, "threshold": s.get("threshold", 4)} for p, s in PARTNER_SETTINGS.items()]
    )
    merged = max_errors.merge(settings_df, on="partner_norm", how="left").fillna({"threshold": 4})

    card_status_map = card_df.set_index("card")["status"].to_dict() if not card_df.empty else {}
    card_partners_map = card_df.set_index("card")["partner_list"].to_dict() if not card_df.empty else {}
    merged["status"] = merged["card"].map(card_status_map)
    merged["partner_list"] = merged["card"].map(card_partners_map)

    problem_mask = (
        (merged["max_consecutive_errors"] >= merged["threshold"]) &
        (merged["status"].isin(VALID_STATUSES)) &
        merged.apply(lambda r: isinstance(r["partner_list"], list) and r["partner_norm"] in r["partner_list"], axis=1)
    )
    problem = merged.loc[problem_mask].copy()
    problem.rename(columns={"partner_norm": "partner"}, inplace=True)

    # === 4. Формирование Excel-отчёта ===
    wb = Workbook()
    wb.remove(wb.active)
    if not problem.empty:
        write_df_to_sheet(wb, "Отключить", flatten_lists_in_df(problem))
    tmp_dir = tempfile.gettempdir()
    report_path = os.path.join(tmp_dir, f"report_{os.path.splitext(os.path.basename(conv_file))[0]}_{datetime.now():%d.%m.%Y}.xlsx")
    wb.save(report_path)
    logger.info(f"[run] 📁 Отчёт сохранён: {report_path}")

    # === 5. Telegram ===
    if send_telegram:
        if not problem.empty:
            _send_problem_cards_to_telegram(problem)
        else:
            send("ℹ️ Нет карт, превысивших порог ошибок.")
        summary_text = f"✅ Анализ {os.path.basename(conv_file)} завершён.\nКарты на отключение: {len(problem)}"
        send(summary_text)
        send_file(report_path, caption=f"📊 Отчёт по {os.path.basename(conv_file)}")

    return {"problem_cards": problem, "report_path": report_path}
