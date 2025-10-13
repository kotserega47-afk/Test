# analyzers/conversion.py

import os
import re
import yaml
import tempfile
import pandas as pd

from openpyxl import Workbook

from utils.logger import logger
from utils.excel_utils import flatten_lists_in_df, write_df_to_sheet
from integrations.telegram_bot import send_message_sync, send_file_sync

# -----------------------------
# Загрузка конфигурации
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "config", "conversion_config.yaml"))

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f) or {}

COLUMNS = CONFIG.get("columns", {})  # ожидаемые имена колонок входного conversion-файла
PARTNER_SETTINGS = {}                # будет заполнено из YAML
VALID_STATUSES = [s.strip().lower() for s in CONFIG.get("valid_statuses", [])]


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
    return name.strip(", ")


def normalize_partners_list(partners_str: str) -> list:
    partners = str(partners_str).split(",")
    return [normalize_name(p) for p in partners if p.strip()]


def load_data(filepath, col_mapping: dict):
    """
    Универсальная загрузка CSV/XLSX и нормализация колонок по mappingу вида:
    {"card": "Карта", "partner": "Партнёр", "status": "Статус", "datetime": "Дата/Время создания", ...}
    """
    if filepath.endswith((".xlsx", ".xls")):
        df = pd.read_excel(filepath, dtype=str)
    else:
        df = pd.read_csv(filepath, sep=None, engine="python", encoding="utf-8", dtype=str)

    logger.info(f"[load_data] Загружен файл {filepath} с колонками: {list(df.columns)}")

    norm_cols = {normalize_colname(c): c for c in df.columns}
    new_cols = {}
    for key, expected_name in col_mapping.items():
        expected_norm = normalize_colname(expected_name)
        if expected_norm not in norm_cols:
            raise ValueError(f"❌ В файле {os.path.basename(filepath)} нет колонки '{expected_name}' (ожидали для '{key}')")
        new_cols[norm_cols[expected_norm]] = key

    df.rename(columns=new_cols, inplace=True)

    # Приведение типов/регистров
    for c in ["card", "status", "partner"]:
        if c in df:
            df[c] = df[c].astype(str).str.strip().str.lower()

    if "datetime" in df:
        # В conversion обычно формат "%d.%m.%Y %H:%M:%S"
        df["datetime"] = pd.to_datetime(df["datetime"], format="%d.%m.%Y %H:%M:%S", errors="coerce")

    required = [c for c in ["card", "status", "datetime"] if c in df.columns]
    if required:
        df.dropna(subset=required, inplace=True)

    if "datetime" in df:
        df.sort_values("datetime", ascending=False, inplace=True)

    return df


def init_partner_settings():
    """
    Возвращает dict нормализованного имени партнёра -> {threshold, exclude: [(start, end), ...]}
    где exclude — интервалы времени, которые исключаются из анализа.
    """
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
            "exclude": exclude_periods,
        }
    return partners


PARTNER_SETTINGS = init_partner_settings()


def _send_problem_cards_to_telegram(problem_df: pd.DataFrame) -> None:
    """
    Отправляет построчно список карт на отключение батчами по ~500 строк.
    Формат строки: "<card> <partner> <max_consecutive_errors>"
    """
    if problem_df.empty:
        return

    cols_ok = all(c in problem_df.columns for c in ["card", "partner", "max_consecutive_errors"])
    if not cols_ok:
        send_message_sync("⚠️ Пропущено формирование списка: отсутствуют нужные колонки.")
        return

    msg_lines = [
        f"{row['card']} {row['partner']} {row['max_consecutive_errors']}"
        for _, row in (
            problem_df[["card", "partner", "max_consecutive_errors"]]
            .dropna()
            .astype(str)
            .drop_duplicates()
            .sort_values(by=["partner", "card"])
            .iterrows()
        )
    ]

    total = len(msg_lines)
    if total == 0:
        return

    BATCH_SIZE = 500
    for i in range(0, total, BATCH_SIZE):
        chunk = msg_lines[i:i + BATCH_SIZE]
        send_message_sync("🚫 Карты на отключение:\n" + "\n".join(chunk))
    logger.info(f"[telegram] Отправлен список {total} карт на отключение.")


# -----------------------------
# Основная функция анализа
# -----------------------------
def run(
    conv_file: str,
    card_files: list,
    col_mapping: dict,
    *,
    generate_excel: bool = True,
    send_telegram: bool = True,
) -> dict:
    """
    Универсальный анализатор conversion-файлов (без БД):
    - Загружает conversion и card файлы
    - Применяет exclude-периоды из YAML
    - Подсчитывает серии ошибок по картам/партнёрам (векторизовано)
    - Сравнивает с порогами из YAML
    - Возвращает problem_cards и summary
    - По желанию формирует Excel и отправляет результаты в Telegram
    """
    logger.info(f"[run] 🚀 Начало анализа: {os.path.basename(conv_file)}")

    # 1) Загрузка conversion-файла
    usecols = list(col_mapping.values())
    try:
        conv_df = pd.read_excel(conv_file, dtype=str, usecols=usecols) if conv_file.endswith((".xlsx", ".xls")) \
            else pd.read_csv(conv_file, dtype=str, usecols=usecols, sep=None, engine="python")
    except ValueError as e:
        logger.warning(f"[run] ⚠️ Не найдены все колонки ({usecols}), читаем доступные: {e}")
        conv_df = pd.read_excel(conv_file, dtype=str) if conv_file.endswith((".xlsx", ".xls")) \
            else pd.read_csv(conv_file, dtype=str, sep=None, engine="python")

    conv_df.rename(columns={v: k for k, v in col_mapping.items()}, inplace=True)

    # Приведение типов/нормализация
    conv_df["status"] = conv_df["status"].astype(str).str.strip().str.lower()
    conv_df["partner_norm"] = conv_df["partner"].apply(normalize_name)
    conv_df["datetime"] = pd.to_datetime(conv_df["datetime"], format="%d.%m.%Y %H:%M:%S", errors="coerce")
    conv_df.dropna(subset=["card", "datetime", "status"], inplace=True)

    # Оставляем только релевантные статусы
    conv_df = conv_df[conv_df["status"].isin(["ошибка", "оплачен"] + VALID_STATUSES)]

    logger.info(f"[run] 📄 Загружено {len(conv_df)} строк из conversion.")

    # 2) Применяем exclude-периоды
    for partner_name, settings in PARTNER_SETTINGS.items():
        for start, end in settings.get("exclude", []):
            before = len(conv_df)
            mask = (conv_df["partner_norm"] == partner_name) & (conv_df["datetime"].between(start, end))
            conv_df = conv_df[~mask]
            if len(conv_df) != before:
                logger.info(f"[run] ⏳ Исключено {before - len(conv_df)} строк по exclude для «{partner_name}»")

    # 3) Загрузка card-файлов
    card_df_list = []
    for f in card_files or []:
        try:
            # В card-файле ожидаем минимум: "Карта", "Партнёр", "Статус"
            card_df_list.append(load_data(f, {"card": "Карта", "partner": "Партнёр", "status": "Статус"}))
        except Exception as e:
            logger.warning(f"[run] ⚠️ Пропускаю card-файл {os.path.basename(f)}: {e}")

    card_df = pd.concat(card_df_list, ignore_index=True) if card_df_list else pd.DataFrame(
        columns=["card", "partner", "status"]
    )
    if not card_df.empty:
        card_df["partner_list"] = card_df["partner"].apply(normalize_partners_list)
        card_df["status"] = card_df["status"].astype(str).str.strip().str.lower()
    else:
        card_df["partner_list"] = []

    logger.info(f"[run] 🧩 Загружено {len(card_df)} карт из card-файлов.")

    # 4) Подсчёт серий ошибок (векторизовано)
    # Сортировка важна для корректного расчёта последовательностей
    conv_df.sort_values(["card", "partner_norm", "datetime"], inplace=True)
    # Меняем группу каждый раз, когда статус не "ошибка"
    conv_df["err_block"] = (conv_df["status"] != "ошибка").cumsum()
    conv_df["series_len"] = conv_df.groupby(["card", "partner_norm", "err_block"])["status"].transform(
        lambda s: len(s) if s.iloc[0] == "ошибка" else 0
    )
    max_errors = (
        conv_df.groupby(["card", "partner_norm"])["series_len"]
        .max()
        .reset_index(name="max_consecutive_errors")
    )

    # 5) Порог из YAML
    settings_df = pd.DataFrame(
        [{"partner_norm": p, "threshold": s.get("threshold", 4)} for p, s in PARTNER_SETTINGS.items()]
    )
    merged = max_errors.merge(settings_df, on="partner_norm", how="left").fillna({"threshold": 4})

    # 6) Добавляем статус и партнёров из card-файлов
    card_status_map = card_df.set_index("card")["status"].to_dict() if not card_df.empty else {}
    card_partners_map = card_df.set_index("card")["partner_list"].to_dict() if not card_df.empty else {}

    merged["status"] = merged["card"].map(card_status_map).astype(str).str.strip().str.lower()
    merged["partner_list"] = merged["card"].map(card_partners_map)

    # 7) Фильтрация проблемных карт
    problem_mask = (
        (merged["max_consecutive_errors"] >= merged["threshold"])
        & (merged["status"].isin(VALID_STATUSES))
        & merged.apply(
            lambda r: isinstance(r["partner_list"], list) and r["partner_norm"] in r["partner_list"],
            axis=1
        )
    )
    problem = merged.loc[problem_mask].copy()
    problem.rename(columns={"partner_norm": "partner"}, inplace=True)

    # 8) Итоговый отчёт и Telegram
    summary = {
        "Карт в работе": conv_df["card"].nunique(),
        "Max ошибки": int(merged["max_consecutive_errors"].max()) if not merged.empty else 0,
        "Карты на отключение": int(problem["card"].nunique() if not problem.empty else 0),
    }

    logger.info(f"[run] ✅ Обнаружено {summary['Карты на отключение']} карт на отключение.")

    # Excel отчёт
    wb = None
    report_path = None
    if generate_excel:
        wb = Workbook()
        wb.remove(wb.active)
        # Данные
        write_df_to_sheet(wb, "Data_conv", flatten_lists_in_df(conv_df.copy()))
        write_df_to_sheet(
            wb,
            "Data_card",
            flatten_lists_in_df(card_df.drop(columns=["partner_list"], errors="ignore").copy())
            if not card_df.empty else card_df,
        )
        # Проблемные (если есть)
        if not problem.empty:
            write_df_to_sheet(
                wb,
                "Отключить",
                flatten_lists_in_df(problem.sort_values(by=["partner", "card"]).copy())
            )

        # Сохраняем во временный файл
        tmp_dir = tempfile.gettempdir()
        base_name = f"report_{os.path.basename(conv_file)}"
        # Гарантируем расширение .xlsx
        if not base_name.lower().endswith(".xlsx"):
            base_name += ".xlsx"
        report_path = os.path.join(tmp_dir, base_name)
        wb.save(report_path)
        logger.info(f"[run] 📁 Отчёт сохранён: {report_path}")

    # Telegram отправки
    if send_telegram:
        # список карт
        if not problem.empty:
            _send_problem_cards_to_telegram(problem)
        else:
            send_message_sync("ℹ️ Нет карт, превысивших порог ошибок.")

        # summary
        try:
            summary_text = (
                f"✅ Анализ *{os.path.basename(conv_file)}* завершён.\n"
                f"Карт в работе: {summary.get('Карт в работе', '—')}\n"
                f"На отключение: {summary.get('Карты на отключение', '—')}"
            )
            send_message_sync(summary_text)
        except Exception as e:
            logger.exception(f"[run] Ошибка при отправке Telegram summary: {e}")

        # файл
        if generate_excel and report_path and os.path.exists(report_path):
            try:
                send_file_sync(report_path, caption=f"📊 Отчёт по {os.path.basename(conv_file)}")
                logger.info(f"[run] Файл отчёта отправлен в Telegram: {report_path}")
            except Exception as e:
                logger.exception(f"[run] Ошибка при отправке отчёта в Telegram: {e}")

    # Возвращаем результат
    return {
        "summary": summary,
        "workbook": wb,              # может быть None, если generate_excel=False
        "problem_cards": problem,    # DataFrame
        "report_path": report_path,  # путь к файлу, если generate_excel=True
    }
