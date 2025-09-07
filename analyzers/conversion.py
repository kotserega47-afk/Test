# analyzers/conversion.py
import pandas as pd
import re
from datetime import datetime


def normalize_partner_name(name: str) -> str:
    return re.sub(r'\s*\(\d+\)$', '', name).strip()


def normalize_partners_list(partners_str: str) -> list:
    partners = partners_str.split(',')
    return [normalize_partner_name(p) for p in partners if p.strip()]


def load_data(filepath, col_mapping: dict):
    """
    Загружает CSV/Excel и нормализует колонки.
    col_mapping = {'card': 'Карта', 'status': 'Статус',
                   'datetime': 'Дата/Время создания', 'partner': 'Партнёр'}
    """
    if filepath.endswith((".xlsx", ".xls")):
        df = pd.read_excel(filepath, dtype={col_mapping['card']: str})
    else:
        df = pd.read_csv(filepath, sep=None, engine='python', encoding='utf-8')

    # Переименовываем колонки под стандарт
    df.rename(columns={v: k for k, v in col_mapping.items() if v in df.columns}, inplace=True)
    df['card'] = df['card'].astype(str).str.strip()
    df['datetime'] = pd.to_datetime(df['datetime'], errors='coerce')
    df['status'] = df['status'].str.strip().str.lower()
    df['partner'] = df['partner'].str.strip().str.lower()
    df.dropna(subset=['card', 'status', 'datetime'], inplace=True)
    df.sort_values('datetime', ascending=False, inplace=True)
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
    Универсальный анализ конверсии по картам и партнёрам.
    Возвращает словарь: {'summary': {...}, 'data': pd.DataFrame, 'problem_cards': pd.DataFrame}
    """
    df = load_data(file_path, col_mapping)
    card_df = load_data(card_path, {'card': 'Карта', 'partner': 'Партнёр'})  # только нужные колонки

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

    # Фильтрация по файлу card
    merged = problem_cards.merge(card_df, on='card', how='left', suffixes=('', '_card'))
    filtered = []
    for _, row in merged.iterrows():
        partners_list = normalize_partners_list(row.get('partner_card', ''))
        partner_name = row['partner']
        if partner_name in [p.lower() for p in partners_list]:
            filtered.append(row)
    problem_cards_filtered = pd.DataFrame(filtered)

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
    """
    Обёртка для analyze_conversion, чтобы selector мог вызывать единый интерфейс.
    """
    # Если у тебя есть отдельный card_path (например, словарь карт),
    # можно добавить его через env или yaml. Пока используем только file_path.
    return analyze_conversion(file_path, file_path, columns)
