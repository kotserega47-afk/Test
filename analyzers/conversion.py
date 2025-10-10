# analyzers/conversion.py

import os
import re
import yaml
import pandas as pd
from openpyxl import Workbook

from utils.logger import logger
from utils.excel_utils import flatten_lists_in_df, write_df_to_sheet
from integrations.telegram_bot import send_message_sync
from load_data import process_cards, process_conversion
from db.database import get_session


# ==============================
# 📦 Конфигурация
# ==============================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "config", "conversion_config.yaml"))

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

COLUMNS = CONFIG.get("columns", {})
PARTNER_SETTINGS = {}


# ==============================
# 🔧 Вспомогательные функции
# ==============================
def normalize_colname(name: str) -> str:
    return str(name).strip().lower().replace("ё", "е")


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    name = name.lower().strip()
    name = name.replace("ё", "е").replace("амобайл", "а-мобайл")
    name = re.sub(r"\(\d+\)$", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip(", ")


def normalize_partners_list(partners_str: str) -> list:
    partners = str(partners_str).split(",")
    return [normalize_name(p) for p in partners if p.strip()]


def load_data(filepath, col_mapping: dict):
    """Загрузка CSV/XLSX и нормализация колонок"""
    if filepath.endswith((".xlsx", ".xls")):
        df = pd.read_excel(filepath, dtype=str)
    else:
        df = pd.read_csv(filepath, sep=None, engine="python", encoding="utf-8")

    logger.info(f"[load_data] Загружен файл {filepath} с колонками: {list(df.columns)}")

    norm_cols = {normalize_colname(c): c for c in df.columns}
    new_cols = {}
    for key, expected_name in col_mapping.items():
        expected_norm = normalize_colname(expected_name)
        if expected_norm not in norm_cols:
            raise ValueError(f"❌ В файле нет колонки '{expected_name}' (ожидали для '{key}')")
        new_cols[norm_cols[expected_norm]] = key
    df.rename(columns=new_cols, inplace=True)

    for c in ["card", "status", "partner"]:
        if c in df:
            df[c] = df[c].astype(str).str.strip().str.lower()
    if "datetime" in df:
        df["datetime"] = pd.to_datetime(df["datetime"], format="%d.%m.%Y %H:%M:%S", errors="coerce")

    required = [c for c in ["card", "status", "datetime"] if c in df]
    if required:
        df.dropna(subset=required, inplace=True)
    if "datetime" in df:
        df.sort_values("datetime", ascending=False, inplace=True)

    return df


def init_partner_settings():
    """Создание словаря партнёров из YAML"""
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
            "exclude": exclude_periods
        }
    return partners


PARTNER_SETTINGS = init_partner_settings()


def count_consecutive_errors(group, partner_name: str) -> int:
    count = max_count = 0
    for status in group["status"]:
        if status == "оплачен":
            break
        if status == "ошибка":
            count += 1
            max_count = max(max_count, count)
        else:
            count = 0
    return max_count


# ==============================
# 🧩 Основной анализ (run)
# ==============================
def run(conv_file: str, card_files: list, col_mapping: dict) -> dict:
    conv_df = load_data(conv_file, col_mapping)
    if "datetime" in conv_df.columns:
        conv_df["datetime"] = pd.to_datetime(conv_df["datetime"], format="%d.%m.%Y %H:%M:%S", errors="coerce")
    conv_df["partner_norm"] = conv_df["partner"].apply(normalize_name)

    card_df_list = [
        load_data(f, {"card": "Карта", "partner": "Партнер", "status": "Статус"})
        for f in card_files
    ]
    card_df = pd.concat(card_df_list, ignore_index=True) if card_df_list else pd.DataFrame(columns=["card", "partner", "status"])
    card_df["partner_list"] = card_df["partner"].apply(normalize_partners_list)

    results, problem_cards = [], []
    VALID_STATUSES = [s.strip().lower() for s in CONFIG.get("valid_statuses", [])]

    for (card, partner_norm), group in conv_df.groupby(["card", "partner_norm"]):
        settings = PARTNER_SETTINGS.get(partner_norm, {"threshold": 4, "exclude": []})
        threshold = settings.get("threshold", 4)

        # Убираем исключённые периоды
        for start_ex, end_ex in settings.get("exclude", []):
            group = group[~group["datetime"].between(start_ex, end_ex)]

        card_status_raw = card_df.loc[card_df["card"] == card, "status"]
        card_status = (
            card_status_raw.iloc[0].strip().lower()
            if not card_status_raw.empty and pd.notna(card_status_raw.iloc[0])
            else None
        )

        max_errors = count_consecutive_errors(group, partner_norm)
        results.append({
            "card": card,
            "partner": partner_norm,
            "max_consecutive_errors": max_errors,
            "threshold": threshold,
            "status": card_status,
        })

        partners_list = [p for sublist in card_df.loc[card_df["card"] == card, "partner_list"] for p in sublist]
        if partner_norm in partners_list and max_errors >= threshold and card_status in VALID_STATUSES:
            problem_cards.append({
                "card": card,
                "partner": partner_norm,
                "max_consecutive_errors": max_errors,
                "status": card_status,
            })

    problem_cards_df = pd.DataFrame(problem_cards)
    summary = {
        "Карт в работе": conv_df["card"].nunique(),
        "Max ошибки": max([r["max_consecutive_errors"] for r in results], default=0),
        "Карты на отключение": int(problem_cards_df["card"].nunique()) if not problem_cards_df.empty else 0
    }

    wb = Workbook()
    wb.remove(wb.active)
    write_df_to_sheet(wb, "Data_conv", flatten_lists_in_df(conv_df))
    write_df_to_sheet(wb, "Data_card", flatten_lists_in_df(card_df.drop(columns=["partner_list"], errors="ignore")))
    if not problem_cards_df.empty:
        write_df_to_sheet(wb, "Отключить", flatten_lists_in_df(problem_cards_df.sort_values(by=["partner", "card"])))

        with get_session() as session:
            process_cards(card_df, session)
            process_conversion(card_df, conv_df, session)

    return {
        "summary": summary,
        "workbook": wb,
        "problem_cards": problem_cards_df,
    }


# ==============================
# ⚡ Ускоренный анализ (run_fast)
# ==============================
def run_fast(conv_file: str, card_files: list, col_mapping: dict, generate_excel: bool = False) -> dict:
    try:
        usecols = list(col_mapping.values())
        if conv_file.endswith((".xlsx", ".xls")):
            df = pd.read_excel(conv_file, dtype=str, usecols=usecols)
        else:
            df = pd.read_csv(conv_file, dtype=str, usecols=usecols, sep=None, engine="python")
    except ValueError as e:
        logger.warning(f"[run_fast] ⚠️ Ошибка при чтении колонок: {e}")
        df = pd.read_excel(conv_file, dtype=str) if conv_file.endswith((".xlsx", ".xls")) else pd.read_csv(conv_file, dtype=str, sep=None, engine="python")

    df.rename(columns={v: k for k, v in col_mapping.items()}, inplace=True)
    df["status"] = df["status"].astype(str).str.strip().str.lower()
    df["partner_norm"] = df["partner"].apply(normalize_name)
    df["datetime"] = pd.to_datetime(df["datetime"], format="%d.%m.%Y %H:%M:%S", errors="coerce")
    df.dropna(subset=["card", "datetime", "status"], inplace=True)

    valid_statuses = [s.lower() for s in CONFIG.get("valid_statuses", [])]
    df = df[df["status"].isin(["ошибка", "оплачен"] + valid_statuses)]

    for partner_name, settings in PARTNER_SETTINGS.items():
        for start, end in settings.get("exclude", []):
            mask = (df["partner_norm"] == partner_name) & (df["datetime"].between(start, end))
            df = df[~mask]

    card_df_list = [load_data(f, {"card": "Карта", "partner": "Партнёр", "status": "Статус"}) for f in card_files]
    card_df = pd.concat(card_df_list, ignore_index=True) if card_df_list else pd.DataFrame(columns=["card", "partner", "status"])
    card_df["partner_list"] = card_df["partner"].apply(normalize_partners_list)
    card_df["status"] = card_df["status"].astype(str).str.strip().str.lower()

    df.sort_values(["card", "partner_norm", "datetime"], inplace=True)
    df["err_block"] = (df["status"] != "ошибка").cumsum()
    df["series_len"] = df.groupby(["card", "partner_norm", "err_block"])["status"].transform(lambda s: len(s) if s.iloc[0] == "ошибка" else 0)
    max_errors = df.groupby(["card", "partner_norm"])["series_len"].max().reset_index(name="max_consecutive_errors")

    settings_df = pd.DataFrame([{"partner_norm": p, "threshold": s.get("threshold", 4)} for p, s in PARTNER_SETTINGS.items()])
    merged = max_errors.merge(settings_df, on="partner_norm", how="left").fillna({"threshold": 4})

    card_status_map = card_df.set_index("card")["status"].to_dict()
    card_partners_map = card_df.set_index("card")["partner_list"].to_dict()

    merged["status"] = merged["card"].map(card_status_map).astype(str).str.strip().str.lower()
    merged["partner_list"] = merged["card"].map(card_partners_map)

    problem = merged[
        (merged["max_consecutive_errors"] >= merged["threshold"]) &
        (merged["status"].isin(valid_statuses)) &
        (merged.apply(lambda row: row["partner_norm"] in (row["partner_list"] or []), axis=1))
    ].copy()

    problem.rename(columns={"partner_norm": "partner"}, inplace=True)

    summary = {
        "Карт в работе": df["card"].nunique(),
        "Max ошибки": merged["max_consecutive_errors"].max() if not merged.empty else 0,
        "Карты на отключение": problem["card"].nunique(),
    }

    wb = None
    if generate_excel:
        wb = Workbook()
        wb.remove(wb.active)
        write_df_to_sheet(wb, "Data", flatten_lists_in_df(df))
        write_df_to_sheet(wb, "Проблемные карты", flatten_lists_in_df(problem))

    # 🧩 Запись в БД
    try:
        with get_session() as session:
            process_cards(card_df, session)
            process_conversion(card_df, df, session)
    except Exception as e:
        logger.warning(f"[run_fast] ⚠️ Ошибка при записи в БД: {e}")

    # 💾 Запись истории отключений
    try:
        if not problem.empty and "card" in problem.columns:
            from db.models import CardDisableHistory
            with get_session() as session:
                for card_number in problem["card"].dropna().unique():
                    session.add(CardDisableHistory(card_number=card_number))
            logger.info(f"[run_fast] 💾 Добавлено {len(problem)} отключений в БД.")
    except Exception as e:
        logger.warning(f"[run_fast] ⚠️ Ошибка при записи истории отключений: {e}")

    return {
        "summary": summary,
        "problem_cards": problem,
        "workbook": wb,
    }
