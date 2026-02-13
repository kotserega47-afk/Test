import os
import sys
from datetime import datetime, timedelta
import pandas as pd
import yaml
from zoneinfo import ZoneInfo


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.telegram_bot import send_message_sync
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from utils.normalization import normalize_partner_name
from core.rules_provider import get_rules_snapshot
from core.config_manager import get_exclude_time_df

icon, name = LOG_PROFILES["ANALYZER"]
logger = get_logger(name, icon)

# ————————————————————————————————————————————————
# ДИНАМИЧЕСКОЕ ОПРЕДЕЛЕНИЕ TELEGRAM CHAT ID
# ————————————————————————————————————————————————
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_RACCOON_WALLET")
if not CHAT_ID:
    raise RuntimeError("Не задан TELEGRAM_CHAT_ID_RACCOON_WALLET")

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "config",
    "raccoon_wallet_config.yaml"
)


# === Статусы ===============================================================

def _normalize_status(s: str) -> str:
    return str(s).strip().lower()


def _status_success(s: str) -> bool:
    return _normalize_status(s) == "оплачен"


def _status_countable(s: str) -> bool:
    return _normalize_status(s) in {"оплачен", "ошибка"}


# === Конфиг =================================================================
def _normalize_columns(df):
    df.columns = [str(c).strip() for c in df.columns]
    return df

def _apply_mapping(df, mapping: dict, required: list[str], kind: str):
    # mapping: internal_key -> excel_column_name
    missing = [k for k in required if k not in mapping or not mapping[k]]
    if missing:
        raise RuntimeError(f"{kind}: в config нет mapping для: {missing}")

    df = _normalize_columns(df)

    # проверяем, что excel-колонки реально есть
    absent = [mapping[k] for k in required if mapping[k] not in df.columns]
    if absent:
        raise RuntimeError(f"{kind}: в файле нет колонок: {absent}")

    # переименовываем в единые внутренние имена
    rename = {mapping[k]: k for k in mapping if mapping[k] in df.columns}
    return df.rename(columns=rename)

def _get_mapping(cfg: dict, section: str) -> dict:
    cols = (cfg.get("columns") or {}).get(section) or {}
    # нормализуем строки
    return {k: str(v).strip() for k, v in cols.items() if v is not None}

def _require_mapping(mapping: dict, required: list[str], kind: str):
    missing = [k for k in required if not mapping.get(k)]
    if missing:
        raise RuntimeError(f"{kind}: в config нет mapping для ключей: {missing}")

def _load_cfg():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError("raccoon_wallet_config.yaml не найден")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    cfg.setdefault("window_minutes", 8)
    cfg.setdefault("offset_minutes", 8)
    cfg.setdefault("min_events", 10)
    cfg.setdefault("partners", {})
    cfg.setdefault("groups", {})

    return cfg
def _parse_analyzers_csv(val: str) -> list[str]:
    return sorted(set(
        p.strip().lower()
        for p in str(val or "").split(",")
        if p.strip()
    ))

# === rules.xlsx (thresholds_partner) ========================================

ANALYZER_KEY = "raccoon_wallet"

def _apply_wallet_limits_from_rules(cfg: dict) -> dict:
    """
    Применяет лимиты из rules.xlsx (лист wallet_limits).

    CONTRACT v4:
      - analyzers: required (CSV list)
      - limit_type: daily_max_amount
      - scope: partner|group
      - uniqueness key for enabled=1:
          (analyzers, scope, scope_value, limit_type)

    Применение:
      - scope=partner -> cfg['partners'][partner]['daily_max_amount']
      - scope=group   -> cfg['groups'][group]['daily_max_amount']
    """

    rules_path = get_rules_snapshot().local_path

    try:
        df = pd.read_excel(rules_path, sheet_name="wallet_limits")
    except Exception as e:
        logger.warning(f"⚠️ wallet_limits: не удалось прочитать rules.xlsx ({rules_path}): {e}")
        return cfg

    if df.empty:
        return cfg

    # --- CONTRACT v4: required columns ---
    if "analyzers" not in df.columns:
        raise ValueError("wallet_limits: missing required column 'analyzers' (contract v4)")

    df = df.copy()

    # normalize base fields
    df["enabled"] = pd.to_numeric(df.get("enabled"), errors="coerce").fillna(0).astype(int)
    df["analyzers"] = df.get("analyzers").astype(str).str.strip()
    df["_analyzers_list"] = df["analyzers"].apply(_parse_analyzers_csv)

    # fail-closed: enabled=1 -> analyzers must be non-empty
    bad_an = df[df["enabled"].eq(1) & df["_analyzers_list"].map(len).eq(0)]
    if not bad_an.empty:
        raise ValueError("wallet_limits: analyzers cannot be empty for enabled=1 (contract v4)")

    df["scope"] = df.get("scope").astype(str).str.strip().str.lower()
    df["scope_value"] = df.get("scope_value").astype(str).str.strip()
    df["limit_type"] = df.get("limit_type").astype(str).str.strip().str.lower()
    df["limit_value"] = pd.to_numeric(df.get("limit_value"), errors="coerce")

    # keep only enabled + supported type + current analyzer
    df = df[
        (df["enabled"] == 1) &
        (df["limit_type"] == "daily_max_amount") &
        (df["_analyzers_list"].map(lambda lst: ANALYZER_KEY in lst))
    ]

    if df.empty:
        return cfg

    # --- CONTRACT v4: validate scope ---
    valid_scopes = {"partner", "group"}
    bad_scope = df[~df["scope"].isin(valid_scopes)]
    if not bad_scope.empty:
        bad_vals = sorted(set(bad_scope["scope"].astype(str).tolist()))
        raise ValueError(f"wallet_limits: invalid scope value(s) {bad_vals} (allowed: partner, group)")

    # --- CONTRACT v4: limit_value required and numeric ---
    bad_val = df[df["limit_value"].isna()]
    if not bad_val.empty:
        raise ValueError("wallet_limits: limit_value cannot be empty/non-numeric for enabled=1 (contract v4)")

    # --- CONTRACT v4: uniqueness check ---
    # key: (analyzers, scope, scope_value, limit_type)
    dup = (
        df.groupby(["analyzers", "scope", "scope_value", "limit_type"])
          .size()
          .reset_index(name="cnt")
    )
    if (dup["cnt"] > 1).any():
        raise ValueError("wallet_limits: duplicate active rules for same key (contract v4)")

    cfg.setdefault("partners", {})
    cfg.setdefault("groups", {})

    partners = cfg.get("partners") or {}
    groups = cfg.get("groups") or {}

    # normalized map for existing partners from cfg
    p_norm_map = {normalize_partner_name(k): k for k in partners.keys()}

    applied_p = 0
    applied_g = 0

    for _, r in df.iterrows():
        scope = r["scope"]
        scope_value = r["scope_value"]
        val = float(r["limit_value"])

        # scope_value must exist (contractually required for applying)
        if not scope_value:
            raise ValueError("wallet_limits: scope_value cannot be empty for enabled=1 (contract v4)")

        if scope == "partner":
            p_norm = normalize_partner_name(scope_value)
            cfg_key = p_norm_map.get(p_norm)

            if not cfg_key:
                # Создаём партнёра, чтобы лимит реально применился
                cfg_key = scope_value
                partners.setdefault(cfg_key, {})
                p_norm_map[p_norm] = cfg_key

            partners[cfg_key]["daily_max_amount"] = val
            applied_p += 1

        elif scope == "group":
            gname = scope_value
            groups.setdefault(gname, {})
            groups[gname]["daily_max_amount"] = val
            applied_g += 1

    if applied_p or applied_g:
        logger.info(f"[wallet_limits] applied partners={applied_p}, groups={applied_g} from rules.xlsx")

    cfg["partners"] = partners
    cfg["groups"] = groups
    return cfg


def _apply_partner_thresholds_from_rules(cfg: dict) -> dict:
    """
    Применяет пороги из rules.xlsx (лист thresholds_partner) для wallet.

    Канон v2:
      - metric=conversion_rate  -> threshold_min (% 0..100) -> cfg['partners'][*]['threshold']
      - metric=api_cancel_rate  -> threshold_max (% 0..100) -> cfg['partners'][*]['api_cancel_threshold'] (backward compat)

    Поддерживаем миграцию:
      - metric=threshold               -> трактуем как conversion_rate
      - metric=api_cancel_threshold    -> трактуем как api_cancel_rate
      - колонка threshold              -> deprecated fallback, если min/max не заполнены
    """

    rules_path = get_rules_snapshot().local_path

    try:
        df = pd.read_excel(rules_path, sheet_name="thresholds_partner")
    except Exception as e:
        logger.warning(f"⚠️ thresholds_partner: не удалось прочитать rules.xlsx ({rules_path}): {e}")
        return cfg

    if df.empty:
        return cfg

    df = df.copy()
    df["enabled"] = pd.to_numeric(df.get("enabled"), errors="coerce").fillna(0).astype(int)
    df["analyzer"] = df.get("analyzer").astype(str).str.strip().str.lower()
    df["partner"] = df.get("partner").astype(str).str.strip()
    df["metric"] = df.get("metric").astype(str).str.strip().str.lower()

    # columns (v2 + deprecated)
    df["threshold"] = pd.to_numeric(df.get("threshold"), errors="coerce")
    df["threshold_min"] = pd.to_numeric(df.get("threshold_min"), errors="coerce")
    df["threshold_max"] = pd.to_numeric(df.get("threshold_max"), errors="coerce")

    df = df[(df["enabled"] == 1) & (df["analyzer"] == ANALYZER_KEY)]
    if df.empty:
        return cfg

    cfg.setdefault("partners", {})
    partners = cfg.get("partners") or {}

    applied = 0

    # map normalized partner -> cfg key
    norm_map = {normalize_partner_name(k): k for k in partners.keys()}

    for _, r in df.iterrows():
        metric = str(r["metric"] or "").strip().lower()
        # migrate metric names
        if metric == "threshold":
            metric = "conversion_rate"
        if metric == "api_cancel_threshold":
            metric = "api_cancel_rate"

        p_raw = str(r["partner"] or "").strip()
        if not p_raw:
            continue
        p_norm = normalize_partner_name(p_raw)
        cfg_key = norm_map.get(p_norm)
        if not cfg_key:
            # не молчим: создаём партнёра, чтобы правило применилось
            cfg_key = p_raw
            partners.setdefault(cfg_key, {})
            norm_map[p_norm] = cfg_key

        if metric == "conversion_rate":
            v = r["threshold_min"]
            if pd.isna(v):
                v = r["threshold"]  # deprecated
            if pd.isna(v):
                continue
            partners[cfg_key]["threshold"] = float(v)  # %
            applied += 1

        elif metric == "api_cancel_rate":
            v = r["threshold_max"]
            if pd.isna(v):
                v = r["threshold"]  # deprecated
            if pd.isna(v):
                continue
            partners[cfg_key]["api_cancel_threshold"] = float(v)  # % (old key used in code ниже)
            applied += 1

    if applied:
        logger.info(f"[thresholds_partner] applied={applied} from rules.xlsx")

    cfg["partners"] = partners
    return cfg

# === Основной анализатор =====================================================

def analyze_raccoon_wallets(payin_path: str, payout_path: str | None = None):
    cfg = _load_cfg()
    cfg = _apply_partner_thresholds_from_rules(cfg)
    cfg = _apply_wallet_limits_from_rules(cfg)
    window_min = cfg["window_minutes"]
    offset_min = cfg["offset_minutes"]

    tz = ZoneInfo("Europe/Moscow")

    logger.info(f"[Analyzer] Загружаю PayIn: {payin_path}")

    # === Чтение PayIn ==========================================================

    try:
        df = pd.read_excel(payin_path)
    except Exception as e:
        send_message_sync(f"⚠️ Не удалось прочитать PayIn: {e}", chat_id=CHAT_ID)
        return

    if df.empty:
        logger.info("[Analyzer] PayIn пуст — выходим")
        return

    payin_map = _get_mapping(cfg, "payin")
    _require_mapping(payin_map, required=["dt", "partner", "status", "amount"], kind="payin")
    df = _apply_mapping(df, payin_map, required=["dt", "partner", "status", "amount"], kind="payin")

    COL_DT = "dt"
    COL_PARTNER = "partner"
    COL_STATUS = "status"
    COL_AMOUNT = "amount"
    COL_INFO = "info" if "info" in df.columns else None

    # нормализация PayIn дат
    df[COL_DT] = df[COL_DT].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()

    df["_dt"] = pd.to_datetime(df[COL_DT], dayfirst=True, errors="coerce")
    if df["_dt"].dt.tz is None:
        df["_dt"] = df["_dt"].dt.tz_localize(tz, nonexistent="shift_forward")

    df["_partner_norm"] = df[COL_PARTNER].astype(str).apply(normalize_partner_name)
    df["_status_raw"] = df[COL_STATUS].astype(str)
    df["_status_success"] = df["_status_raw"].apply(_status_success)
    df["_status_count"] = df["_status_raw"].apply(_status_countable)
    df["_info_norm"] = df[COL_INFO].astype(str).str.lower()

    # === APPLY exclude_time (единые окна) =====================================

    try:

        exclude_df = get_exclude_time_df(
            rules_xlsx_path=os.getenv("RULES_XLSX_PATH"),
            notify=send_message_sync,
            chat_id=CHAT_ID,
        )

        # 1) гарантируем datetime
        exclude_df["start_dt"] = pd.to_datetime(exclude_df["start_dt"], dayfirst=True, errors="coerce")
        exclude_df["end_dt"] = pd.to_datetime(exclude_df["end_dt"], dayfirst=True, errors="coerce")

        # 2) приводим таймзону к Europe/Moscow (как у df["_dt"])
        tz = df["_dt"].dt.tz  # Europe/Moscow

        if getattr(exclude_df["start_dt"].dt, "tz", None) is None:
            exclude_df["start_dt"] = exclude_df["start_dt"].dt.tz_localize(tz, nonexistent="shift_forward",
                                                                           ambiguous="NaT")
        else:
            exclude_df["start_dt"] = exclude_df["start_dt"].dt.tz_convert(tz)

        if getattr(exclude_df["end_dt"].dt, "tz", None) is None:
            exclude_df["end_dt"] = exclude_df["end_dt"].dt.tz_localize(tz, nonexistent="shift_forward", ambiguous="NaT")
        else:
            exclude_df["end_dt"] = exclude_df["end_dt"].dt.tz_convert(tz)

        # только активные окна, применимые к wallet
        ex = exclude_df[
            (exclude_df["enabled"] == 1) &
            (exclude_df["_analyzers_list"].map(lambda lst: ANALYZER_KEY in lst))
            ].copy()

        if not ex.empty:
            # нормализуем партнёра в rules так же, как в данных
            ex["_partner_norm"] = ex["partner"].apply(normalize_partner_name)

            # берём только ошибки (то, что влияет на конверсию)
            err_mask = df["_status_raw"].str.lower() == "ошибка"
            df_err = df.loc[err_mask, ["_dt", "_partner_norm"]].copy()
            df_err["_src_idx"] = df_err.index

            if not df_err.empty:
                # join по партнёру
                m = df_err.merge(
                    ex[["_partner_norm", "start_dt", "end_dt"]],
                    on="_partner_norm",
                    how="left",
                )

                # ошибка попала в любое исключённое окно
                in_window = (m["_dt"] >= m["start_dt"]) & (m["_dt"] < m["end_dt"])
                excluded_src_idx = m.loc[in_window.fillna(False), "_src_idx"].unique()

                # ВАЖНО: выключаем участие в расчётах
                df.loc[excluded_src_idx, "_status_count"] = False

                # DEBUG: сколько ошибок исключено exclude_time
                excluded_cnt = (
                        (df["_status_raw"].str.lower() == "ошибка")
                        & (df["_status_count"] == False)
                ).sum()

                logger.info(f"[DEBUG exclude_time] excluded_errors={excluded_cnt}")

    except Exception as e:
        send_message_sync(
            f"❌ rules exclude_time остановил RaccoonWalletAnalyzer: {e}",
            chat_id=CHAT_ID,
        )
        return

    logger.info(
        f"[DEBUG counts] countable_total={df['_status_count'].sum()}, "
        f"success_total={df['_status_success'].sum()}"
    )

    now = datetime.now(tz)

    # временные окна
    end_time = now - timedelta(minutes=offset_min)
    start_time = end_time - timedelta(minutes=window_min)

    df_window = df[(df["_dt"] >= start_time) & (df["_dt"] < end_time)]
    df_today = df[df["_dt"] >= now.replace(hour=0, minute=0, second=0, microsecond=0)]

    partners_cfg = cfg["partners"]
    groups_cfg = cfg["groups"]

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

        success = subset["_status_success"].sum()
        conv = (success / total * 100) if total else 0

        today_part = df_today[df_today["_partner_norm"] == key_norm]
        today_success = today_part[today_part["_status_success"]]
        amount_today = pd.to_numeric(today_success[COL_AMOUNT], errors="coerce").sum()

        threshold = settings.get("threshold", 0)
        api_threshold = settings.get("api_cancel_threshold", 100)

        min_events = cfg["min_events"]

        # Конверсия
        if total < min_events:
            conv_bad = False
            conv_text = f"{conv:.1f}% — ℹ️ Недостаточно данных"
        else:
            conv_bad = conv < threshold
            conv_text = (
                f"{conv:.1f}% (< {threshold:.1f}%) — "
                + ("🔴" if conv_bad else "🟢")
            )

        # API ошибки за час
        error_keyword = "отмена по api"
        one_hour_ago = now - timedelta(hours=1)

        last_hour = df[
            (df["_partner_norm"] == key_norm)
            & (df["_dt"] >= one_hour_ago)
        ]

        lh_countable = last_hour[last_hour["_status_count"]]
        lh_total = len(lh_countable)
        lh_papi = lh_countable["_info_norm"].str.contains(error_keyword, case=False, na=False).sum()

        if lh_total < min_events:
            api_rate = 0
            api_bad = False
            api_total = lh_papi
            api_icon = "ℹ️"
        else:
            api_rate = (lh_papi / lh_total * 100) if lh_total else 0
            api_total = lh_papi
            api_bad = api_rate > api_threshold
            api_icon = "🔴" if api_bad else "🟢"

        # Нет доступных аккаунтов
        nok_wallets_total = sub_all["_info_norm"].str.contains("нет доступных аккаунтов").sum()
        nok_bad = nok_wallets_total > 0

        # Лимиты
        daily_limit = settings.get("daily_max_amount")
        group_name = None

        for gname, gdata in groups_cfg.items():
            if partner_name in (gdata.get("partners") or []):
                group_name = gname
                daily_limit = gdata.get("daily_max_amount")
                break

        if group_name:
            group_partners = groups_cfg[group_name]["partners"]
            df_group_today = df_today[df_today["_partner_norm"].isin(
                [normalize_partner_name(p) for p in group_partners]
            )]
            df_group_success = df_group_today[df_group_today["_status_success"]]
            group_amount_today = pd.to_numeric(df_group_success[COL_AMOUNT], errors="coerce").sum()
            percent_filled = int(group_amount_today / daily_limit * 100) if daily_limit else 0
        else:
            percent_filled = int(amount_today / daily_limit * 100) if daily_limit else 0

        # лимит статус
        if not daily_limit:
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

        daily_limit = float(daily_limit) if daily_limit else 0.0
        last_success = df[
            (df["_partner_norm"] == key_norm) &
            (df["_status_success"])
            ]

        if not last_success.empty:
            last_op_time = last_success["_dt"].max()
            last_op_str = last_op_time.strftime("%d.%m %H:%M:%S")
        else:
            last_op_str = "—"

        nok_line = (
            f"  Нет доступных аккаунтов: {nok_wallets_total} — 🔴\n"
            if nok_wallets_total > 0
            else ""
        )

        msg = (
            f"{partner_name}\n"
            f"  Всего операций: {total}\n"
            f"  Успешных: {success}\n"
            f"  Конверсия: {conv_text}\n"
            f"  Поступления: {amount_today:,.0f} / {float(daily_limit or 0):,.0f} "
            f"({percent_filled}%) — {limit_icon}\n"
            f"  Отмен по API: {api_total} шт ({api_rate:.1f}%) — {api_icon}\n"
            f"{nok_line}"
            f"  Последняя успешная операция: {last_op_str}\n"
        )

        messages.append(msg)

        if conv_bad or api_bad or limit_bad or limit_warn or nok_bad:
            bad.append({
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
            })

    # === Если нет сообщений — выход =================================================

    if not messages:
        logger.info("[Analyzer] Нет партнёров с операциями — ничего не отправляем")
        return

    # === Зависшие операции ==========================================================

    pending_cfg = cfg.get("pending_thresholds", {})
    payin_limit = pending_cfg.get("payin_minutes", 10)
    payout_limit = pending_cfg.get("payout_minutes", 180)

     #PayIn зависшие
    df_pending_payin = df[
        (df["_status_raw"].str.lower() == "ожидает оплаты")
       & ((now - df["_dt"]) > timedelta(minutes=payin_limit))
    ]
    pending_payin_count = len(df_pending_payin)


    # Формируем блок зависших
    pending_block = (
        "⏳ Зависшие:\n"
        f"• Поступления: {pending_payin_count} шт\n"
    )

    # === Отправляем основной отчёт ===============================================

    send_message_sync(
        pending_block
        + "📦 Wallet Analyzer\n"
        + f"🕒 Окно: {window_min} мин (смещение {offset_min})\n\n"
        + "\n\n".join(messages),
        chat_id=CHAT_ID,
    )

    # === BAD блок ===============================================================

    if not bad:
        send_message_sync("🟢 Все партнёры в норме!", chat_id=CHAT_ID)
        return

    lines = ["❗ Обнаружены отклонения:"]

    for p in bad:
        block = f"\n{p['name']}\n"

        if p["conv_bad"]:
            block += "  Конверсия ниже порога — 🔴\n"
        if p["api_bad"]:
            block += (
                f"  Отмен по API: {p['api_rate']:.1f}% (> {p['api_threshold']}%) — 🔴\n"
            )
        if p["limit_bad"]:
            block += "  Лимит превышен — 🔴\n"
        if p["limit_warn"]:
            block += f"  Лимит почти исчерпан ({p['percent']}%) — 🟡\n"
        if p["nok_bad"]:
            block += f"  Нет доступных аккаунтов: {p['nok_count']} — 🔴\n"

        lines.append(block)

    send_message_sync("\n".join(lines), chat_id=CHAT_ID)

