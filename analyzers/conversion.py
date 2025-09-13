# analyzers/conversion.py

import pandas as pd
import re
import os
import yaml
import logging
from utils.logger import logger
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter
from utils.excel_utils import flatten_lists_in_df, style_worksheet, write_df_to_sheet

# -----------------------------
# Загрузка конфигурации
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "config", "conversion_config.yaml"))

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

PARTNERS = CONFIG.get("partners", {})
POOLS = CONFIG.get("pools", {})
COLUMNS = CONFIG.get("columns", {})

# -----------------------------
# Вспомогательные функции
# -----------------------------
def normalize_colname(name: str) -> str:
    return str(name).strip().lower().replace("ё", "е")

def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    # общий базовый слой
    name = name.lower().strip()
    name = name.replace("ё", "е")
    name = name.replace("амобайл", "а-мобайл")
    name = re.sub(r"\(\d+\)$", "", name)  # убираем коды (107) и т.п.
    name = re.sub(r"\s+", " ", name)      # схлопываем пробелы
    # унифицируем "выплаты"
    name = re.sub(r"\+.*", "+выплаты", name)
    return name.strip(", ")


def normalize_partners_list(partners_str: str) -> list:
    partners = str(partners_str).split(',')
    return [normalize_name(p) for p in partners if p.strip()]

def load_data(filepath, col_mapping: dict):
    """Загрузка CSV/XLSX и нормализация колонок"""
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

partners_thresholds = {
    normalize_name(k): v for k, v in CONFIG.get("partners", {}).items()
}

def count_consecutive_errors(group, partner_name: str) -> tuple[int, int]:
    partner_norm = normalize_name(partner_name)
    threshold = partners_thresholds.get(partner_norm, 4)

    count = max_count = 0
    for status in group['status']:
        if status == 'оплачен':
            break
        if status in ['ошибка']:
            count += 1
            max_count = max(max_count, count)
        else:
            count = 0
    return max_count, threshold


def build_stat_sheet(conv_df: pd.DataFrame, wb: Workbook):
    """
    Создаёт лист Stat (широкая таблица карты × партнёры)
    и лист Charts с вертикальным столбчатым графиком по партнёрам.
    """
    logger.info("Формируем лист Stat (широкая таблица)")

    cards = conv_df["card"].unique()
    partners = conv_df["partner_norm"].unique()

    stat_rows = []
    for card in cards:
        row = {"Карта": card}
        for partner in partners:
            mask = (conv_df["card"] == card) & (conv_df["partner_norm"] == partner)
            errors = conv_df.loc[mask & (conv_df["status"] == "ошибка"), "status"].count()
            success = conv_df.loc[mask & (conv_df["status"] == "оплачен"), "status"].count()
            row[f"{partner} Ошибки"] = errors
            row[f"{partner} Успешно"] = success
        stat_rows.append(row)

    stat_df = pd.DataFrame(stat_rows)

    # Лист Stat
    write_df_to_sheet(wb, "Stat", flatten_lists_in_df(stat_df))

    # -----------------------------
    # Лист Charts
    # -----------------------------
    logger.info("Формируем лист Charts (вертикальный график)")
    chart_ws = wb.create_sheet("Charts")

    # Подготовка агрегированных данных
    agg_list = []
    for partner in partners:
        errors = conv_df.loc[conv_df["partner_norm"] == partner].loc[conv_df["status"] == "ошибка", "status"].count()
        success = conv_df.loc[conv_df["partner_norm"] == partner].loc[conv_df["status"] == "оплачен", "status"].count()
        agg_list.append({"partner": partner, "errors": errors, "success": success})
    agg_df = pd.DataFrame(agg_list)

    # Запись данных на лист Charts
    chart_ws.append(["Партнёр", "Оплачен", "Ошибка"])
    for r in agg_df.itertuples(index=False):
        chart_ws.append([r.partner, r.success, r.errors])

    style_worksheet(chart_ws)

    # Создание вертикального столбчатого графика
    max_row = chart_ws.max_row
    chart = BarChart()
    chart.type = "col"
    chart.title = "Ошибки и успехи по партнёрам"
    chart.y_axis.title = "Количество"

    # Зеленый = Оплачен, красный = Ошибка
    for col, fill_color in zip([2, 3], ["00FF00", "FF0000"]):
        # Данные (колонки Оплачен и Ошибка)
        data = Reference(chart_ws, min_col=2, min_row=1, max_col=3, max_row=chart_ws.max_row)
        chart.add_data(data, titles_from_data=True)
        # Задаём цвет заливки (только для визуального различия в openpyxl)
        for cell in chart_ws[get_column_letter(col)]:
            cell.fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")

    # Категории (имена партнёров) снизу
    cats = Reference(chart_ws, min_col=1, min_row=2, max_row=chart_ws.max_row)
    chart.set_categories(cats)
    chart.shape = 4
    # Подписи данных сверху столбцов
    chart.dataLabels = DataLabelList()
    chart.dataLabels.showVal = True

    # Добавляем график на лист
    chart_ws.add_chart(chart, "E2")

    logger.info("Лист Charts сформирован")

# -----------------------------
# Новая утилита
# -----------------------------
def flatten_lists_in_df(df: pd.DataFrame) -> pd.DataFrame:
    """Преобразует списки в строку перед записью в Excel"""
    def _cell_to_str(x):
        if isinstance(x, (list, tuple)):
            return ", ".join(map(str, x))
        if pd.isna(x):
            return ""
        return x
    for col in df.columns:
        df[col] = df[col].apply(_cell_to_str)
    return df

# -----------------------------
# Основной запуск
# -----------------------------
def run(conv_file: str, card_files: list, col_mapping: dict) -> dict:
    conv_df = load_data(conv_file, col_mapping)
    conv_df["partner_norm"] = conv_df["partner"].apply(normalize_name)

    card_df_list = [load_data(f, col_mapping) for f in card_files]
    card_df = pd.concat(card_df_list, ignore_index=True) if card_df_list else pd.DataFrame(columns=["card", "partner"])
    card_df["partner_list"] = card_df["partner"].apply(normalize_partners_list)

    results = []
    problem_cards = []

    VALID_STATUSES = [s.strip().lower() for s in CONFIG.get("valid_statuses", [])]

    for (card, partner_norm), group in conv_df.groupby(["card", "partner_norm"]):
        threshold = partners_thresholds.get(partner_norm, 4)
        if partner_norm not in partners_thresholds:
            logger.warning(f"[NO YAML] Партнёр '{partner_norm}' не найден в YAML. Использован порог {threshold}")

        card_status_raw = card_df.loc[card_df["card"] == card, "status"]
        card_status = (
            card_status_raw.iloc[0].strip().lower()
            if not card_status_raw.empty and pd.notna(card_status_raw.iloc[0])
            else None
        )

        max_errors, _ = count_consecutive_errors(group, partner_norm)
        results.append({
            "card": card,
            "partner": partner_norm,
            "max_consecutive_errors": max_errors,
            "threshold": threshold,
            "status": card_status_raw.iloc[0] if not card_status_raw.empty else None
        })

        partners_list = [
            p for sublist in card_df.loc[card_df["card"] == card, "partner_list"] for p in sublist
        ]
        if (
                partner_norm in partners_list
                and max_errors >= threshold
                and card_status in VALID_STATUSES
        ):
            problem_cards.append({
                "card": card,
                "partner": partner_norm,
                "max_consecutive_errors": max_errors
            })

    problem_cards_df = pd.DataFrame(
        problem_cards,
        columns=["card", "partner", "max_consecutive_errors", "status"]
    )

    valid_results = [r for r in results if pd.notna(r["card"]) and str(r["card"]).strip() != ""]
    if valid_results:
        max_error_record = max(valid_results, key=lambda r: r["max_consecutive_errors"])
        max_errors = max_error_record["max_consecutive_errors"]
        max_error_card = max_error_record["card"]
    else:
        max_errors = 0
        max_error_card = None

    summary = {
        "Карт в работе": conv_df["card"].nunique(),
        "Max ошибки": max_errors,
        "Карта с Max ошибками": max_error_card,
        "Карты на отключение": int(problem_cards_df["card"].nunique()) if not problem_cards_df.empty else 0
    }

    # -----------------------------
    # Workbook
    # -----------------------------
    wb = Workbook()
    wb.remove(wb.active)

    # Data_conv
    safe_conv = flatten_lists_in_df(conv_df.copy())
    ws_conv = wb.create_sheet("Data_conv")
    for r in dataframe_to_rows(safe_conv, index=False, header=True):
        ws_conv.append(r)

    # Data_card (без partner_list)
    safe_card = flatten_lists_in_df(card_df.drop(columns=["partner_list"], errors="ignore").copy())
    ws_card = wb.create_sheet("Data_card")
    for r in dataframe_to_rows(safe_card, index=False, header=True):
        ws_card.append(r)

    # Отключить
    if not problem_cards_df.empty:
        safe_problem = flatten_lists_in_df(problem_cards_df.copy())
        ws_prob = wb.create_sheet("Отключить")
        for r in dataframe_to_rows(safe_problem, index=False, header=True):
            ws_prob.append(r)

    # Stat
    build_stat_sheet(conv_df, wb)

    return {
        "summary": summary,
        "workbook": wb,
        "problem_cards": problem_cards_df
    }