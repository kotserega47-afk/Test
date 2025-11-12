# analyzers/wallet_analyzer.py
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.telegram_bot import send_message_sync
from utils.logger import logger


CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_WALLET") or os.getenv("TELEGRAM_CHAT_ID")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "config", "wallet_config.yaml")

STATE_PATH = Path("/tmp/wallet_alerts_state.json")


# ==============================================
#   ВСПОМОГАТЕЛЬНЫЕ БЛОКИ
# ==============================================

def _now_msk():
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("Europe/Moscow"))
    except:
        return datetime.now().astimezone()


def _normalize(v):
    if not isinstance(v, str):
        return ""
    v = v.strip().lower().replace("ё", "е")
    v = v.replace("амобайл", "а-мобайл")
    return " ".join(v.split())


def _status_success(s):
    s = _normalize(s)
    return s in {"оплачен", "успешно", "success", "paid"}


def _load_cfg():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError("wallet_config.yaml не найден")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    cfg.setdefault("window_minutes", 8)
    cfg.setdefault("offset_minutes", 8)
    cfg.setdefault("min_events", 10)
    cfg.setdefault("success_window_minutes", 5)
    cfg.setdefault("alert_cooldown_min", 20)
    cfg.setdefault("partners", {})

    return cfg


def _load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text("utf-8"))
        except:
            pass
    return {}


def _save_state(st):
    try:
        STATE_PATH.write_text(json.dumps(st, ensure_ascii=False), "utf-8")
    except:
        pass


# ==============================================
#   ОСНОВНОЙ АНАЛИЗАТОР
# ==============================================

def analyze_wallets(payin_path: str):
    cfg = _load_cfg()
    window_min = cfg["window_minutes"]
    offset_min = cfg["offset_minutes"]
    min_events = cfg["min_events"]
    success_window = cfg["success_window_minutes"]
    cooldown_min = cfg["alert_cooldown_min"]

    logger.info(f"[Analyzer] Загружаю PayIn: {payin_path}")

    try:
        df = pd.read_excel(payin_path)
    except Exception as e:
        send_message_sync(f"⚠️ Не удалось прочитать PayIn: {e}", chat_id=CHAT_ID)
        return

    if df.empty:
        logger.info("[Analyzer] PayIn пуст — выходим")
        return

    # Строго фиксированные колонки
    COL_DT = "Дата/Время создания"
    COL_PARTNER = "Партнер"
    COL_STATUS = "Статус"
    COL_INFO = "Инфо"
    COL_AMOUNT = "Сумма"

    # Парсинг даты
    df["_dt"] = pd.to_datetime(df[COL_DT], format="%d.%m.%Y %H:%M:%S", errors="coerce")
    df["_partner_norm"] = df[COL_PARTNER].astype(str).apply(_normalize)
    df["_status"] = df[COL_STATUS].astype(str)

    now = _now_msk()

    # ------------------------------------------------------
    #  ОКНО АНАЛИЗА (window + offset)
    # ------------------------------------------------------
    end_time = now - timedelta(minutes=offset_min)
    start_time = end_time - timedelta(minutes=window_min)

    df_window = df[(df["_dt"] >= start_time) & (df["_dt"] < end_time)]

    # ------------------------------------------------------
    #  УСПЕХИ ЗА ПОСЛЕДНИЕ success_window МИНУТ
    # ------------------------------------------------------
    start_sw = now - timedelta(minutes=success_window)
    df_success_window = df[(df["_dt"] >= start_sw) & (df["_dt"] <= now)]

    # ------------------------------------------------------
    #  СУТОЧНАЯ СТАТИСТИКА
    # ------------------------------------------------------
    start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    df_today = df[(df["_dt"] >= start_day) & (df["_dt"] <= now)]

    state = _load_state()
    alerts = []
    partners_cfg = cfg["partners"]

    for partner_name, settings in partners_cfg.items():

        key_norm = _normalize(partner_name)
        subset = df_window[df_window["_partner_norm"] == key_norm]
        total = len(subset)

        # анти-шум
        if total < min_events:
            continue

        # успешные
        success = subset["_status"].apply(_status_success).sum()
        conv = success / total if total else 0
        thr = settings.get("threshold", 0.8)

        # конверсия
        if conv < thr:
            text = (
                f"⚠️ Низкая конверсия у *{partner_name}*\n"
                f"Окно: {window_min} мин (смещение {offset_min})\n"
                f"Конверсия: {conv:.1%} ({success}/{total})\n"
                f"Порог: ≥ {thr:.1%}"
            )
            alerts.append((key_norm, text))

        # --------------------------------------------------
        # API CANCEL %
        # --------------------------------------------------
        api_thr = settings.get("api_cancel_threshold")
        api_kw = settings.get("api_cancel_keyword")

        if api_thr is not None and api_kw:
            errors_partner = subset[subset["_status"].apply(lambda s: _normalize(s) == "ошибка")]

            if not errors_partner.empty:
                errors_partner["_info_norm"] = (
                    errors_partner[COL_INFO].astype(str).apply(_normalize)
                )

                kw = _normalize(api_kw)
                cancel_cnt = errors_partner["_info_norm"].apply(lambda x: kw in x).sum()
                total_err = len(errors_partner)
                api_percent = (cancel_cnt / total_err) * 100 if total_err else 0

                if api_percent > api_thr:
                    text = (
                        f"⚠️ Ошибки API у *{partner_name}* превышают порог\n"
                        f"{cancel_cnt}/{total_err} = {api_percent:.1f}%\n"
                        f"Порог: ≤ {api_thr}%\n"
                        f"Тип: «{api_kw}»"
                    )
                    alerts.append((key_norm, text))

        # --------------------------------------------------
        # СУТОЧНЫЙ ЛИМИТ СУММЫ
        # --------------------------------------------------
        day_min = settings.get("daily_min_amount")
        day_max = settings.get("daily_max_amount")

        df_today_p = df_today[df_today["_partner_norm"] == key_norm]
        amount_today = pd.to_numeric(df_today_p[COL_AMOUNT], errors="coerce").sum()

        if day_min is not None and amount_today < day_min:
            text = (
                f"⚠️ Низкие суточные поступления у *{partner_name}*\n"
                f"{amount_today:,.2f} < {day_min:,.2f}"
            )
            alerts.append((key_norm, text))

        if day_max is not None and amount_today > day_max:
            text = (
                f"⚠️ Превышение суточных поступлений у *{partner_name}*\n"
                f"{amount_today:,.2f} > {day_max:,.2f}"
            )
            alerts.append((key_norm, text))


    # =============================================
    #  ОТПРАВКА АЛЕРТОВ (с учётом cooldown)
    # =============================================
    sent = 0
    now_iso = now.isoformat()

    for key_norm, text in alerts:
        last = state.get(key_norm)
        can_send = True

        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if (now - last_dt) < timedelta(minutes=cooldown_min):
                    can_send = False
            except:
                pass

        if can_send:
            send_message_sync(text, chat_id=CHAT_ID)
            state[key_norm] = now_iso
            sent += 1

    _save_state(state)
    logger.info(f"[Analyzer] отправлено алертов: {sent}")
