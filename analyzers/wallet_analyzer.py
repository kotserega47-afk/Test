import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

# Добавляем корень проекта в пути
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.telegram_bot import send_message_sync
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from load_data import normalize_partner_name
from core.config_manager import get_exclude_time_df
from integrations.dropbox_watcher import download_file
import tempfile

icon, name = LOG_PROFILES["ANALYZER"]
logger = get_logger(name, icon)

# ————————————————————————————————————————————————
# TELEGRAM CHAT ID
# ————————————————————————————————————————————————
def _get_chat_id() -> str:
    chat_id = os.getenv("TELEGRAM_CHAT_ID_WALLET")
    if not chat_id:
        raise RuntimeError("Не задан TELEGRAM_CHAT_ID_WALLET")
    return chat_id

# ————————————————————————————————————————————————
# PATHS / DEFAULTS
# ————————————————————————————————————————————————
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "wallet_config.yaml")
DEFAULT_RULES_XLSX_PATH = "/tmp/rules/rules.xlsx"
ANALYZER_KEY = "wallet"

# === Статусы ===============================================================


def _normalize_status(s: str) -> str:
    return str(s).strip().lower()


def _status_success(s: str) -> bool:
    return _normalize_status(s) == "оплачен"


def _status_countable(s: str) -> bool:
    return _normalize_status(s) in {"оплачен", "ошибка"}


# === Конфиг =================================================================


def _load_cfg() -> dict:
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError("wallet_config.yaml не найден")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # defaults
    cfg.setdefault("window_minutes", 8)
    cfg.setdefault("offset_minutes", 8)
    cfg.setdefault("min_events", 10)
    cfg.setdefault("partners", {})
    cfg.setdefault("groups", {})
    cfg.setdefault("pending_thresholds", {"payin_minutes": 10, "payout_minutes": 180})

    # optional (можно будет перенести в rules позже)
    cfg.setdefault("api_cancel_keyword", "отмена по api")

    return cfg


# === exclude_time ===========================================================


def _apply_exclude_time(df: pd.DataFrame, chat_id: str) -> pd.DataFrame:
    """
    Применяет exclude_time к PayIn:
      - ищет окна exclude_time для ANALYZER_KEY
      - выключает участие строк "ошибка" в расчётах (_status_count=False),
        если они попали в исключённые интервалы.

    Важно: если rules.xlsx недоступен или повреждён — НЕ стопаем анализатор.
    Просто логируем/уведомляем и продолжаем без exclude_time.
    """
    # 1) Сначала пробуем Dropbox (как special_cards.xlsx)
    dropbox_rules_folder = os.getenv("DROPBOX_RULES_PATH", "/Ostin/platform/config/rules")
    dropbox_rules_file = os.path.join(dropbox_rules_folder, "rules.xlsx")
    local_rules_path = os.path.join(tempfile.gettempdir(), "rules.xlsx")

    rules_path = None
    if download_file(dropbox_rules_file, local_rules_path):
        rules_path = local_rules_path
        logger.info(f"[exclude_time] 📥 rules.xlsx загружен из Dropbox: {dropbox_rules_file} → {local_rules_path}")
    else:
        # 2) Фолбэк: локальный путь (если ты всё-таки примонтировал файл)
        candidate = os.getenv("RULES_XLSX_PATH", DEFAULT_RULES_XLSX_PATH)

        # если дали папку — ожидаем внутри rules.xlsx
        if candidate and os.path.isdir(candidate):
            candidate = os.path.join(candidate, "rules.xlsx")

        if candidate and os.path.exists(candidate):
            rules_path = candidate

    # 3) Если не нашли нигде — пропускаем exclude_time
    if not rules_path:
        msg = (
            f"⚠️ rules.xlsx не найден.\n"
            f"Dropbox: {dropbox_rules_file}\n"
            f"Local: {os.getenv('RULES_XLSX_PATH', DEFAULT_RULES_XLSX_PATH)!r}\n"
            f"— пропускаю exclude_time"
        )
        logger.warning(msg)
        send_message_sync(msg, chat_id=chat_id)
        return df

    try:
        exclude_df = get_exclude_time_df(
            rules_xlsx_path=rules_path,
            notify=send_message_sync,
            chat_id=chat_id,
        )
    except Exception as e:
        msg = f"⚠️ rules exclude_time недоступен ({rules_path}): {e} — продолжаю без exclude_time"
        logger.exception(msg)
        send_message_sync(msg, chat_id=chat_id)
        return df

    # только активные окна, применимые к wallet
    try:
        ex = exclude_df[
            (exclude_df.get("enabled") == 1)
            & (exclude_df["_analyzers_list"].map(lambda lst: ANALYZER_KEY in lst))
        ].copy()
    except Exception:
        msg = "⚠️ exclude_time: неожиданный формат правил — продолжаю без exclude_time"
        logger.exception(msg)
        send_message_sync(msg, chat_id=chat_id)
        return df

    if ex.empty:
        return df

    # приводим start_dt/end_dt к той же TZ, что и df["_dt"], иначе сравнение упадёт
    df_tz = df["_dt"].dt.tz  # tzinfo (например Europe/Moscow)

    for col in ("start_dt", "end_dt"):
        if col not in ex.columns:
            continue

        ex[col] = pd.to_datetime(ex[col], errors="coerce")

        # если правила без TZ -> локализуем в TZ данных
        if getattr(ex[col].dtype, "tz", None) is None:
            ex[col] = ex[col].dt.tz_localize(df_tz, nonexistent="shift_forward", ambiguous="NaT")
        else:
            ex[col] = ex[col].dt.tz_convert(df_tz)

    # нормализуем партнёра в rules так же, как в данных
    ex["partner"] = ex.get("partner").fillna("").astype(str)
    ex["_partner_norm"] = ex["partner"].apply(normalize_partner_name)

    # берём только ошибки (то, что влияет на конверсию)
    err_mask = df["_status_raw"].astype(str).str.lower() == "ошибка"
    df_err = df.loc[err_mask, ["_dt", "_partner_norm"]].copy()
    if df_err.empty:
        return df

    # сохраняем исходный индекс, чтобы не ошибиться после merge
    df_err["_src_idx"] = df_err.index

    # join по партнёру
    m = df_err.merge(
        ex[["_partner_norm", "start_dt", "end_dt"]],
        on="_partner_norm",
        how="left",
    )

    # ошибка попала в любое исключённое окно
    valid_rule = m["start_dt"].notna() & m["end_dt"].notna()
    in_window = valid_rule & (m["_dt"] >= m["start_dt"]) & (m["_dt"] < m["end_dt"])
    excluded_src_idx = m.loc[in_window, "_src_idx"].dropna().unique()

    if len(excluded_src_idx) > 0:
        df.loc[excluded_src_idx, "_status_count"] = False

        excluded_cnt = (
            (df["_status_raw"].astype(str).str.lower() == "ошибка") & (~df["_status_count"].astype(bool))
        ).sum()
        logger.info(f"[exclude_time] excluded_errors={excluded_cnt}")

    return df


# === Основной анализатор =====================================================


def analyze_wallets(payin_path: str, payout_path: str):
    chat_id = _get_chat_id()
    cfg = _load_cfg()
    window_min = int(cfg["window_minutes"])
    offset_min = int(cfg["offset_minutes"])
    min_events = int(cfg["min_events"])
    api_keyword = str(cfg.get("api_cancel_keyword", "отмена по api")).lower()

    tz = ZoneInfo("Europe/Moscow")

    logger.info(f"[Analyzer] Загружаю PayIn: {payin_path}")

    # === Чтение PayIn ==========================================================
    try:
        df = pd.read_excel(payin_path)
    except Exception as e:
        send_message_sync(f"⚠️ Не удалось прочитать PayIn: {e}", chat_id=chat_id)
        return

    if df.empty:
        logger.info("[Analyzer] PayIn пуст — выходим")
        return

    # Колонки PayIn (как в выгрузке Antares)
    COL_DT = "Дата/Время создания"
    COL_PARTNER = "Партнер"
    COL_STATUS = "Статус"
    COL_INFO = "Инфо"
    COL_AMOUNT = "Сумма"

    missing = [c for c in [COL_DT, COL_PARTNER, COL_STATUS, COL_INFO, COL_AMOUNT] if c not in df.columns]
    if missing:
        send_message_sync(f"⚠️ PayIn: отсутствуют колонки: {missing}", chat_id=chat_id)
        return

    # нормализация PayIn дат
    df[COL_DT] = df[COL_DT].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()

    df["_dt"] = pd.to_datetime(df[COL_DT], dayfirst=True, errors="coerce")
    if df["_dt"].isna().all():
        send_message_sync("⚠️ PayIn: не удалось распарсить даты", chat_id=chat_id)
        return

    # timezone: делаем все даты tz-aware в Europe/Moscow
    if getattr(df["_dt"].dtype, "tz", None) is None:
        df["_dt"] = df["_dt"].dt.tz_localize(tz, nonexistent="shift_forward")
    else:
        df["_dt"] = df["_dt"].dt.tz_convert(tz)

    df["_partner_norm"] = df[COL_PARTNER].astype(str).apply(normalize_partner_name)
    df["_status_raw"] = df[COL_STATUS].astype(str)
    df["_status_success"] = df["_status_raw"].apply(_status_success)
    df["_status_count"] = df["_status_raw"].apply(_status_countable)
    df["_info_norm"] = df[COL_INFO].astype(str).str.lower()

    # === APPLY exclude_time ====================================================
    df = _apply_exclude_time(df, chat_id=chat_id)

    logger.info(
        f"[DEBUG counts] countable_total={int(df['_status_count'].sum())}, "
        f"success_total={int(df['_status_success'].sum())}"
    )

    now = datetime.now(tz)

    # временные окна
    end_time = now - timedelta(minutes=offset_min)
    start_time = end_time - timedelta(minutes=window_min)

    df_window = df[(df["_dt"] >= start_time) & (df["_dt"] < end_time)]
    df_today = df[df["_dt"] >= now.replace(hour=0, minute=0, second=0, microsecond=0)]

    partners_cfg: dict = cfg["partners"] or {}
    groups_cfg: dict = cfg["groups"] or {}

    messages = []
    bad = []

    # === Анализ по каждому партнёру ===========================================
    for partner_name, settings in partners_cfg.items():
        key_norm = normalize_partner_name(partner_name)

        sub_all = df_window[df_window["_partner_norm"] == key_norm]
        subset = sub_all[sub_all["_status_count"]]

        total = len(subset)
        if total == 0:
            continue

        success = int(subset["_status_success"].sum())
        conv = (success / total * 100) if total else 0.0

        today_part = df_today[df_today["_partner_norm"] == key_norm]
        today_success = today_part[today_part["_status_success"]]
        amount_today = float(pd.to_numeric(today_success[COL_AMOUNT], errors="coerce").fillna(0).sum())

        threshold = float(settings.get("threshold", 0) or 0)  # доля (0..1)
        api_threshold = float(settings.get("api_cancel_threshold", 100) or 100)  # проценты (0..100)

        # Конверсия
        if total < min_events:
            conv_bad = False
            conv_text = f"{conv:.1f}% — ℹ️ Недостаточно данных"
        else:
            conv_bad = conv < threshold * 100
            conv_text = (
                f"{conv:.1f}% (< {threshold * 100:.1f}%) — "
                + ("🔴" if conv_bad else "🟢")
            )

        # API ошибки за час
        one_hour_ago = now - timedelta(hours=1)
        last_hour = df[(df["_partner_norm"] == key_norm) & (df["_dt"] >= one_hour_ago)]

        lh_countable = last_hour[last_hour["_status_count"]]
        lh_total = len(lh_countable)
        lh_papi = int(lh_countable["_info_norm"].str.contains(api_keyword, na=False).sum())

        if lh_total < min_events:
            api_rate = 0.0
            api_bad = False
            api_total = lh_papi
            api_icon = "ℹ️"
        else:
            api_rate = (lh_papi / lh_total * 100) if lh_total else 0.0
            api_total = lh_papi
            api_bad = api_rate > api_threshold
            api_icon = "🔴" if api_bad else "🟢"

        # Нет доступных аккаунтов
        nok_wallets_total = int(sub_all["_info_norm"].str.contains("нет доступных аккаунтов").sum())
        nok_bad = nok_wallets_total > 0

        # Лимиты (старый механизм: partner/group из yaml; позже можно переехать в rules.xlsx)
        daily_limit = settings.get("daily_max_amount")
        group_name = None

        # group override
        for gname, gdata in groups_cfg.items():
            try:
                if partner_name in (gdata.get("partners") or []):
                    group_name = gname
                    daily_limit = gdata.get("daily_max_amount")
                    break
            except Exception:
                continue

        daily_limit_num = float(daily_limit) if daily_limit not in (None, "", 0) else None

        if group_name and daily_limit_num:
            group_partners = groups_cfg[group_name].get("partners") or []
            norm_list = [normalize_partner_name(p) for p in group_partners]
            df_group_today = df_today[df_today["_partner_norm"].isin(norm_list)]
            df_group_success = df_group_today[df_group_today["_status_success"]]
            group_amount_today = float(pd.to_numeric(df_group_success[COL_AMOUNT], errors="coerce").fillna(0).sum())
            percent_filled = int((group_amount_today / daily_limit_num) * 100) if daily_limit_num else 0
        elif daily_limit_num:
            percent_filled = int((amount_today / daily_limit_num) * 100) if daily_limit_num else 0
        else:
            percent_filled = 0

        # лимит статус
        if not daily_limit_num:
            limit_icon = "🟢"
            limit_bad = False
            limit_warn = False
        else:
            if percent_filled >= 100:
                limit_bad = True
                limit_warn = False
                limit_icon = "🔴"
            elif percent_filled >= 90:
                limit_bad = False
                limit_warn = True
                limit_icon = "🟡"
            else:
                limit_bad = False
                limit_warn = False
                limit_icon = "🟢"

        last_op_time = df[df["_partner_norm"] == key_norm]["_dt"].max()
        last_op_str = last_op_time.strftime("%d.%m %H:%M:%S") if pd.notna(last_op_time) else "-"

        nok_line = (
            f"  Нет доступных аккаунтов: {nok_wallets_total} — 🔴\n"
            if nok_wallets_total > 0
            else ""
        )

        # форматирование лимита (чтобы не падать на None)
        daily_limit_str = f"{daily_limit_num:,.0f}" if daily_limit_num else "—"

        msg = (
            f"{partner_name}\n"
            f"  Всего операций: {total}\n"
            f"  Успешных: {success}\n"
            f"  Конверсия: {conv_text}\n"
            f"  Поступления: {amount_today:,.0f} / {daily_limit_str} "
            f"({percent_filled}%) — {limit_icon}\n"
            f"  Отмен по API: {api_total} шт ({api_rate:.1f}%) — {api_icon}\n"
            f"{nok_line}"
            f"  Последняя операция: {last_op_str}\n"
        )

        messages.append(msg)

        if conv_bad or api_bad or limit_bad or limit_warn or nok_bad:
            bad.append(
                {
                    "name": partner_name,
                    "conv_bad": conv_bad,
                    "api_bad": api_bad,
                    "limit_bad": limit_bad,
                    "limit_warn": limit_warn,
                    "nok_bad": nok_bad,
                    "api_rate": api_rate,
                    "api_threshold": api_threshold,
                    "percent": percent_filled,
                    "nok_count": nok_wallets_total,
                }
            )

    # === Если нет сообщений — выход ===========================================
    if not messages:
        logger.info("[Analyzer] Нет партнёров с операциями — ничего не отправляем")
        return

    # === Зависшие операции =====================================================
    pending_cfg = cfg.get("pending_thresholds", {}) or {}
    payin_limit = int(pending_cfg.get("payin_minutes", 10) or 10)
    payout_limit = int(pending_cfg.get("payout_minutes", 180) or 180)

    # PayIn зависшие
    df_pending_payin = df[
        (df["_status_raw"].astype(str).str.lower() == "ожидает оплаты")
        & ((now - df["_dt"]) > timedelta(minutes=payin_limit))
    ]
    pending_payin_count = len(df_pending_payin)

    # Payout зависшие
    pending_payout_count = 0

    try:
        dfp = pd.read_excel(payout_path)

        COL_DT_P = "Дата/Время создания"
        COL_STATUS_P = "Статус"

        missing_p = [c for c in [COL_DT_P, COL_STATUS_P] if c not in dfp.columns]
        if not missing_p:
            dfp[COL_DT_P] = (
                dfp[COL_DT_P].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
            )
            dfp["_dt"] = pd.to_datetime(dfp[COL_DT_P], dayfirst=True, errors="coerce")
            if getattr(dfp["_dt"].dtype, "tz", None) is None:
                dfp["_dt"] = dfp["_dt"].dt.tz_localize(tz, nonexistent="shift_forward")
            else:
                dfp["_dt"] = dfp["_dt"].dt.tz_convert(tz)

            dfp["_status_raw"] = dfp[COL_STATUS_P].astype(str)

            df_pending_payout = dfp[
                (dfp["_status_raw"].astype(str).str.lower() == "ожидает оплаты")
                & ((now - dfp["_dt"]) > timedelta(minutes=payout_limit))
            ]
            pending_payout_count = len(df_pending_payout)
        else:
            logger.warning(f"[Analyzer] Payout: отсутствуют колонки: {missing_p}")

    except Exception as e:
        logger.error(f"[Analyzer] Ошибка payout: {e}")

    pending_block = (
        "⏳ Зависшие:\n"
        f"• Поступления: {pending_payin_count} шт\n"
        f"• Выплаты: {pending_payout_count} шт\n\n"
    )

    # === Отправляем основной отчёт ============================================
    send_message_sync(
        pending_block
        + "📦 Wallet Analyzer\n"
        + f"🕒 Окно: {window_min} мин (смещение {offset_min})\n\n"
        + "\n\n".join(messages),
        chat_id=chat_id,
    )

    # === BAD блок ==============================================================
    if not bad:
        send_message_sync("🟢 Все партнёры в норме!", chat_id=chat_id)
        return

    lines = ["❗ Обнаружены отклонения:"]

    for p in bad:
        block = f"\n{p['name']}\n"

        if p["conv_bad"]:
            block += "  Конверсия ниже порога — 🔴\n"
        if p["api_bad"]:
            block += f"  Отмен по API: {p['api_rate']:.1f}% (> {p['api_threshold']}%) — 🔴\n"
        if p["limit_bad"]:
            block += "  Лимит превышен — 🔴\n"
        if p["limit_warn"]:
            block += f"  Лимит почти исчерпан ({p['percent']}%) — 🟡\n"
        if p["nok_bad"]:
            block += f"  Нет доступных аккаунтов: {p['nok_count']} — 🔴\n"

        lines.append(block)

    send_message_sync("\n".join(lines), chat_id=chat_id)
