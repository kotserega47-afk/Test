# analyzers/conversion.py

import os
import re
import tempfile
import pandas as pd

from openpyxl import Workbook
from datetime import datetime
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from utils.excel_utils import flatten_lists_in_df, write_df_to_sheet
from integrations.telegram_bot import send_message_sync, send_file_sync
from integrations.dropbox_watcher import download_file
from utils.normalization import parse_dt_series_msk
from core.rules_provider import get_snapshot_v2


icon, name = LOG_PROFILES["CONVERT"]
logger = get_logger(name, icon)

# -----------------------------
# Загрузка конфигурации
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID_ANALIZ") or "").strip()

# -----------------------------
# Вспомогательные функции (используем уже существующую нормализацию)
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
        df["datetime"] = parse_dt_series_msk(df["datetime"])

    required = [c for c in ["card", "status", "datetime"] if c in df.columns]
    if required:
        df.dropna(subset=required, inplace=True)

    if "datetime" in df:
        df.sort_values("datetime", ascending=False, inplace=True)

    return df


def _send_problem_cards_to_telegram(problem_df: pd.DataFrame) -> None:
    """
    Отправляет построчно список карт на отключение батчами по ~500 строк.
    Формат строки: "<card> <partner> <max_consecutive_errors>"
    """
    if not CHAT_ID:
        logger.warning("[telegram] CHAT_ID не задан — список карт не отправлен")
        return

    if problem_df.empty:
        return

    cols_ok = all(c in problem_df.columns for c in ["card", "partner", "max_consecutive_errors"])
    if not cols_ok:
        send_message_sync("⚠️ Пропущено формирование списка: отсутствуют нужные колонки.", chat_id=CHAT_ID)
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
        send_message_sync("🚫 Карты на отключение:\n" + "\n".join(chunk), chat_id=CHAT_ID)
    logger.info(f"[telegram] Отправлен список {total} карт на отключение.")


def count_last_error_streak(df: pd.DataFrame) -> pd.DataFrame:
    """
    Возвращает DataFrame с длиной последней непрерывной серии ошибок для каждой пары (карта, партнёр).
    Берём записи по убыванию даты, считаем подряд идущие 'ошибка' до первого 'оплачен' (или иного статуса ≠ 'ошибка').
    """
    results = []
    if df.empty:
        return pd.DataFrame(columns=["card", "partner_norm", "max_consecutive_errors"])

    for (card, partner), group in df.groupby(["card", "partner_norm"], sort=False):
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
# Основная функция анализа
# -----------------------------

def run(
    conv_file: str,
    card_files: list,
    col_mapping: dict,
    *,
    generate_excel: bool = True,
    send_telegram: bool = True,
    rules_force_sync: bool = False,
) -> dict:
    """
    ...
    """
    if send_telegram and not CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID_ANALIZ не задан — отправка в Telegram отключена")
        send_telegram = False

    logger.info(f"[run] 🚀 Начало анализа: {os.path.basename(conv_file)}")

    snapshot = get_snapshot_v2(force_sync=rules_force_sync)

    valid_statuses: set[str] = set()

    for param in snapshot.job_params:
        if not param.enabled:
            continue
        if param.job_key != "conversion":
            continue
        if param.param_key != "valid_status":
            continue

        value = str(param.value).strip().lower()
        if value:
            valid_statuses.add(value)

    if not valid_statuses:
        valid_statuses = {"готов к работе", "активный вход"}
    logger.info(f"[run] valid_statuses for conversion: {sorted(valid_statuses)}")

    # 0) Пути к special_cards.xlsx в Dropbox
    special_folder = os.getenv("DROPBOX_SPECIAL_PATH", "/Ostin/platform/special")
    dropbox_special_file = os.path.join(special_folder, "special_cards.xlsx")
    local_special_path = os.path.join(tempfile.gettempdir(), "special_cards.xlsx")

    # 1) Пытаемся скачать special_cards.xlsx и подготовить правила
    special_rules = {}  # ключ: (card, partner_norm) -> start_date (Timestamp)
    latest_special_date = None
    special_loaded = False

    try:
        if download_file(dropbox_special_file, local_special_path):
            logger.info(f"[run] 📥 special_cards.xlsx загружен: {dropbox_special_file} → {local_special_path}")
            # читаем файл
            df_special = pd.read_excel(local_special_path, dtype=str)

            df_special.rename(
                columns={
                    "Карта": "card",
                    "Партнер": "partner",
                    "Дата": "start_date",
                },
                inplace=True
            )

            # обязательные колонки
            missing_cols = [c for c in ["card", "partner", "start_date"] if c not in df_special.columns]
            if missing_cols:
                logger.warning(f"[run] ⚠️ В special_cards.xlsx нет колонок: {missing_cols}")
            else:
                # нормализация
                df_special["card"] = df_special["card"].astype(str).str.strip()
                df_special["partner_norm"] = df_special["partner"].apply(normalize_name)
                df_special["start_date"] = parse_dt_series_msk(
                    df_special["start_date"].astype(str).str.strip())
                # дубликаты: оставляем запись с самой свежей датой по (card, partner_norm)
                df_special.sort_values("start_date", ascending=False, inplace=True, na_position="last")
                df_special.drop_duplicates(subset=["card", "partner_norm"], keep="first", inplace=True)

                # словарь правил и "последняя дата"
                special_rules = df_special.set_index(["card", "partner_norm"])["start_date"].to_dict()
                latest_special_date = df_special["start_date"].max()
                special_loaded = True

                # 🔹 Telegram-отчёт по сегодняшним special-картам
                if send_telegram:
                    today = pd.Timestamp.now().normalize()
                    today_special = df_special[df_special["start_date"] == today]
                    if not today_special.empty:
                        counts = today_special["partner_norm"].value_counts()
                        stats = "\n".join([f"• {p}: {int(c)}" for p, c in counts.items()])
                        send_message_sync(
                            f"📊 Добавленные special-карты за {today.strftime('%d.%m.%Y')}:\n{stats}", chat_id=CHAT_ID)
                        logger.info(f"[run] 📊 Найдено {len(today_special)} новых special-карт.")
                    else:
                        send_message_sync(f"ℹ️ За {today.strftime('%d.%m.%Y')} новых special-карт не добавлено.", chat_id=CHAT_ID)
        else:
            logger.warning("[run] ⚠️ Не удалось скачать special_cards.xlsx из Dropbox.")
            if send_telegram:
                send_message_sync(
                    "⚠️ Файл special_cards.xlsx не найден в Dropbox.\nАнализ выполнен без ограничений для специальных карт.", chat_id=CHAT_ID)
    except Exception as e:
        logger.warning(f"[run] ⚠️ Ошибка при загрузке/чтении special_cards.xlsx: {e}")
        if send_telegram:
            send_message_sync(
                "⚠️ Ошибка при загрузке special_cards.xlsx из Dropbox.\nАнализ выполнен без ограничений для специальных карт.", chat_id=CHAT_ID)

    # Сообщаем последнюю дату (или что дат нет)
    if send_telegram:
        if special_loaded:
            if pd.notna(latest_special_date):
                send_message_sync(
                    f"📅 Последняя дата в special_cards.xlsx: {latest_special_date.strftime('%d.%m.%Y')}"
                , chat_id=CHAT_ID)
            else:
                send_message_sync("ℹ️ В special_cards.xlsx нет валидных дат.", chat_id=CHAT_ID)
        # если не загрузили — сообщения уже отправили выше

    # 2) Загрузка conversion-файла
    conv_df = load_data(conv_file, col_mapping)

    # Нормализация полей
    conv_df["status"] = conv_df["status"].astype(str).str.strip().str.lower()
    conv_df["partner_norm"] = conv_df["partner"].apply(normalize_name)
    conv_df["datetime"] = parse_dt_series_msk(conv_df["datetime"])
    conv_df.dropna(subset=["card", "datetime", "status"], inplace=True)

    # Фильтруем по допустимым статусам (ошибка/оплачен + валидные)
    allowed_statuses = {"ошибка", "оплачен"} | valid_statuses
    conv_df = conv_df[conv_df["status"].isin(allowed_statuses)]
    logger.info(f"[run] 📄 Загружено {len(conv_df)} строк из conversion.")


    # 3) Применяем индивидуальные ограничения по special_rules (карта + партнёр)
    if special_rules:
        before = len(conv_df)
        # Векторно: режем по каждой паре
        # Создадим вспомогательную Series с датой старта для каждой строки (если есть правило)
        key_tuples = list(special_rules.keys())
        if key_tuples:
            # Сформируем MultiIndex lookup через merge
            rules_df = pd.DataFrame(
                [(card, partner, dt) for (card, partner), dt in special_rules.items()],
                columns=["card", "partner_norm", "start_date"]
            )
            conv_df = conv_df.merge(rules_df, on=["card", "partner_norm"], how="left")

            # НОРМАЛИЗАЦИЯ ТИПА start_date К ТОМУ ЖЕ TZ-ФОРМАТУ, ЧТО И conv_df["datetime"]
            conv_df["start_date"] = parse_dt_series_msk(conv_df["start_date"])

            # Если для строки есть start_date -> оставляем только >= этой даты
            mask_keep = (conv_df["start_date"].isna()) | (conv_df["datetime"] >= conv_df["start_date"])
            conv_df = conv_df.loc[mask_keep].drop(columns=["start_date"])
        after = len(conv_df)
        if after != before:
            logger.info(f"[run] 🧭 Применены правила special_cards: отфильтровано {before - after} строк.")
    excluded_total = 0

    # 4) Применяем exclude-периоды из rules exclude_time
    for rule in snapshot.exclusion_rules:
        if not rule.enabled:
            continue
        if rule.job_key != "conversion":
            continue
        if rule.scope_type != "partner":
            continue

        partner = snapshot.partners.get(rule.scope_key)
        if not partner:
            continue

        partner_norm = normalize_name(partner.display_name or partner.source_name or "")
        if not partner_norm:
            continue

        before = len(conv_df)

        mask = (
                (conv_df["partner_norm"] == partner_norm)
                & (conv_df["datetime"].between(rule.start_dt, rule.end_dt))
        )
        conv_df = conv_df[~mask]

        excluded = before - len(conv_df)
        excluded_total += excluded
        if excluded > 0:
            logger.info(f"[run] ⏳ Исключено {excluded} строк по exclude для «{partner_norm}»")
    logger.info(f"[run] total excluded by rules exclude_time: {excluded_total}")

    # 5) Загрузка card-файлов (справочник статусов и партнёров)
    card_df_list = []
    for f in card_files or []:
        try:
            # В card-файле ожидаем минимум: "Карта", "Партнёр", "Статус"
            card_df_list.append(load_data(f, {"card": "Карта", "partner": "Партнёр", "status": "Статус", "pool": "Пул"}))

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
    logger.info(f"[run] Колонки в card_df: {list(card_df.columns)}")

    # 6) Подсчёт текущих серий ошибок (только последняя непрерывная)
    # Сортировка важна для корректного расчёта последовательностей
    conv_df.sort_values(["card", "partner_norm", "datetime"], inplace=True)
    max_errors = count_last_error_streak(conv_df)

    # 7) Пороги из rules thresholds_partner
    threshold_map: dict[str, int] = {}

    for rule in snapshot.threshold_rules:
        if not rule.enabled:
            continue
        if rule.job_key != "conversion":
            continue
        if rule.scope_type != "partner":
            continue
        if rule.metric_key != "error_streak":
            continue

        partner = snapshot.partners.get(rule.scope_key)
        if not partner:
            continue

        partner_norm = normalize_name(partner.display_name or partner.source_name or "")
        if not partner_norm:
            continue

        threshold_value = rule.threshold_max
        if threshold_value is None:
            continue

        threshold_map[partner_norm] = int(threshold_value)
    logger.info(f"[run] thresholds loaded for conversion: {len(threshold_map)} partners")

    merged = max_errors.copy()
    merged["threshold"] = merged["partner_norm"].map(threshold_map).fillna(4).astype(int)

    # 8) Добавляем статус и партнёров из card-файлов
    card_status_map = card_df.set_index("card")["status"].to_dict() if not card_df.empty else {}
    card_partners_map = card_df.set_index("card")["partner_list"].to_dict() if not card_df.empty else {}

    merged["status"] = merged["card"].map(card_status_map).astype(str).str.strip().str.lower()
    merged["partner_list"] = merged["card"].map(card_partners_map)

    # 9) Фильтрация проблемных карт
    problem_mask = (
        (merged["max_consecutive_errors"] >= merged["threshold"])
        & (merged["status"].isin(valid_statuses))
        & merged.apply(
            lambda r: isinstance(r["partner_list"], list) and r["partner_norm"] in r["partner_list"],
            axis=1
        )
    )
    problem = merged.loc[problem_mask].copy()
    problem.rename(columns={"partner_norm": "partner"}, inplace=True)

    # 10) Подсчёт "Карт в работе по партнёрам"
    # Берём карты из card_df в статусе "Готов к работе"/"Активный вход" и с заполненным партнёром
    ACTIVE_STATUSES = list(valid_statuses)

    active_cards = card_df[
        card_df["status"].isin(ACTIVE_STATUSES)
        & card_df["partner"].notna()
        & (card_df["partner"].str.strip() != "")
        ].copy()

    # Исключаем карты, которые попали в problem (на отключение)
    if not problem.empty:
        active_cards = active_cards[~active_cards["card"].isin(problem["card"])]

    # Разворачиваем многозначных партнёров из поля "Партнёр"
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

    # === Карт в работе по пулам ===
    if "pool" in card_df.columns:
        active_pools = card_df[
            card_df["status"].isin(ACTIVE_STATUSES)
            & card_df["pool"].notna()
            & (card_df["pool"].str.strip() != "")
            ].copy()

        # Исключаем карты на отключение
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
    logger.info(f"[run] 📊 Карт в работе по партнёрам: {summary['Карт в работе по партнёрам']}")

    # Excel отчёт
    wb = None
    report_path = None
    if generate_excel:
        wb = Workbook()
        wb.remove(wb.active)

        # Проблемные карты (если есть)
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
        # Добавляем дату в формате (ДД.ММ.ГГГГ) к имени отчёта
        current_date = datetime.now().strftime("%d.%m.%Y")
        base_name = f"report_{os.path.splitext(os.path.basename(conv_file))[0]}_({current_date}).xlsx"
        report_path = os.path.join(tempfile.gettempdir(), base_name)
        logger.info(f"[run] Листы отчёта: {wb.sheetnames}")
        wb.save(report_path)
        logger.info(f"[run] 📁 Отчёт сохранён: {report_path}")

    # Telegram отправки
    if send_telegram:
        # список карт
        if not problem.empty:
            _send_problem_cards_to_telegram(problem)
        else:
            send_message_sync("ℹ️ Нет карт, превысивших порог ошибок.", chat_id=CHAT_ID)

        # summary
        try:
            msg_lines = [f"• {p}: {n}" for p, n in cards_in_work_by_partner.items()]
            summary_text = (
                    f"✅ Анализ *{os.path.basename(conv_file)}* завершён.\n"
                    f"Карт в работе по партнёрам:\n" + "\n".join(msg_lines) + "\n"
                                                                              f"На отключение: {summary.get('Карты на отключение', '—')}"
            )
            send_message_sync(summary_text, chat_id=CHAT_ID)
        except Exception as e:
            logger.exception(f"[run] Ошибка при отправке Telegram summary: {e}")

        # файл
        if generate_excel and report_path and os.path.exists(report_path):
            try:
                send_file_sync(report_path, caption=f"📊 Отчёт по {os.path.basename(conv_file)}", chat_id=CHAT_ID)
                logger.info(f"[run] Файл отчёта отправлен в Telegram: {report_path}")
            except Exception as e:
                logger.exception(f"[run] Ошибка при отправке отчёта в Telegram: {e}")

    # Возвращаем результат
    return {
        "summary": summary,
        "workbook": wb,
        "problem_cards": problem,
        "report_path": report_path,
    }