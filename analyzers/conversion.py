# analyzers/conversion.py
import os
import re
import yaml
import pandas as pd
from utils.logger import logger

# -----------------------------
# Конфигурация
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "config", "conversion_config.yaml"))

try:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = yaml.safe_load(f)
except Exception as e:
    logger.error(f"❌ Ошибка при загрузке конфигурации {CONFIG_PATH}: {e}")
    CONFIG = {}

PARTNERS = CONFIG.get("partners", {})
POOLS = CONFIG.get("pools", {})
COLUMNS = CONFIG.get("columns", {})

# -----------------------------
# Вспомогательные функции
# -----------------------------
def normalize_colname(name: str) -> str:
    return str(name).strip().lower().replace("ё", "е")

def normalize_partner_name(name: str) -> str:
    return re.sub(r'\s*\(\d+\)$', '', str(name)).strip()

def normalize_partners_list(partners_str: str) -> list[str]:
    partners = str(partners_str).split(',')
    return [normalize_partner_name(p).lower() for p in partners if p.strip()]

def load_data(filepath: str, col_mapping: dict) -> pd.DataFrame:
    """Универсальная загрузка CSV/XLSX с нормализацией и проверкой колонок"""
    if not os.path.exists(filepath):
        logger.warning(f"⚠️ Файл {filepath} не найден, возвращаю пустой DataFrame")
        return pd.DataFrame(columns=col_mapping.keys())

    try:
        if filepath.endswith((".xlsx", ".xls")):
            df = pd.read_excel(filepath, dtype=str)
        else:
            df = pd.read_csv(filepath, sep=None, engine="python", encoding="utf-8", dtype=str)
    except Exception as e:
        logger.error(f"❌ Ошибка при чтении {filepath}: {e}")
        return pd.DataFrame(columns=col_mapping.keys())

    # Нормализация заголовков
    norm_cols = {normalize_colname(c): c for c in df.columns}
    new_cols = {}
    for key, expected_name in col_mapping.items():
        expected_norm = normalize_colname(expected_name)
        if expected_norm not in norm_cols:
            logger.warning(f"⚠️ В {filepath} нет колонки '{expected_name}' (ожидали для '{key}')")
            continue
        new_cols[norm_cols[expected_norm]] = key
    df.rename(columns=new_cols, inplace=True)

    # Очистка и нормализация
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

def count_consecutive_errors(group: pd.DataFrame, partner_name: str) -> tuple[int, int]:
    """Подсчёт максимальной серии ошибок подряд"""
    count = max_count = 0
    threshold = CONFIG.get("partners", {}).get(partner_name, 4)
    for status in group["status"]:
        if status == "оплачен":
            break
        if status in ["ошибка", "ожидает оплаты"]:
            count += 1
            max_count = max(max_count, count)
        else:
            count = 0
    return max_count, threshold

# -----------------------------
# Основная функция
# -----------------------------
def run(conv_file: str, card_files: list[str], col_mapping: dict) -> dict:
    """Главная функция обработки: возвращает summary, data_sheets и problem_cards"""
    try:
        # --- Conversion ---
        conv_df = load_data(conv_file, col_mapping)

        # --- Card files ---
        card_df_list = [load_data(f, {"card": "Карта", "partner": "Партнер"}) for f in card_files]
        card_df = pd.concat(card_df_list, ignore_index=True) if card_df_list else pd.DataFrame(columns=["card", "partner"])
        card_df["partner_list"] = card_df["partner"].apply(normalize_partners_list)

        # --- Data sheets ---
        data_sheets = {
            "Data_conv": conv_df,
            "Data_card": card_df
        }

        # --- Подсчёт ошибок и проблемные карты ---
        results = []
        problem_cards = []

        for (card, partner), group in conv_df.groupby(["card", "partner"]):
            max_errors, threshold = count_consecutive_errors(group, partner)
            results.append({
                "card": card,
                "partner": partner,
                "max_consecutive_errors": max_errors,
                "threshold": threshold
            })

            partners_list = []
            if card in card_df["card"].values:
                partners_list = [p for sublist in card_df.loc[card_df["card"] == card, "partner_list"] for p in sublist]

            if partner in partners_list and max_errors >= threshold:
                problem_cards.append({
                    "card": card,
                    "partner": partner,
                    "max_consecutive_errors": max_errors
                })

        problem_cards_df = pd.DataFrame(problem_cards, columns=["card", "partner", "max_consecutive_errors"])

        # --- Summary ---
        summary = {
            "Карт в работе": conv_df["card"].nunique() if not conv_df.empty else 0,
            "Max ошибки": max([r["max_consecutive_errors"] for r in results]) if results else 0,
            "Карты на отключение": problem_cards_df["card"].nunique() if not problem_cards_df.empty else 0
        }

        for key, pool_name in POOLS.items():
            conv_cards = conv_df.loc[conv_df["partner"].str.contains(pool_name, case=False, na=False), "card"].nunique()
            card_cards = card_df.loc[card_df["partner"].str.contains(pool_name, case=False, na=False), "card"].nunique()
            summary[f"Объем {pool_name}"] = conv_cards / card_cards if card_cards else 0

        # --- Stat ---
        stat_list = []
        for card in conv_df["card"].unique():
            row = {"Карта": card}
            for bank in ["Сбер", "Тинь"]:
                bank_mask = conv_df["partner"].str.contains(bank, case=False, na=False) & (conv_df["card"] == card)
                row[f"{bank} Ошибки"] = conv_df.loc[bank_mask & conv_df["status"].isin(["ошибка", "ожидает оплаты"]), "status"].count()
                row[f"{bank} Успешно"] = conv_df.loc[bank_mask & (conv_df["status"] == "оплачен"), "status"].count()
            stat_list.append(row)

        data_sheets["Stat"] = pd.DataFrame(stat_list)
        data_sheets["Отключить"] = problem_cards_df

        return {
            "summary": summary,
            "data_sheets": data_sheets,
            "problem_cards": problem_cards_df
        }

    except Exception as e:
        logger.exception(f"❌ Ошибка в conversion.run: {e}")
        return {
            "summary": {"Ошибка": str(e)},
            "data_sheets": {},
            "problem_cards": pd.DataFrame()
        }
