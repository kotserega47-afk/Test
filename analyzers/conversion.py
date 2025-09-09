# analyzers/conversion.py
import pandas as pd
import re
from utils.report_builder import build_report
from utils.logger import logger
from utils.data_loader import load_data, normalize_partners_list, count_consecutive_errors
from integrations.telegram_bot import send_message_sync

CONFIG_PATH = "config/conversion_config.yaml"

import yaml
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)


def analyze_conversion(conv_file: str, card_files: list[str], col_mapping: dict) -> dict:
    # --------------------------
    # Загружаем данные
    # --------------------------
    conv_df = load_data(conv_file, col_mapping)

    card_dfs = [load_data(f, {"card": "Карта", "partner": "Партнер"}) for f in card_files]
    card_df = pd.concat(card_dfs, ignore_index=True)

    # --------------------------
    # Подсчёт max consecutive errors
    # --------------------------
    results = []
    for (card, partner), group in conv_df.groupby(['card', 'partner']):
        max_errors, threshold = count_consecutive_errors(group, partner)
        results.append({
            "card": card,
            "partner": partner,
            "max_consecutive_errors": max_errors,
            "threshold": threshold
        })
    errors_df = pd.DataFrame(results)

    # --------------------------
    # Проблемные карты для Отключить
    # --------------------------
    problem_cards = []
    for _, row in errors_df.iterrows():
        partners_list = normalize_partners_list(card_df.query("card == @row['card']")['partner'].sum())
        if row['partner'] in partners_list and row['max_consecutive_errors'] >= row['threshold']:
            problem_cards.append(row)
    problem_cards_df = pd.DataFrame(problem_cards)

    # --------------------------
    # Summary
    # --------------------------
    summary = {}
    summary['Карт в работе'] = conv_df['card'].nunique()

    pools = CONFIG.get("pools", {})
    for pool_name, pool_label in pools.items():
        conv_pool_cards = conv_df.query("partner.str.contains(@pool_label, case=False, na=False)")['card'].nunique()
        card_pool_cards = card_df.query("partner.str.contains(@pool_label, case=False, na=False)")['card'].nunique()
        summary[f"Объем {pool_label}"] = conv_pool_cards / card_pool_cards if card_pool_cards else 0

    summary['Max ошибки'] = errors_df['max_consecutive_errors'].max() if not errors_df.empty else 0
    summary['Карты на отключение'] = problem_cards_df['card'].nunique() if not problem_cards_df.empty else 0

    # --------------------------
    # Подготовка листа Stat
    # --------------------------
    stat_rows = []
    for card in conv_df['card'].unique():
        row = {"Карты": card}
        for bank in ["Сбер", "Тинь"]:
            errs = conv_df.query("card==@card and partner.str.contains(@bank, case=False, na=False) and status=='ошибка'")['status'].count()
            success = conv_df.query("card==@card and partner.str.contains(@bank, case=False, na=False) and status=='оплачен'")['status'].count()
            row[f"{bank} Ошибки"] = errs
            row[f"{bank} Успешно"] = success
        stat_rows.append(row)
    stat_df = pd.DataFrame(stat_rows)

    # --------------------------
    # Подготовка data_sheets
    # --------------------------
    data_sheets = {
        "Data_conv": conv_df,
        "Data_card": card_df,
        "Stat": stat_df,
        "Отключить": problem_cards_df[['card', 'partner', 'max_consecutive_errors']] if not problem_cards_df.empty else pd.DataFrame()
    }

    return {
        'summary': summary,
        'data_sheets': data_sheets,
        'problem_cards': problem_cards_df,
    }


def run(conv_file: str, card_files: list[str], columns: dict) -> dict:
    return analyze_conversion(conv_file, card_files, columns)
