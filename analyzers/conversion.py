# analyzers/conversion.py
import os
import re
import yaml
import tempfile
import pandas as pd
import gc
import psutil

from openpyxl import Workbook
from datetime import datetime
from utils.logger import logger
from utils.excel_utils import flatten_lists_in_df, write_df_to_sheet
from integrations.telegram_bot import send_message_sync, send_file_sync
from integrations.dropbox_watcher import download_file

# -----------------------------
# Загрузка конфигурации
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "config", "conversion_config.yaml"))

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f) or {}

COLUMNS = CONFIG.get("columns", {})
VALID_STATUSES = [s.strip().lower() for s in CONFIG.get("valid_statuses", [])]
POOLS = CONFIG.get("pools", {})


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


def log_memory(step: str):
    """Логирование использования памяти"""
    process = psutil.Process(os.getpid())
    mb = process.memory_info().rss / 1024 / 1024
    logger.info(f"🧠 {step}: {mb:.1f} MB")


def load_data_optimized(filepath, col_mapping: dict):
    """Оптимизированная загрузка данных с экономией памяти"""
    log_memory(f"До загрузки {os.path.basename(filepath)}")

    # Оптимизированные типы данных
    dtype_optimized = {
        'card': 'string',
        'status': 'category',
        'partner': 'string',
        'datetime': 'string'
    }

    # Читаем ВСЕ колонки без usecols
    try:
        if filepath.endswith((".xlsx", ".xls")):
            df = pd.read_excel(filepath, dtype=dtype_optimized)
        else:
            df = pd.read_csv(filepath, dtype=dtype_optimized,
                             sep=None, engine="python", encoding="utf-8")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки файла {filepath}: {e}")
        raise

    logger.info(f"[load_data] Загружен файл {filepath} с колонками: {list(df.columns)}")

    # Нормализация колонок
    norm_cols = {normalize_colname(c): c for c in df.columns}
    new_cols = {}
    for key, expected_name in col_mapping.items():
        expected_norm = normalize_colname(expected_name)
        if expected_norm not in norm_cols:
            raise ValueError(f"❌ В файле {os.path.basename(filepath)} нет колонки '{expected_name}'")
        new_cols[norm_cols[expected_norm]] = key

    df.rename(columns=new_cols, inplace=True)

    # Приведение типов
    for c in ["card", "status", "partner"]:
        if c in df:
            df[c] = df[c].astype(str).str.strip().str.lower()

    if "datetime" in df:
        df["datetime"] = pd.to_datetime(df["datetime"], format="%d.%m.%Y %H:%M:%S", errors="coerce")

    # Фильтрация БЕЗ создания копий
    required = [c for c in ["card", "status", "datetime"] if c in df.columns]
    if required:
        mask = df[required].notna().all(axis=1)
        df = df[mask]

    if "datetime" in df:
        df.sort_values("datetime", ascending=False, inplace=True)

    log_memory(f"После загрузки {os.path.basename(filepath)}")
    return df


def init_partner_settings():
    """Инициализация настроек партнеров"""
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
    """Отправка проблемных карт в Telegram"""
    if problem_df.empty:
        return

    cols_ok = all(c in problem_df.columns for c in ["card", "partner", "max_consecutive_errors"])
    if not cols_ok:
        send_message_sync("⚠️ Пропущено формирование списка: отсутствуют нужные колонки.")
        return

    # Обработка батчами для экономии памяти
    BATCH_SIZE = 500
    total_cards = len(problem_df)

    for i in range(0, total_cards, BATCH_SIZE):
        batch = problem_df.iloc[i:i + BATCH_SIZE]
        msg_lines = [
            f"{row['card']} {row['partner']} {row['max_consecutive_errors']}"
            for _, row in batch.iterrows()
        ]
        send_message_sync("🚫 Карты на отключение:\n" + "\n".join(msg_lines))

    logger.info(f"[telegram] Отправлен список {total_cards} карт на отключение.")


def count_last_error_streak(df: pd.DataFrame) -> pd.DataFrame:
    """Подсчет серий ошибок с оптимизацией памяти"""
    if df.empty:
        return pd.DataFrame(columns=["card", "partner_norm", "max_consecutive_errors"])

    # Обработка группами для экономии памяти
    results = []
    unique_pairs = df[["card", "partner_norm"]].drop_duplicates()

    for _, (card, partner) in unique_pairs.iterrows():
        group = df[(df["card"] == card) & (df["partner_norm"] == partner)]
        statuses = group.sort_values("datetime", ascending=False)["status"].tolist()

        streak = 0
        for s in statuses:
            if s == "ошибка":
                streak += 1
            elif s == "оплачен":
                break
            else:
                break

        results.append({"card": card, "partner_norm": partner, "max_consecutive_errors": streak})

    return pd.DataFrame(results)


# -----------------------------
# ОПТИМИЗИРОВАННАЯ основная функция
# -----------------------------
def run(
        conv_file: str,
        card_files: list,
        col_mapping: dict,
        *,
        generate_excel: bool = True,
        send_telegram: bool = True,
) -> dict:
    """Оптимизированная основная функция анализа"""
    logger.info(f"[run] 🚀 Начало анализа: {os.path.basename(conv_file)}")
    log_memory("Начало анализа")

    # 0) Пути к special_cards.xlsx
    special_folder = os.getenv("DROPBOX_SPECIAL_PATH", "/Ostin/platform/special")
    dropbox_special_file = os.path.join(special_folder, "special_cards.xlsx")
    local_special_path = os.path.join(tempfile.gettempdir(), "special_cards.xlsx")

    # 1) Загрузка special_cards с оптимизацией
    special_rules = {}
    latest_special_date = None
    special_loaded = False

    try:
        if download_file(dropbox_special_file, local_special_path):
            log_memory("После загрузки special_cards")

            # Оптимизированная загрузка special_cards
            dtype_special = {'Карта': 'string', 'Партнер': 'string', 'Дата': 'string'}
            df_special = pd.read_excel(local_special_path, dtype=dtype_special)

            df_special.rename(columns={"Карта": "card", "Партнер": "partner", "Дата": "start_date"}, inplace=True)

            missing_cols = [c for c in ["card", "partner", "start_date"] if c not in df_special.columns]
            if not missing_cols:
                df_special["card"] = df_special["card"].astype(str).str.strip()
                df_special["partner_norm"] = df_special["partner"].apply(normalize_name)
                df_special["start_date"] = pd.to_datetime(
                    df_special["start_date"].astype(str).str.strip(), dayfirst=True, errors="coerce"
                )

                df_special.sort_values("start_date", ascending=False, inplace=True, na_position="last")
                df_special.drop_duplicates(subset=["card", "partner_norm"], keep="first", inplace=True)

                special_rules = df_special.set_index(["card", "partner_norm"])["start_date"].to_dict()
                latest_special_date = df_special["start_date"].max()
                special_loaded = True

            # Очистка памяти
            del df_special
            gc.collect()
            log_memory("После обработки special_cards")

    except Exception as e:
        logger.warning(f"[run] ⚠️ Ошибка при загрузке special_cards.xlsx: {e}")

    # 2) Оптимизированная загрузка conversion-файла
    log_memory("До загрузки conversion")

    # ЯВНО указываем ВСЕ нужные колонки для conversion-файла
    conversion_col_mapping = {
        "card": "Карта",
        "status": "Статус",
        "partner": "Партнёр",
        "datetime": "Дата/Время создания"
    }

    conv_df = load_data_optimized(conv_file, conversion_col_mapping)
    log_memory("После загрузки conversion")

    # Добавляем нормализованное имя партнера
    conv_df["partner_norm"] = conv_df["partner"].apply(normalize_name)

    # 3) Применяем правила special_cards БЕЗ создания копий
    if special_rules:
        before = len(conv_df)
        # Векторная фильтрация без merge (экономит память)
        mask_keep = pd.Series(True, index=conv_df.index)

        for (card, partner), start_date in special_rules.items():
            card_partner_mask = (conv_df["card"] == card) & (conv_df["partner_norm"] == partner)
            date_mask = conv_df["datetime"] >= start_date
            mask_keep &= ~(card_partner_mask & ~date_mask)

        conv_df = conv_df[mask_keep]
        after = len(conv_df)
        if after != before:
            logger.info(f"[run] 🧭 Применены правила special_cards: отфильтровано {before - after} строк.")

    # 4) Применяем exclude-периоды БЕЗ создания копий
    for partner_name, settings in PARTNER_SETTINGS.items():
        for start, end in settings.get("exclude", []):
            before = len(conv_df)
            mask = (conv_df["partner_norm"] == partner_name) & (conv_df["datetime"].between(start, end))
            conv_df = conv_df[~mask]
            if len(conv_df) != before:
                logger.info(f"[run] ⏳ Исключено {before - len(conv_df)} строк по exclude для «{partner_name}»")

    # 5) Оптимизированная загрузка card-файлов
    log_memory("До загрузки card files")
    card_df_list = []
    for f in card_files or []:
        try:
            card_df = load_data_optimized(f, {"card": "Карта", "partner": "Партнёр", "status": "Статус", "pool": "Пул"})
            card_df_list.append(card_df)
        except Exception as e:
            logger.warning(f"[run] ⚠️ Пропускаю card-файл {os.path.basename(f)}: {e}")

    card_df = pd.concat(card_df_list, ignore_index=True) if card_df_list else pd.DataFrame()
    if not card_df.empty:
        card_df["partner_list"] = card_df["partner"].apply(normalize_partners_list)
        card_df["status"] = card_df["status"].astype(str).str.strip().str.lower()
    log_memory("После загрузки card files")

    # 6) Подсчёт текущих серий ошибок
    log_memory("До подсчета серий ошибок")
    max_errors = count_last_error_streak(conv_df)
    log_memory("После подсчета серий ошибок")

    # 7) Пороги из YAML
    settings_df = pd.DataFrame(
        [{"partner_norm": p, "threshold": s.get("threshold", 4)} for p, s in PARTNER_SETTINGS.items()]
    )
    merged = max_errors.merge(settings_df, on="partner_norm", how="left").fillna({"threshold": 4})

    # 8) Добавляем статус и партнёров из card-файлов
    card_status_map = card_df.set_index("card")["status"].to_dict() if not card_df.empty else {}
    card_partners_map = card_df.set_index("card")["partner_list"].to_dict() if not card_df.empty else {}

    merged["status"] = merged["card"].map(card_status_map).astype(str).str.strip().str.lower()
    merged["partner_list"] = merged["card"].map(card_partners_map)

    # 9) Фильтрация проблемных карт
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

    # 10) Подсчёт "Карт в работе по партнёрам"
    ACTIVE_STATUSES = VALID_STATUSES

    active_cards = card_df[
        card_df["status"].isin(ACTIVE_STATUSES)
        & card_df["partner"].notna()
        & (card_df["partner"].str.strip() != "")
        ].copy()

    # Исключаем карты, которые попали в problem (на отключение)
    if not problem.empty:
        active_cards = active_cards[~active_cards["card"].isin(problem["card"])]

    # Разворачиваем многозначных партнёров
    def split_partners(row):
        parts = [p.strip() for p in str(row["partner"]).split(",") if p.strip()]
        return [(p, row["card"]) for p in parts]

    pairs = active_cards.apply(split_partners, axis=1).explode()
    pairs = pairs.dropna()
    pairs = pairs.apply(pd.Series)
    pairs.columns = ["partner_display", "card"]

    # Считаем количество уникальных карт по партнёрам
    cards_in_work_by_partner = (
        pairs.drop_duplicates(subset=["partner_display", "card"])
        .groupby("partner_display")["card"]
        .nunique()
        .sort_values(ascending=False)
    )

    # Карт в работе по пулам
    if "pool" in card_df.columns:
        active_pools = card_df[
            card_df["status"].isin(ACTIVE_STATUSES)
            & card_df["pool"].notna()
            & (card_df["pool"].str.strip() != "")
            ].copy()

        if not problem.empty:
            active_pools = active_pools[~active_pools["card"].isin(problem["card"])]

        cards_in_work_by_pool = (
            active_pools.drop_duplicates(subset=["pool", "card"])
            .groupby("pool")["card"]
            .nunique()
            .sort_values(ascending=False)
        )
    else:
        cards_in_work_by_pool = pd.Series(dtype=int)

    # Формируем summary
    summary = {
        "Карт в работе по партнёрам": cards_in_work_by_partner.to_dict(),
        "Карт в работе по пулам": cards_in_work_by_pool.to_dict(),
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

        # Проблемные карты
        if not problem.empty:
            write_df_to_sheet(
                wb,
                "Отключить",
                flatten_lists_in_df(problem.sort_values(by=["partner", "card"]).copy())
            )

        # Карт в работе по партнёрам
        if not cards_in_work_by_partner.empty:
            write_df_to_sheet(
                wb,
                "Карт в работе",
                cards_in_work_by_partner.reset_index().rename(
                    columns={"partner_display": "Партнёр", "card": "Карт в работе"}
                )
            )

        # Карт в работе по пулам
        if not cards_in_work_by_pool.empty:
            write_df_to_sheet(
                wb,
                "Карт в работе (Пулы)",
                cards_in_work_by_pool.reset_index().rename(
                    columns={"pool": "Пул", "card": "Карт в работе"}
                )
            )

        # Сохраняем отчёт
        tmp_dir = tempfile.gettempdir()
        current_date = datetime.now().strftime("%d.%m.%Y")
        base_name = f"report_{os.path.splitext(os.path.basename(conv_file))[0]}_({current_date}).xlsx"
        report_path = os.path.join(tempfile.gettempdir(), base_name)
        logger.info(f"[run] Листы отчёта: {wb.sheetnames}")
        wb.save(report_path)
        logger.info(f"[run] 📁 Отчёт сохранён: {report_path}")

    # Telegram отправки
    if send_telegram:
        if not problem.empty:
            _send_problem_cards_to_telegram(problem)
        else:
            send_message_sync("ℹ️ Нет карт, превысивших порог ошибок.")

        # summary
        try:
            msg_lines = [f"• {p}: {n}" for p, n in cards_in_work_by_partner.items()]
            summary_text = (
                    f"✅ Анализ *{os.path.basename(conv_file)}* завершён.\n"
                    f"Карт в работе по партнёрам:\n" + "\n".join(msg_lines) + "\n"
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

    # Очистка памяти
    gc.collect()
    log_memory("Конец анализа")

    return {
        "summary": summary,
        "workbook": wb,
        "problem_cards": problem,
        "report_path": report_path,
    }