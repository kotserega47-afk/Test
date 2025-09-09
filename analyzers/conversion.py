# analyzers/conversion.py
import pandas as pd
import re
import yaml
from utils.logger import logger

CONFIG_PATH = "config/conversion_config.yaml"
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

# -----------------------------
# Вспомогательные функции
# -----------------------------
def normalize_colname(name: str) -> str:
    return str(name).strip().lower().replace("ё", "е")

def normalize_partner_name(name: str) -> str:
    return re.sub(r'\s*\(\d+\)$', '', str(name)).strip()

def normalize_partners_list(partners_str: str) -> list:
    partners = str(partners_str).split(',')
    return [normalize_partner_name(p).lower() for p in partners if p.strip()]

def load_data(filepath, col_mapping: dict):
    if filepath.endswith((".xlsx", ".xls")):
        df = pd.read_excel(filepath, dtype=str)
    else:
        df = pd.read_csv(filepath, sep=None, engine="python", encoding="utf-8")

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

def count_consecutive_errors(group, partner_name: str) -> tuple[int, int]:
    count = max_count = 0
    threshold = CONFIG.get("partners", {}).get(partner_name, 4)
    for status in group['status']:
        if status == 'оплачен': break
        if status in ['ошибка', 'ожидает оплаты']:
            count += 1
            max_count = max(max_count, count)
        else:
            count = 0
    return max_count, threshold

# -----------------------------
# Основная функция
# -----------------------------
def run(conv_file: str, card_files: list, col_mapping: dict) -> dict:
    conv_df = load_data(conv_file, col_mapping)

    # Загружаем все Card файлы
    card_df_list = [load_data(f, {'card': 'Карта', 'partner': 'Партнер'}) for f in card_files]
    card_df = pd.concat(card_df_list, ignore_index=True)

    # -----------------------------
    # Data_conv и Data_card
    # -----------------------------
    data_sheets = {
        "Data_conv": conv_df,
        "Data_card": card_df
    }

    # -----------------------------
    # Статистика
    # -----------------------------
    results = []
    problem_cards = []

    for (card, partner), group in conv_df.groupby(['card', 'partner']):
        max_errors, threshold = count_consecutive_errors(group, partner)
        results.append({"card": card, "partner": partner,
                        "max_consecutive_errors": max_errors,
                        "threshold": threshold})
        # проверка на отключение
        partners_list = normalize_partners_list(','.join(card_df.loc[card_df['card']==card, 'partner']))
        if partner in partners_list and max_errors >= threshold:
            problem_cards.append({"card": card, "partner": partner, "max_consecutive_errors": max_errors})

    problem_cards_df = pd.DataFrame(problem_cards)

    # -----------------------------
    # Summary
    # -----------------------------
    pools = CONFIG.get("pools", {})
    summary = {
        "Карт в работе": conv_df['card'].nunique(),
        "Max ошибки": max([r['max_consecutive_errors'] for r in results]) if results else 0,
        "Карты на отключение": problem_cards_df['card'].nunique() if not problem_cards_df.empty else 0
    }

    # Объём по пуллам
    for key, pool_name in pools.items():
        conv_cards = conv_df.loc[conv_df['partner'].str.contains(pool_name.lower()), 'card'].nunique()
        card_cards = card_df.loc[card_df['partner'].str.contains(pool_name.lower()), 'card'].nunique()
        summary[f"Объем {pool_name}"] = conv_cards / card_cards if card_cards else 0

    # -----------------------------
    # Лист Stat
    # -----------------------------
    stat_list = []
    for card in conv_df['card'].unique():
        row = {"Карта": card}
        for bank in ["Сбер", "Тинь"]:
            bank_mask = conv_df['partner'].str.contains(bank.lower()) & (conv_df['card'] == card)
            row[f"{bank} Ошибки"] = conv_df.loc[bank_mask & conv_df['status'].isin(['ошибка','ожидает оплаты']), 'status'].count()
            row[f"{bank} Успешно"] = conv_df.loc[bank_mask & (conv_df['status']=='оплачен'), 'status'].count()
        stat_list.append(row)
    data_sheets["Stat"] = pd.DataFrame(stat_list)

    # -----------------------------
    # Лист Отключить
    # -----------------------------
    data_sheets["Отключить"] = problem_cards_df[['card','partner','max_consecutive_errors']]

    return {
        "summary": summary,
        "data_sheets": data_sheets,
        "problem_cards": problem_cards_df
    }
