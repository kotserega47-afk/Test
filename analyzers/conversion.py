# analyzers/conversion.py
import pandas as pd
import re
from datetime import datetime

def normalize_colname(name: str) -> str:
    """Нормализует название колонки: lower, убираем пробелы, ё → е"""
    return (
        str(name)
        .strip()
        .lower()
        .replace("ё", "е")
    )

def normalize_partner_name(name: str) -> str:
    return re.sub(r'\s*\(\d+\)$', '', name).strip()

def normalize_partners_list(partners_str: str) -> list:
    partners = str(partners_str).split(',')
    return [normalize_partner_name(p) for p in partners if p.strip()]

def load_data(filepath, col_mapping: dict):
    """
    Загружает CSV/Excel и нормализует колонки.
    col_mapping = {'card': 'Карта', 'status': 'Статус',
                   'datetime': 'Дата/Время создания', 'partner': 'Партнер'}
    """
    if filepath.endswith((".xlsx", ".xls")):
        df = pd.read_excel(filepath)
    else:
        df = pd.read_csv(filepath, sep=None, engine="python", encoding="utf-8")

    # Сопоставляем колонки по нормализованным названиям
    norm_cols = {normalize_colname(c): c for c in df.columns}
    new_cols = {}
    for key, expected_name in col_mapping.items():
        expected_norm = normalize_colname(expected_name)
        if expected_norm not in norm_cols:
            raise ValueError(f"❌ В файле нет колонки '{expected_name}' (ожидали для '{key}')")
        new_cols[norm_cols[expected_norm]] = key

    df.rename(columns=new_cols, inplace=True)

    # Нормализация значений
    if "card" in df:
        df["card"] = df["card"].astype(str).str.strip()
    if "status" in df:
        df["status"] = df["status"].astype(str).str.strip().str.lower()
    if "partner" in df:
        df["partner"] = df["partner"].astype(str).str.strip().str.lower()
    if "datetime" in df:
        df["datetime"] = pd.to_datetime(
            df["datetime"], format="%d.%m.%Y %H:%M:%S", errors="coerce"
        )

    # Убираем пустые строки только по тем колонкам, которые реально есть
    required = [c for c in ["card", "status", "datetime"] if c in df]
    if required:
        df.dropna(subset=required, inplace=True)

    if "datetime" in df:
        df.sort_values("datetime", ascending=False, inplace=True)

    return df

def count_consecutive_errors(group):
    count = max_count = 0
    for status in group['status']:
        if status == 'оплачен':
            break
        if status in ['ошибка', 'ожидает оплаты']:
            count += 1
            max_count = max(max_count, count)
        else:
            count = 0
    return max_count

def analyze_conversion(file_path: str, card_path: str, col_mapping: dict) -> dict:
    """
    Универсальный анализ конверсии по картам и партнерам.
    Возвращает словарь: {'summary': {...}, 'data': pd.DataFrame, 'problem_cards': pd.DataFrame}
    """
    df = load_data(file_path, col_mapping)
    card_df = load_data(card_path, {'card': 'Карта', 'partner': 'Партнер'})  # справочник карт

    grouped = df.groupby(['card', 'partner'])
    results = []
    for (card, partner), group in grouped:
        max_errors = count_consecutive_errors(group)
        results.append({
            'card': card,
            'partner': partner,
            'max_consecutive_errors': max_errors
        })

    report_df = pd.DataFrame(results)
    problem_cards = report_df[report_df['max_consecutive_errors'] >= 4]

    # Фильтрация по справочнику карт
    merged = problem_cards.merge(card_df, on='card', how='left', suffixes=('', '_card'))
    filtered = []
    for _, row in merged.iterrows():
        partners_list = normalize_partners_list(row.get('partner_card', ''))
        partner_name = row['partner']
        if partner_name in [p.lower() for p in partners_list]:
            filtered.append(row)

    if filtered:
        problem_cards_filtered = pd.DataFrame(filtered)
    else:
        problem_cards_filtered = pd.DataFrame(columns=merged.columns)

    # Подготавливаем summary
    total_cards = df['card'].nunique()
    total_partners = df['partner'].nunique()
    total_problem_cards = problem_cards_filtered['card'].nunique()

    summary = {
        'total_cards': total_cards,
        'total_partners': total_partners,
        'problem_cards': total_problem_cards,
        'total_rows': len(df)
    }

    return {'summary': summary, 'data': report_df, 'problem_cards': problem_cards_filtered}

# 🚀 Входная точка для selector.py
def run(file_path: str, columns: dict) -> dict:
    """Обёртка для analyze_conversion, чтобы selector мог вызывать единый интерфейс."""
    return analyze_conversion(file_path, file_path, columns)
