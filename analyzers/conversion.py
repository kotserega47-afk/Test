# analyzers/conversion.py
import os
import re
import pandas as pd
from datetime import datetime
import yaml

# Путь к конфигу YAML
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "conversion_config.yaml")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

def normalize_colname(name: str) -> str:
    """Нормализует название колонки: lower, убираем пробелы, ё → е"""
    return str(name).strip().lower().replace("ё", "е")

def normalize_partner_name(name: str) -> str:
    """Удаляет (число) в конце и лишние пробелы"""
    return re.sub(r'\s*\(\d+\)$', '', str(name)).strip()

def normalize_partners_list(partners_str: str) -> list:
    """Разделяет список партнеров через запятую и нормализует"""
    partners = str(partners_str).split(',')
    return [normalize_partner_name(p).lower() for p in partners if p.strip()]

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

def count_consecutive_errors(group, partner_name: str) -> int:
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

def analyze_conversion(conv_file: str, card_file: str, col_mapping: dict) -> dict:
    """Основной анализ"""
    conv_df = load_data(conv_file, col_mapping)
    card_df = load_data(card_file, {'card': 'Карта', 'partner': 'Партнер'})  # справочник карт

    # Считаем ошибки подряд
    results = []
    for (card, partner), group in conv_df.groupby(['card', 'partner']):
        max_errors, threshold = count_consecutive_errors(group, partner)
        results.append({"card": card, "partner": partner, "max_consecutive_errors": max_errors, "threshold": threshold})
    report_df = pd.DataFrame(results)

    # Фильтрация карт на отключение (достигли порога и есть в Card)
    merged = report_df.merge(card_df, on='card', how='left', suffixes=('', '_card'))
    problem_cards = []
    for _, row in merged.iterrows():
        partners_list = normalize_partners_list(row.get('partner_card', ''))
        if row['partner'] in partners_list and row['max_consecutive_errors'] >= row['threshold']:
            problem_cards.append({
                'card': row['card'],
                'partner': row['partner'],
                'max_consecutive_errors': row['max_consecutive_errors']
            })

    # Гарантируем наличие колонок, даже если пусто
    problem_cards_df = pd.DataFrame(problem_cards)
    if problem_cards_df.empty:
        problem_cards_df = pd.DataFrame(columns=['card', 'partner', 'max_consecutive_errors'])

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
        "Stat": pd.DataFrame(),  # сюда позже добавим графики
        "Отключить": problem_cards_df
    }

    return {
        'summary': summary,
        'data_sheets': data_sheets,
        'problem_cards': problem_cards_df,
    }

def run(conv_file: str, card_file: str, col_mapping: dict) -> dict:
    """Обёртка для selector.py"""
    return analyze_conversion(conv_file, card_file, col_mapping)