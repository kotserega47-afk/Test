# analyzers/conversion.py

import pandas as pd
import re
import os
import yaml
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
    name = name.lower().strip()
    name = name.replace("ё", "е")
    name = name.replace("амобайл", "а-мобайл")
    name = re.sub(r"\(\d+\)$", "", name)
    name = re.sub(r"\s+", " ", name)
    # name = re.sub(r"\+.*", "+Выплаты)", name)
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


def get_partner_settings():
    """Возвращает dict c нормализованным ключом -> {threshold, start}"""
    partners = {}
    for raw_name, settings in CONFIG.get("partners", {}).items():
        norm_name = normalize_name(raw_name)
        partners[norm_name] = {
            "threshold": settings.get("threshold", 4),
            "start": settings.get("start")
        }
    return partners


PARTNER_SETTINGS = get_partner_settings()


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


def build_stat_sheet(conv_df: pd.DataFrame, wb: Workbook):
    logger.info("Формируем лист Stat (широкая таблица)")

    # Считаем количество ошибок и успехов сразу через groupby
    agg = (
        conv_df.groupby(["card", "partner_norm", "status"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )

    # Переименуем колонки для читаемости
    stat_df = agg.rename(columns={
        "ошибка": "Ошибки",
        "оплачен": "Успешно"
    })

    # Раскладываем по партнёрам (широкая форма)
    stat_wide = stat_df.pivot(index="card", columns="partner_norm", values=["Ошибки", "Успешно"])
    stat_wide.columns = [f"{partner} {col}" for col, partner in stat_wide.columns]
    stat_wide.reset_index(inplace=True)
    stat_wide.rename(columns={"card": "Карта"}, inplace=True)

    write_df_to_sheet(wb, "Stat", flatten_lists_in_df(stat_wide))

    # Charts
    logger.info("Формируем лист Charts")
    chart_ws = wb.create_sheet("Charts")

    agg_partners = (
        conv_df.groupby(["partner_norm", "status"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
        .rename(columns={"ошибка": "Ошибка", "оплачен": "Оплачен"})
    )

    chart_ws.append(["Партнёр", "Оплачен", "Ошибка"])
    for r in agg_partners.itertuples(index=False):
        chart_ws.append([r.partner_norm, r.Оплачен, r.Ошибка])

    style_worksheet(chart_ws)

    chart = BarChart()
    chart.type = "col"
    chart.title = "Ошибки и успехи по партнёрам"
    chart.y_axis.title = "Количество"

    data = Reference(chart_ws, min_col=2, min_row=1, max_col=3, max_row=chart_ws.max_row)
    chart.add_data(data, titles_from_data=True)

    cats = Reference(chart_ws, min_col=1, min_row=2, max_row=chart_ws.max_row)
    chart.set_categories(cats)
    chart.shape = 4
    chart.dataLabels = DataLabelList()
    chart.dataLabels.showVal = True

    chart_ws.add_chart(chart, "E2")


def run(conv_file: str, card_files: list, col_mapping: dict) -> dict:
    conv_df = load_data(conv_file, col_mapping)
    conv_df["partner_norm"] = conv_df["partner"].apply(normalize_name)

    # Загрузка карт
    card_df_list = [
        load_data(f, {"card": "Карта", "partner": "Партнер", "status": "Статус"})
        for f in card_files
    ]
    card_df = (
        pd.concat(card_df_list, ignore_index=True)
        if card_df_list
        else pd.DataFrame(columns=["card", "partner"])
    )
    card_df["partner_list"] = card_df["partner"].apply(normalize_partners_list)

    results, problem_cards = [], []
    VALID_STATUSES = [s.strip().lower() for s in CONFIG.get("valid_statuses", [])]

    for (card, partner_norm), group in conv_df.groupby(["card", "partner_norm"]):
        settings = PARTNER_SETTINGS.get(partner_norm, {"threshold": 4})
        threshold = settings.get("threshold", 4)

        # Исключаем интервалы, если заданы
        excludes = settings.get("exclude", [])
        if excludes:
            total_before = len(group)
            for interval in excludes:
                try:
                    start_ex = pd.to_datetime(
                        interval.get("start"), format="%d.%m.%Y %H:%M:%S", errors="coerce"
                    )
                    end_ex = pd.to_datetime(
                        interval.get("end"), format="%d.%m.%Y %H:%M:%S", errors="coerce"
                    )
                except Exception:
                    start_ex, end_ex = pd.NaT, pd.NaT

                if pd.notna(start_ex) and pd.notna(end_ex):
                    before_len = len(group)
                    group = group[
                        ~((group["datetime"] >= start_ex) & (group["datetime"] <= end_ex))
                    ]
                    logger.info(
                        f"[{partner_norm}] исключён интервал {start_ex} – {end_ex}, "
                        f"записей {before_len} → {len(group)}"
                    )
            logger.info(
                f"[{partner_norm}] после всех исключений записей осталось {len(group)} "
                f"(из {total_before}) для карты {card}"
            )

        card_status_raw = card_df.loc[card_df["card"] == card, "status"]
        card_status = (
            card_status_raw.iloc[0].strip().lower()
            if not card_status_raw.empty and pd.notna(card_status_raw.iloc[0])
            else None
        )

        max_errors = count_consecutive_errors(group, partner_norm)
        results.append(
            {
                "card": card,
                "partner": partner_norm,
                "max_consecutive_errors": max_errors,
                "threshold": threshold,
                "status": card_status_raw.iloc[0]
                if not card_status_raw.empty
                else None,
            }
        )

        partners_list = [
            p
            for sublist in card_df.loc[card_df["card"] == card, "partner_list"]
            for p in sublist
        ]
        if (
            partner_norm in partners_list
            and max_errors >= threshold
            and card_status in VALID_STATUSES
        ):
            problem_cards.append(
                {
                    "card": card,
                    "partner": partner_norm,
                    "max_consecutive_errors": max_errors,
                    "status": card_status,
                }
            )

    problem_cards_df = pd.DataFrame(problem_cards)

    valid_results = [
        r
        for r in results
        if pd.notna(r["card"]) and str(r["card"]).strip() != ""
    ]
    if valid_results:
        max_error_record = max(
            valid_results, key=lambda r: r["max_consecutive_errors"]
        )
        max_errors, max_error_card = (
            max_error_record["max_consecutive_errors"],
            max_error_record["card"],
        )
    else:
        max_errors, max_error_card = 0, None

    summary = {
        "Карт в работе": conv_df["card"].nunique(),
        "Max ошибки": max_errors,
        "Карта с Max ошибками": max_error_card,
        "Карты на отключение": int(problem_cards_df["card"].nunique())
        if not problem_cards_df.empty
        else 0,
    }

    wb = Workbook()
    wb.remove(wb.active)

    # Sheets
    write_df_to_sheet(wb, "Data_conv", flatten_lists_in_df(conv_df.copy()))
    write_df_to_sheet(
        wb,
        "Data_card",
        flatten_lists_in_df(
            card_df.drop(columns=["partner_list"], errors="ignore").copy()
        ),
    )

    if not problem_cards_df.empty:
        safe_problem = flatten_lists_in_df(
            problem_cards_df.sort_values(by=["partner", "card"]).copy()
        )
        write_df_to_sheet(wb, "Отключить", safe_problem)

    return {
        "summary": summary,
        "workbook": wb,
        "problem_cards": problem_cards_df,
    }

