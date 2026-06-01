"""Unit tests for Raccoon conversion monitor (fixed operation-key dedup)."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:ABCDEF-test-token-for-unittest")
os.environ.setdefault("TELEGRAM_CHAT_ID_HOURLY_RACCOON", "-payin-10m-chat")
os.environ.setdefault("TELEGRAM_CHAT_ID_RACCOON_WALLET", "-hourly-wallet-chat")

from analyzers import raccoon_hourly_report as mod
from analyzers.raccoon_hourly_report import (
    CONVERSION_ALERT_CHAT_ENV,
    CONVERSION_DROP_PP,
    CONVERSION_MIN_OPS,
    PAYIN_REPORT_CHAT_ENV,
    build_conversion_facts,
    load_partner_conversion_thresholds,
    run_conversion_monitor,
    run_hourly_report,
)
from utils.normalization import normalize_partner_name

MSK = ZoneInfo("Europe/Moscow")
BASE_DT = datetime(2026, 5, 20, 12, 0, tzinfo=MSK)
NOW = datetime(2026, 5, 20, 14, 30, tzinfo=MSK)


def _conv_df(rows: list[dict]) -> pd.DataFrame:
    out = []
    for i, r in enumerate(rows):
        partner = r["Партнер"]
        dt = r.get("Дата/Время создания", BASE_DT + timedelta(minutes=i))
        out.append({
            "Партнер": partner,
            "norm": r.get("norm", normalize_partner_name(partner)),
            "Статус": r["Статус"],
            "Сумма": r.get("Сумма", 0),
            "Дата/Время создания": dt,
        })
    return pd.DataFrame(out)


def _rows_last10(paid: int, failed: int, *, partner="A (1)", dt_start=None) -> list[dict]:
    dt_start = dt_start or BASE_DT
    rows = []
    for i in range(paid):
        rows.append({
            "Партнер": partner,
            "Статус": "оплачен",
            "Дата/Время создания": dt_start + timedelta(minutes=100 + i),
        })
    for i in range(failed):
        rows.append({
            "Партнер": partner,
            "Статус": "ошибка",
            "Дата/Время создания": dt_start + timedelta(minutes=200 + i),
        })
    return rows


@pytest.fixture(autouse=True)
def isolated_conversion_state(monkeypatch: pytest.MonkeyPatch):
    td = tempfile.mkdtemp()
    monkeypatch.setattr(mod, "WARN_DEDUP_PATH", os.path.join(td, "conversion_warn_dedup.json"))
    monkeypatch.setattr(mod, "CONVERSION_ALERT_STATE_PATH", os.path.join(td, "conversion_alert_state.json"))


@pytest.fixture
def tmp_state(monkeypatch: pytest.MonkeyPatch):
    td = tempfile.mkdtemp()
    warn_path = os.path.join(td, "conversion_warn_dedup.json")
    alert_path = os.path.join(td, "conversion_alert_state.json")
    monkeypatch.setattr(mod, "WARN_DEDUP_PATH", warn_path)
    monkeypatch.setattr(mod, "CONVERSION_ALERT_STATE_PATH", alert_path)
    return {"tmpdir": td, "warn": warn_path, "alert": alert_path}


def _run_monitor(df, thresholds, sent: list, *, now=NOW, tmp_state=None):
    with patch.object(mod, "send_message_sync", side_effect=lambda t, chat_id=None: sent.append((t, chat_id))):
        with patch.object(mod, "load_partner_conversion_thresholds", return_value=thresholds):
            run_conversion_monitor(df, now=now)


def _warn_messages(sent: list) -> list:
    return [m for m, _ in sent if "Нет порога конверсии" in m]


def test_rolling_conversion_uses_last_10_operations():
    norm = normalize_partner_name("A (1)")
    old = [{"Партнер": "A (1)", "Статус": "оплачен",
            "Дата/Время создания": BASE_DT + timedelta(minutes=i)} for i in range(10)]
    recent = _rows_last10(2, 8, dt_start=BASE_DT + timedelta(hours=1))
    facts = build_conversion_facts(_conv_df(old + recent))
    fact = next(f for f in facts if f["norm"] == norm)
    assert fact["rolling_ready"]
    assert fact["window_non_pending_count"] == 20
    assert fact["conversion_pct"] == pytest.approx(20.0)

    sent: list = []
    _run_monitor(_conv_df(old + recent), {norm: 70.0}, sent)
    assert len(sent) == 1
    assert "последних 10" in sent[0][0]


def test_missing_threshold_case_a_same_ops_next_hour_no_warn(tmp_state):
    """A: 18:00 warning → 19:00 same ops → 0 warnings."""
    rows = [{"Партнер": "B (2)", "Статус": "оплачен", "Сумма": 10,
             "Дата/Время создания": BASE_DT + timedelta(minutes=i)} for i in range(2)]
    df = _conv_df(rows)
    t18 = NOW.replace(hour=18, minute=0, second=0, microsecond=0)
    sent: list = []
    _run_monitor(df, {}, sent, now=t18, tmp_state=tmp_state)
    assert len(_warn_messages(sent)) == 1

    sent.clear()
    _run_monitor(df, {}, sent, now=t18 + timedelta(hours=1), tmp_state=tmp_state)
    assert _warn_messages(sent) == []


def test_missing_threshold_case_b_new_op_next_hour_warns(tmp_state):
    """B: 18:00 warning → 19:00 new op → 1 warning."""
    rows = [{"Партнер": "B (2)", "Статус": "оплачен", "Сумма": 10,
             "Дата/Время создания": BASE_DT + timedelta(minutes=i)} for i in range(2)]
    df = _conv_df(rows)
    t18 = NOW.replace(hour=18, minute=0, second=0, microsecond=0)
    sent: list = []
    _run_monitor(df, {}, sent, now=t18, tmp_state=tmp_state)
    assert len(_warn_messages(sent)) == 1

    sent.clear()
    t19 = t18 + timedelta(hours=1)
    rows_with_new = rows + [{
        "Партнер": "B (2)",
        "Статус": "оплачен",
        "Сумма": 99,
        "Дата/Время создания": t19 + timedelta(minutes=5),
    }]
    _run_monitor(_conv_df(rows_with_new), {}, sent, now=t19, tmp_state=tmp_state)
    assert len(_warn_messages(sent)) == 1


def test_missing_threshold_case_c_new_op_same_hour_no_warn(tmp_state):
    """C: 18:00 warning → 18:20 new op → 0 warnings (hourly cap)."""
    rows = [{"Партнер": "B (2)", "Статус": "оплачен", "Сумма": 10,
             "Дата/Время создания": BASE_DT + timedelta(minutes=i)} for i in range(2)]
    df = _conv_df(rows)
    t18 = NOW.replace(hour=18, minute=0, second=0, microsecond=0)
    sent: list = []
    _run_monitor(df, {}, sent, now=t18, tmp_state=tmp_state)
    assert len(_warn_messages(sent)) == 1

    sent.clear()
    rows_with_new = rows + [{
        "Партнер": "B (2)",
        "Статус": "оплачен",
        "Сумма": 50,
        "Дата/Время создания": t18 + timedelta(minutes=20),
    }]
    _run_monitor(_conv_df(rows_with_new), {}, sent, now=t18 + timedelta(minutes=20), tmp_state=tmp_state)
    assert _warn_messages(sent) == []


def test_conversion_drop_alert_dedup_on_same_operations(tmp_state):
    """D: same operations repeated → no duplicate alert."""
    norm = normalize_partner_name("A (1)")
    df = _conv_df(_rows_last10(3, 7))
    sent: list = []
    with patch.object(mod, "send_message_sync", side_effect=lambda t, chat_id=None: sent.append(t)):
        with patch.object(mod, "load_partner_conversion_thresholds", return_value={norm: 70.0}):
            run_conversion_monitor(df, now=NOW)
            run_conversion_monitor(df, now=NOW)
    assert len(sent) == 1


def test_state_merge_warn_and_alert_keys_not_overwritten(tmp_state):
    """E: updating last_warn_operation_keys must not erase last_alert_operation_keys."""
    norm = normalize_partner_name("B (2)")
    alert_path = tmp_state["alert"]

    with open(alert_path, "w", encoding="utf-8") as f:
        json.dump({
            "version": mod.CONVERSION_ALERT_STATE_VERSION,
            "day": str(NOW.date()),
            "partners": {
                norm: {
                    "last_alert_operation_keys": ["existing-alert-key"],
                },
            },
        }, f)

    rows = [{"Партнер": "B (2)", "Статус": "оплачен", "Сумма": 10,
             "Дата/Время создания": BASE_DT}]
    sent: list = []
    _run_monitor(_conv_df(rows), {}, sent, now=NOW, tmp_state=tmp_state)

    with open(alert_path, encoding="utf-8") as f:
        state = json.load(f)
    partner_state = state["partners"][norm]
    assert "last_warn_operation_keys" in partner_state
    assert partner_state["last_alert_operation_keys"] == ["existing-alert-key"]


def test_missing_threshold_state_stores_last_warn_operation_keys(tmp_state):
    norm = normalize_partner_name("B (2)")
    rows = [{"Партнер": "B (2)", "Статус": "оплачен", "Сумма": 10,
             "Дата/Время создания": BASE_DT}]
    sent: list = []
    _run_monitor(_conv_df(rows), {}, sent, now=NOW, tmp_state=tmp_state)

    with open(tmp_state["alert"], encoding="utf-8") as f:
        state = json.load(f)
    keys = state["partners"][norm]["last_warn_operation_keys"]
    assert len(keys) == 1
    assert "last_alert_operation_keys" not in state["partners"][norm]


def test_conversion_alert_uses_wallet_chat_id(tmp_state):
    norm = normalize_partner_name("A (1)")
    df = _conv_df(_rows_last10(3, 7))
    sent: list = []
    _run_monitor(df, {norm: 70.0}, sent, tmp_state=tmp_state)
    alert_calls = [c for c in sent if "Падение конверсии" in c[0]]
    assert len(alert_calls) == 1
    assert alert_calls[0][1] == os.environ[CONVERSION_ALERT_CHAT_ENV]
    assert alert_calls[0][1] != os.environ[PAYIN_REPORT_CHAT_ENV]


def test_missing_threshold_warning_uses_wallet_chat_id(tmp_state):
    rows = [{"Партнер": "B (2)", "Статус": "оплачен",
             "Дата/Время создания": BASE_DT + timedelta(minutes=i)} for i in range(3)]
    sent: list = []
    _run_monitor(_conv_df(rows), {}, sent, tmp_state=tmp_state)
    warn_calls = [c for c in sent if "Нет порога конверсии" in c[0]]
    assert len(warn_calls) == 1
    assert warn_calls[0][1] == os.environ[CONVERSION_ALERT_CHAT_ENV]
    assert warn_calls[0][1] != os.environ[PAYIN_REPORT_CHAT_ENV]


def test_payin_report_uses_hourly_raccoon_chat_id():
    paid_rows = [
        {"Партнер": "A (1)", "Сумма": 100, "Статус": "оплачен",
         "Дата/Время создания": BASE_DT + timedelta(minutes=i)}
        for i in range(2)
    ]
    window = _conv_df(paid_rows)
    sent: list = []

    def _capture(text, chat_id=None):
        sent.append((text, chat_id))

    with patch.object(mod, "send_message_sync", side_effect=_capture):
        with patch.object(mod, "prepare_data", return_value=window):
            with patch.object(mod, "_load_last_state", return_value={}):
                with patch.object(mod, "_save_last_state"):
                    with patch.object(mod, "_calc_fingerprint", return_value={"hash": "new"}):
                        with patch.object(mod, "load_cfg", return_value={"payin": {}, "payin_groups": {}, "payin_layout": []}):
                            with patch.object(mod, "aggregate_payin", return_value=[]):
                                run_hourly_report()

    report_calls = [c for c in sent if "Итого поступления" in c[0]]
    assert len(report_calls) == 1
    assert report_calls[0][1] == os.environ[PAYIN_REPORT_CHAT_ENV]


def test_load_thresholds_uses_rules_snapshot():
    snap = MagicMock()
    snap.local_path = "dummy_rules.xlsx"
    df_rules = pd.DataFrame([
        {
            "enabled": 1,
            "analyzer": "raccoon_wallet",
            "partner": "Cat.Casino (207)",
            "metric": "conversion_rate",
            "threshold_min": 75,
        },
    ])
    with patch.object(mod, "get_rules_snapshot", return_value=snap) as gs:
        with patch.object(mod.pd, "read_excel", return_value=df_rules):
            out = load_partner_conversion_thresholds()
    gs.assert_called_once_with(force_sync=False)
    assert out[normalize_partner_name("Cat.Casino (207)")] == 75.0


def test_drop_is_percentage_points():
    assert CONVERSION_DROP_PP == 20
    threshold = 70
    assert 50 <= threshold - CONVERSION_DROP_PP
    assert not (51 <= threshold - CONVERSION_DROP_PP)


def test_raccoon_hourly_job_chain_calls_monitor():
    import integrations.raccoon_jobs as jobs

    with patch.object(jobs, "run_hourly_raccoon_cycle") as dl:
        with patch.object(jobs, "run_conversion_monitor_from_payin") as monitor:
            with patch.object(jobs, "run_raccoon_hourly_report_fn") as report:
                jobs.run_raccoon_hourly_job()
    dl.assert_called_once()
    monitor.assert_called_once()
    report.assert_called_once()
