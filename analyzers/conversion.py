# analyzers/conversion.py
import os
import re
import pandas as pd
from datetime import datetime
import yaml

# -----------------------------
# Конфиг YAML
# -----------------------------
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "conversion_config.yaml")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

# -----------------------------
# Вспомогательные функции
# -----------------------------
def normalize_colname(name: str) -> str:
    """Нормализует название колонки: lower, убираем пробелы, ё → е"""
    return str(name).strip().lower().replace("ё", "е")

def normalize_partner_name(name: str) -> str:
    """Удаляет (число) в конце и лишние пробелы, lower"""
    return re.sub(r'\s*\(\d+\)$', '', str(name)).strip().lower()

def normalize_partners_list(partners_str: str) -> list:
    """Разделяет список партнеров через запятую и нормализует"""
    partners = str(partners_str).split(',')
    return [normalize_partner_name(p) for p in partners if p.strip()]

def load_data(filepath, col_mapping: dict):
    """Загрузка CSV/Excel и нормализация колонок"""
    if filepath.endswith((".xlsx", ".xls")):
        df = pd.read_excel(filepath, dtype=str)
    else:
        df = pd.read_csv(filepath, sep=None, engine="python", encoding="utf-8")

    # Нормализуем заголовки
    norm_cols = {normalize_colname(c): c for c in df.columns}
    new_cols = {}
    for key, expected_name in col_mapping.items():
        expected_norm = normalize_colname(expected_name)
        if expected_norm not in norm_cols:
            raise ValueError(f"❌ В файле нет колонки '{expected_name}' (ожидали для '{key}')")
        new_cols[norm_cols[expected_norm]] = key
    df.rename(columns=new_cols, inplace=True)

    # Нормализуем значения
    if "card" in df:
        df["card"] = df["card"].astype(str).str.strip()
    if "status" in df:
        df["status"] = df["status"].astype(str).str.strip().str.lower()
    if "partner" in df:
        df["partner"] = df["partner"].astype(str).str.strip().str.lower()
    if "datetime" in df:
        df["datetime"] = pd.to_datetime(df["datetime"], format="%d.%m.%Y %H:%M:%S", errors="coerce")

    required = [c for c in ["card", "status", "datetime"] if c in df]
    if required:
        df.dropna(subset=required, inplace=True)
    if "datetime" in df:
        df.sort_values("datetime", ascending=False, inplace=True)

    return df

def count_consecutive_errors(group, partner_name: str) -> tuple[int, int]:
    """Считает максимальное количество ошибок подряд по конкретному партнеру"""
    count = max_count = 0
    threshold = CONFIG.get("partners", {}).get(partner_name, 4)
    for status in group['status']:
        if status == 'оплачен':
            break
        if status in ['ошибка', 'ожидает оплаты']:
            count += 1
            max_count = max(max_count, count)
        else:
            count = 0
    return max_count, threshold

# -----------------------------
# Основной анализ
# -----------------------------
def analyze_conversion(conv_file: str, card_file: str, col_mapping: dict) -> dict:
    conv_df = load_data(conv_file, col_mapping)
    card_df = load_data(card_file, {'card': 'Карта', 'partner': 'Партнер'})  # справочник карт

    results = []
    for (card, partner), group in conv_df.groupby(['card', 'partner']):
        max_errors, threshold = count_consecutive_errors(group, partner)
        results.append({
            "card": card,
            "partner": partner,
            "max_consecutive_errors": max_errors,
            "threshold": threshold
        })
    report_df = pd.DataFrame(results)

    # Фильтрация проблемных карт по справочнику
    merged = report_df.merge(card_df, on='card', how='left', suffixes=('', '_card'))
    problem_cards = []
    for _, row in merged.iterrows():
        partners_list = normalize_partners_list(row.get('partner_card', ''))
        if row['partner'] in partners_list and row['max_consecutive_errors'] >= row['threshold']:
            problem_cards.append(row)
    problem_cards_df = pd.DataFrame(problem_cards)

    # Summary
    total_cards = conv_df['card'].nunique()
    total_partners = conv_df['partner'].nunique()
    summary = {
        'total_cards': total_cards,
        'total_partners': total_partners,
        'problem_cards': problem_cards_df['card'].nunique(),
        'total_rows': len(conv_df)
    }

    # Data для Excel
    data_sheets = {
        "Data_conv": conv_df,
        "Data_card": card_df,
        "Stat": pd.DataFrame(),
        "Отключить": problem_cards_df[['card', 'partner', 'max_consecutive_errors']]
    }

    return {
        'summary': summary,
        'data_sheets': data_sheets,
        'problem_cards': problem_cards_df,
    }

# -----------------------------
# Обёртка для selector.py
# -----------------------------
def run(conv_file: str, card_file: str, col_mapping: dict) -> dict:
    return analyze_conversion(conv_file, card_file, col_mapping)
