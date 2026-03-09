# core/config_manager.py
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from core.rules_provider import get_rules_snapshot
from utils.normalization import normalize_partner_name, parse_dt_series_msk


# =============================================================================
# Contract: job_params
# =============================================================================

ALLOWED_VALUE_TYPES = {"str", "int", "float", "bool", "csv", "json"}
ALLOWED_SCOPES = {"global", "job", "partner"}

# Храним ожидаемый value_type как строку, единообразно для всех job
ALLOWED_JOB_PARAMS: Dict[str, Dict[str, str]] = {
    "hourly": {
        "intraday_interval_minutes": "int",
        "final_daily_time": "str",
        "max_comment_length": "int",
        "send_enabled": "bool",
    },
    "wallet": {
        "window_minutes": "int",
        "offset_minutes": "int",
        "min_events": "int",
        "pending_payin_minutes": "int",
        "pending_payout_minutes": "int",
    },
    "ttl_clean": {
        "interval_minutes": "int",
    },
}

# =============================================================================
# Result models
# =============================================================================

@dataclass
class ValidationResult:
    ok: bool
    errors: List[str]       # fatal
    warnings: List[str]     # non-fatal
    df_norm: pd.DataFrame


@dataclass
class _RulesCache:
    stat_key: Optional[Tuple[float, int]] = None  # (mtime, size)
    result: Optional[ValidationResult] = None


@dataclass
class _NotifyState:
    last_sent_ts: float = 0.0
    last_hash: str = ""


# =============================================================================
# job_params
# =============================================================================

def _parse_value(value: Any, value_type: str) -> Any:
    vt = (value_type or "str").strip().lower()

    if value is None:
        if vt == "json":
            return {}
        if vt == "csv":
            return []
        if vt == "bool":
            return False
        return ""

    # pandas NaN
    try:
        if pd.isna(value):
            if vt == "json":
                return {}
            if vt == "csv":
                return []
            if vt == "bool":
                return False
            return ""
    except Exception:
        pass

    v = str(value).strip()

    if vt == "int":
        return int(float(v))

    if vt == "float":
        return float(v)

    if vt == "bool":
        return v.lower() in ("1", "true", "yes", "y", "on")

    if vt == "csv":
        return [x.strip() for x in v.split(",") if x.strip()]

    if vt == "json":
        if not v:
            return {}
        return json.loads(v)

    return v  # str


def validate_job_params(df: pd.DataFrame) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    required = ["id", "enabled", "job", "scope", "scope_value", "key", "value_type", "value"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        errors.append(f"job_params: missing columns: {missing}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings, df_norm=df)

    # normalize
    df["enabled"] = pd.to_numeric(df["enabled"], errors="coerce")
    df["job"] = df["job"].fillna("").astype(str).str.strip().str.lower()
    df["scope"] = df["scope"].fillna("").astype(str).str.strip().str.lower()
    df["scope_value"] = df["scope_value"].fillna("").astype(str).str.strip()
    df["key"] = df["key"].fillna("").astype(str).str.strip()
    df["value_type"] = df["value_type"].fillna("").astype(str).str.strip().str.lower()

    # value не приводим сразу к str, чтобы корректно парсить int/float/json
    if "value" not in df.columns:
        df["value"] = None

    # enabled strict
    bad_enabled = df["enabled"].isna() | ~df["enabled"].isin([0, 1])
    if bad_enabled.any():
        bad_ids = df.loc[bad_enabled, "id"].astype(str).tolist()
        errors.append(
            "job_params: enabled must be 0/1; bad ids: "
            + ", ".join(bad_ids[:30])
            + (" …" if len(bad_ids) > 30 else "")
        )

    # scope strict
    bad_scope = ~df["scope"].isin(ALLOWED_SCOPES)
    if bad_scope.any():
        bad_ids = df.loc[bad_scope, "id"].astype(str).tolist()
        errors.append(
            "job_params: invalid scope (allowed: "
            + f"{sorted(ALLOWED_SCOPES)}); ids: "
            + ", ".join(bad_ids[:30])
            + (" …" if len(bad_ids) > 30 else "")
        )

    # value_type strict
    bad_vt = ~df["value_type"].isin(ALLOWED_VALUE_TYPES)
    if bad_vt.any():
        bad_ids = df.loc[bad_vt, "id"].astype(str).tolist()
        errors.append(
            "job_params: invalid value_type (allowed: "
            + f"{sorted(ALLOWED_VALUE_TYPES)}); ids: "
            + ", ".join(bad_ids[:30])
            + (" …" if len(bad_ids) > 30 else "")
        )

    # required strings
    for c in ["id", "job", "scope", "key", "value_type"]:
        bad = df[c].astype(str).str.strip().eq("")
        if bad.any():
            bad_ids = df.loc[bad, "id"].astype(str).tolist()
            errors.append(
                f"job_params: {c} empty; ids: "
                + ", ".join(bad_ids[:30])
                + (" …" if len(bad_ids) > 30 else "")
            )

    active = df[df["enabled"] == 1].copy()

    # whitelist + strict expected type
    for _, r in active.iterrows():
        job = r["job"]
        key = r["key"]
        row_id = str(r["id"])

        if job not in ALLOWED_JOB_PARAMS:
            errors.append(f"job_params: unknown job '{job}'; id={row_id}")
            continue

        if key not in ALLOWED_JOB_PARAMS[job]:
            errors.append(f"job_params: invalid key '{key}' for job '{job}'; id={row_id}")
            continue

        expected_type = ALLOWED_JOB_PARAMS[job][key]
        actual_type = r["value_type"]

        if actual_type != expected_type:
            errors.append(
                f"job_params: key '{key}' for job '{job}' must have value_type='{expected_type}', "
                f"got '{actual_type}'; id={row_id}"
            )
            continue

        try:
            _ = _parse_value(r["value"], actual_type)
        except Exception as e:
            errors.append(
                f"job_params: parse failed for job='{job}' key='{key}' "
                f"({actual_type}); id={row_id}; err={e}"
            )

    # uniqueness among enabled=1
    if not active.empty:
        dup = (
            active.groupby(["job", "scope", "scope_value", "key"])
            .size()
            .reset_index(name="cnt")
        )
        if (dup["cnt"] > 1).any():
            errors.append("job_params: duplicate active overrides (job/scope/scope_value/key)")

    ok = len(errors) == 0
    return ValidationResult(ok=ok, errors=errors, warnings=warnings, df_norm=df)


def build_job_params_overrides(df: pd.DataFrame) -> Tuple[Dict[str, Any], List[str]]:
    """
    Strict mode:
      - if validation errors -> returns ({}, errors)
      - otherwise returns (overrides, [])
    """
    res = validate_job_params(df)
    if res.errors:
        return {}, res.errors

    overrides: Dict[str, Any] = {}

    active = res.df_norm[res.df_norm["enabled"] == 1].copy()
    for _, r in active.iterrows():
        job = r["job"]
        scope = r["scope"]
        scope_value = r["scope_value"] or "__global__"
        key = r["key"]
        value = _parse_value(r["value"], r["value_type"])

        overrides.setdefault(job, {}).setdefault(scope, {}).setdefault(scope_value, {})[key] = value

    return overrides, []


def get_job_params_df(
    *,
    rules_xlsx_path: str = "",
    sheet_name: str = "job_params",
    force_sync: bool = False,
) -> Optional[pd.DataFrame]:
    path = _get_local_rules_path(force_sync=force_sync)

    try:
        return pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
    except ValueError:
        return None


def get_job_params_overrides(
    *,
    rules_xlsx_path: str = "",
    sheet_name: str = "job_params",
    force_sync: bool = False,
) -> Dict[str, Any]:
    df = get_job_params_df(
        rules_xlsx_path=rules_xlsx_path,
        sheet_name=sheet_name,
        force_sync=force_sync,
    )
    if df is None:
        return {}

    res = _read_sheet_cached(
        cache=_JOB_PARAMS_CACHE,
        path=_get_local_rules_path(force_sync=force_sync),
        sheet_name=sheet_name,
        validator=validate_job_params,
    )
    if res.errors:
        return {}

    overrides, errs = build_job_params_overrides(res.df_norm)
    if errs:
        return {}
    return overrides


def get_job_param(
    overrides: Dict[str, Any],
    *,
    job: str,
    key: str,
    scope: str = "global",
    scope_value: str = "",
    default: Any = None,
) -> Any:
    """
    Priority:
      1) exact scope override (if scope != global)
      2) job-scope override
      3) global override
      4) default
    """
    job = (job or "").strip().lower()
    scope = (scope or "global").strip().lower()
    scope_value = (scope_value or "").strip().lower()

    if not overrides or job not in overrides:
        return default

    if scope != "global":
        v = overrides.get(job, {}).get(scope, {}).get(scope_value, {}).get(key)
        if v is not None:
            return v

    v = overrides.get(job, {}).get("job", {}).get(job, {}).get(key)
    if v is not None:
        return v

    v = overrides.get(job, {}).get("global", {}).get("__global__", {}).get(key)
    if v is not None:
        return v

    return default


def get_job_params(
    *,
    rules_xlsx_path: str = "",
    sheet_name: str = "job_params",
    job: str,
    force_sync: bool = False,
) -> Dict[str, Any]:
    """
    Возвращает параметры для конкретного job.
    Priority:
      scope=<job-specific> > scope=global
    """
    overrides = get_job_params_overrides(
        rules_xlsx_path=rules_xlsx_path,
        sheet_name=sheet_name,
        force_sync=force_sync,
    )
    if not overrides:
        return {}

    job_l = job.strip().lower()

    result: Dict[str, Any] = {}

    # global
    result.update(overrides.get(job_l, {}).get("global", {}).get("__global__", {}))
    # job-specific
    result.update(overrides.get(job_l, {}).get("job", {}).get(job_l, {}))

    return result

# =============================================================================
# Caches
# =============================================================================

_EXCLUDE_TIME_CACHE: Dict[str, _RulesCache] = {}
_WALLET_LIMITS_CACHE: Dict[str, _RulesCache] = {}
_JOB_PARAMS_CACHE: Dict[str, _RulesCache] = {}

_NOTIFY_STATES: Dict[str, _NotifyState] = {
    "exclude.fatal": _NotifyState(),
    "exclude.warn": _NotifyState(),
}


# =============================================================================
# Helpers
# =============================================================================

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_lines(lines: List[str]) -> str:
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _safe_notify(
    notify: Optional[Callable[..., Any]],
    chat_id: Optional[str],
    text: str,
) -> None:
    """
    Supports:
      - notify(text, chat_id=...)
      - notify(text)
    """
    if not notify:
        return

    try:
        if chat_id is not None:
            notify(text, chat_id=chat_id)
        else:
            notify(text)
    except TypeError:
        try:
            notify(text)
        except Exception:
            pass
    except Exception:
        pass


def _maybe_notify(
    *,
    key: str,  # e.g. "exclude.fatal" / "exclude.warn"
    lines: List[str],
    notify: Optional[Callable[..., Any]],
    chat_id: Optional[str],
    cooldown_minutes: int,
    header: str,
) -> None:
    if not lines or not notify:
        return

    state = _NOTIFY_STATES.setdefault(key, _NotifyState())
    msg_hash = _hash_lines(lines)
    now = time.time()
    cooldown_sec = cooldown_minutes * 60

    if msg_hash == state.last_hash and (now - state.last_sent_ts) < cooldown_sec:
        return

    body_lines = lines[:80]
    body = "\n".join(f"- {x}" for x in body_lines)
    if len(lines) > 80:
        body += f"\n… (+{len(lines) - 80} more)"

    msg = f"{header}\n{_utc_now_iso()}\n\n{body}"
    _safe_notify(notify, chat_id, msg)

    state.last_hash = msg_hash
    state.last_sent_ts = now


def _norm_str(x: Any) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    return str(x).strip()


def _get_local_rules_path(force_sync: bool = False) -> Path:
    rs = get_rules_snapshot(force_sync=force_sync)
    return Path(rs.local_path)


def _read_sheet_cached(
    *,
    cache: Dict[str, _RulesCache],
    path: Path,
    sheet_name: str,
    validator: Callable[[pd.DataFrame], ValidationResult],
) -> ValidationResult:
    st = path.stat()
    stat_key = (st.st_mtime, st.st_size)

    c = cache.setdefault(str(path) + "::" + sheet_name, _RulesCache())
    if c.stat_key == stat_key and c.result is not None:
        return c.result

    df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
    res = validator(df)

    c.stat_key = stat_key
    c.result = res
    return res

# =============================================================================
# wallet_limits
# =============================================================================

_REQUIRED_WALLET_LIMITS_COLS = [
    "id",
    "enabled",
    "analyzers",
    "scope",
    "scope_value",
    "limit_type",
    "limit_value",
    "reason",
]


def validate_wallet_limits(df: pd.DataFrame) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in _REQUIRED_WALLET_LIMITS_COLS if c not in df.columns]
    if missing:
        errors.append(f"wallet_limits: missing columns: {missing}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings, df_norm=df)

    # optional columns (for hourly comments / method-specific payout)
    if "method" not in df.columns:
        df["method"] = ""
    if "comment" not in df.columns:
        df["comment"] = ""

    # normalize
    df["enabled"] = pd.to_numeric(df["enabled"], errors="coerce")
    df["analyzers"] = df["analyzers"].astype(str).str.strip()
    df["scope"] = df["scope"].astype(str).str.strip().str.lower()
    df["scope_value"] = df["scope_value"].astype(str).str.strip()
    df["limit_type"] = df["limit_type"].astype(str).str.strip().str.lower()
    df["limit_value"] = pd.to_numeric(df["limit_value"], errors="coerce")

    df["method"] = df["method"].astype(str).fillna("").str.strip().str.upper().replace({"*": ""})
    df["comment"] = df["comment"].astype(str).fillna("").str.strip()

    # analyzers parse
    def _parse_analyzers(s: str) -> List[str]:
        parts = [p.strip().lower() for p in (s or "").split(",")]
        return sorted(set(p for p in parts if p))

    df["_analyzers_list"] = df["analyzers"].map(_parse_analyzers)

    bad_an = df[(df["enabled"] == 1) & (df["_analyzers_list"].map(len) == 0)]
    if not bad_an.empty:
        errors.append("wallet_limits: analyzers empty for enabled=1")

    valid_scopes = {"partner", "group"}
    bad_scope = df[~df["scope"].isin(valid_scopes)]
    if not bad_scope.empty:
        errors.append("wallet_limits: invalid scope (allowed: partner, group)")

    bad_val = df[(df["enabled"] == 1) & df["limit_value"].isna()]
    if not bad_val.empty:
        errors.append("wallet_limits: limit_value empty/non-numeric")

    # uniqueness among active rules, per analyzer + scope + normalized scope_value + limit_type + method
    active = df[df["enabled"] == 1].copy()
    if not active.empty:
        ex = active.explode("_analyzers_list").rename(columns={"_analyzers_list": "analyzer"})

        # build scope_value_key
        ex["scope_value_key"] = ex["scope_value"]
        is_partner = ex["scope"] == "partner"
        ex.loc[is_partner, "scope_value_key"] = ex.loc[is_partner, "scope_value"].map(normalize_partner_name)
        ex.loc[~is_partner, "scope_value_key"] = ex.loc[~is_partner, "scope_value"].astype(str).str.strip().str.lower()

        dup = (
            ex.groupby(["analyzer", "scope", "scope_value_key", "limit_type", "method"])
            .size()
            .reset_index(name="cnt")
        )
        if (dup["cnt"] > 1).any():
            errors.append("wallet_limits: duplicate active rules for same key (per analyzer/scope/method)")

    ok = len(errors) == 0
    return ValidationResult(ok=ok, errors=errors, warnings=warnings, df_norm=df)


def get_wallet_limits_df(
    *,
    rules_xlsx_path: str = "",
    sheet_name: str = "wallet_limits",
    force_sync: bool = False,
) -> pd.DataFrame:
    path = _get_local_rules_path(force_sync=force_sync)

    res = _read_sheet_cached(
        cache=_WALLET_LIMITS_CACHE,
        path=path,
        sheet_name=sheet_name,
        validator=validate_wallet_limits,
    )

    if res.errors:
        raise RuntimeError("rules.xlsx validation fatal errors (wallet_limits)")

    return res.df_norm



# =============================================================================
# thresholds_partner
# =============================================================================

_THRESHOLDS_PARTNER_CACHE: Dict[str, _RulesCache] = {}

_REQUIRED_THRESHOLDS_PARTNER_COLS = [
    "id",
    "enabled",
    "analyzer",
    "partner",
    "metric",
    "threshold_min",
    "threshold_max",
    "min_events",
    "reason",
]

_THR_ID_RE = re.compile(r"^THR-\d{5}$")


def validate_thresholds_partner(df: pd.DataFrame) -> ValidationResult:
    """Contract:
    - conversion_rate is stored in threshold_min (percent, e.g. 50, 35)
    - api_cancel_rate is stored in threshold_max (percent)
    - enabled must be 0/1
    - min_events default 0
    """
    errors: List[str] = []
    warnings: List[str] = []

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in _REQUIRED_THRESHOLDS_PARTNER_COLS if c not in df.columns]
    if missing:
        errors.append(f"thresholds_partner: missing columns: {missing}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings, df_norm=df)

    # normalize text
    for c in ["id", "analyzer", "partner", "metric", "reason"]:
        df[c] = df[c].map(_norm_str)

    df["enabled"] = pd.to_numeric(df["enabled"], errors="coerce")

    # numeric thresholds
    for c in ["threshold_min", "threshold_max", "min_events"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # enabled strict 0/1
    bad_enabled = ~df["enabled"].isin([0, 1])
    if bad_enabled.any():
        bad_ids = df.loc[bad_enabled, "id"].tolist()
        errors.append(
            "thresholds_partner: enabled must be 0/1; bad ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    # id format (warning only, don't block if legacy ids exist)
    bad_id = ~df["id"].astype(str).str.match(_THR_ID_RE)
    if bad_id.any():
        bad_ids = df.loc[bad_id, "id"].tolist()
        warnings.append(
            "thresholds_partner: id not in THR-00000 format; ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    # partner must not be empty when enabled=1
    bad_partner = (df["enabled"] == 1) & (df["partner"].astype(str).str.strip() == "")
    if bad_partner.any():
        bad_ids = df.loc[bad_partner, "id"].tolist()
        errors.append(
            "thresholds_partner: partner empty; ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    # metric must not be empty when enabled=1
    bad_metric = (df["enabled"] == 1) & (df["metric"].astype(str).str.strip() == "")
    if bad_metric.any():
        bad_ids = df.loc[bad_metric, "id"].tolist()
        errors.append(
            "thresholds_partner: metric empty; ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    # min_events: default 0 if empty; must be >= 0
    df["min_events"] = df["min_events"].fillna(0)
    bad_min_events = (df["enabled"] == 1) & (df["min_events"] < 0)
    if bad_min_events.any():
        bad_ids = df.loc[bad_min_events, "id"].tolist()
        errors.append(
            "thresholds_partner: min_events must be >= 0; ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    # percent metrics rules
    # conversion_rate must be present in threshold_min, api_cancel_rate in threshold_max
    is_conv = (df["enabled"] == 1) & (df["metric"].str.lower() == "conversion_rate")
    if is_conv.any():
        bad = df.loc[is_conv, "threshold_min"].isna()
        if bad.any():
            bad_ids = df.loc[is_conv & bad, "id"].tolist()
            errors.append(
                "thresholds_partner: conversion_rate requires threshold_min (percent); ids: "
                + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
            )

    is_api = (df["enabled"] == 1) & (df["metric"].str.lower() == "api_cancel_rate")
    if is_api.any():
        bad = df.loc[is_api, "threshold_max"].isna()
        if bad.any():
            bad_ids = df.loc[is_api & bad, "id"].tolist()
            errors.append(
                "thresholds_partner: api_cancel_rate requires threshold_max (percent); ids: "
                + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
            )

    active = df[df["enabled"] == 1].copy()
    if not active.empty:
        dup = (
            active.groupby(["analyzer", "partner", "metric"])
            .size()
            .reset_index(name="cnt")
        )
        if (dup["cnt"] > 1).any():
            errors.append("thresholds_partner: duplicate active rules for same analyzer/partner/metric")

    ok = len(errors) == 0
    return ValidationResult(ok=ok, errors=errors, warnings=warnings, df_norm=df)

def get_thresholds_partner_df(
    *,
    rules_xlsx_path: str = "",
    sheet_name: str = "thresholds_partner",
    force_sync: bool = False,
) -> pd.DataFrame:
    path = _get_local_rules_path(force_sync=force_sync)

    res = _read_sheet_cached(
        cache=_THRESHOLDS_PARTNER_CACHE,
        path=path,
        sheet_name=sheet_name,
        validator=validate_thresholds_partner,
    )

    if res.errors:
        raise RuntimeError("rules.xlsx validation fatal errors (thresholds_partner)")

    return res.df_norm

# =============================================================================
# partner_groups
# =============================================================================

_PARTNER_GROUPS_CACHE: Dict[str, _RulesCache] = {}

_REQUIRED_PARTNER_GROUPS_COLS = [
    "id",
    "enabled",
    "analyzers",
    "group_name",
    "partner",
]


def validate_partner_groups(df: pd.DataFrame) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in _REQUIRED_PARTNER_GROUPS_COLS if c not in df.columns]
    if missing:
        errors.append(f"partner_groups: missing columns: {missing}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings, df_norm=df)

    df["enabled"] = pd.to_numeric(df["enabled"], errors="coerce")

    for c in ["id", "analyzers", "group_name", "partner"]:
        df[c] = df[c].map(_norm_str)

    def _parse_analyzers(s: str) -> List[str]:
        parts = [p.strip().lower() for p in (s or "").split(",")]
        return sorted(set(p for p in parts if p))

    df["_analyzers_list"] = df["analyzers"].map(_parse_analyzers)

    bad_an = (df["enabled"] == 1) & (df["_analyzers_list"].map(len) == 0)
    if bad_an.any():
        bad_ids = df.loc[bad_an, "id"].tolist()
        errors.append(
            "partner_groups: analyzers empty/unparseable; ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    bad_enabled = ~df["enabled"].isin([0, 1])
    if bad_enabled.any():
        bad_ids = df.loc[bad_enabled, "id"].tolist()
        errors.append(
            "partner_groups: enabled must be 0/1; ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    bad_partner = (df["enabled"] == 1) & (df["partner"].astype(str).str.strip() == "")
    if bad_partner.any():
        bad_ids = df.loc[bad_partner, "id"].tolist()
        errors.append(
            "partner_groups: partner empty; ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    bad_group = (df["enabled"] == 1) & (df["group_name"].astype(str).str.strip() == "")
    if bad_group.any():
        bad_ids = df.loc[bad_group, "id"].tolist()
        errors.append(
            "partner_groups: group_name empty; ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    ok = len(errors) == 0
    return ValidationResult(ok=ok, errors=errors, warnings=warnings, df_norm=df)


def get_partner_groups_df(
    *,
    rules_xlsx_path: str = "",
    sheet_name: str = "partner_groups",
    force_sync: bool = False,
) -> pd.DataFrame:
    path = _get_local_rules_path(force_sync=force_sync)

    res = _read_sheet_cached(
        cache=_PARTNER_GROUPS_CACHE,
        path=path,
        sheet_name=sheet_name,
        validator=validate_partner_groups,
    )

    if res.errors:
        raise RuntimeError("rules.xlsx validation fatal errors (partner_groups)")

    return res.df_norm


# =============================================================================
# exclude_time
# =============================================================================

_REQUIRED_EXCLUDE_COLS = [
    "id",
    "enabled",
    "analyzers",
    "partner",
    "start_dt",
    "end_dt",
    "reason",
    "created_by",
    "created_at",
]

_ID_RE = re.compile(r"^EXC-\d{5}$")


def validate_exclude_time(df: pd.DataFrame) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in _REQUIRED_EXCLUDE_COLS if c not in df.columns]
    if missing:
        errors.append(f"exclude_time: missing columns: {missing}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings, df_norm=df)

    for c in ["id", "analyzers", "partner", "reason", "created_by"]:
        df[c] = df[c].map(_norm_str)

    def _parse_analyzers(s: str) -> List[str]:
        parts = [p.strip().lower() for p in (s or "").split(",")]
        return sorted(set(p for p in parts if p))

    df["_analyzers_list"] = df["analyzers"].map(_parse_analyzers)

    bad = df["_analyzers_list"].map(len) == 0
    if bad.any():
        bad_ids = df.loc[bad, "id"].tolist()
        errors.append(
            "exclude_time: analyzers empty/unparseable; ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    # enabled strict 0/1
    def _parse_enabled(v: Any) -> Optional[int]:
        if v is None:
            return None
        try:
            if pd.isna(v):
                return None
        except Exception:
            pass
        try:
            return int(v)
        except Exception:
            return None

    df["enabled"] = df["enabled"].map(_parse_enabled)
    bad_enabled = df["enabled"].isna() | ~df["enabled"].isin([0, 1])
    if bad_enabled.any():
        bad_ids = df.loc[bad_enabled, "id"].tolist()
        errors.append(
            "exclude_time: enabled must be 0/1; bad ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    # id format + uniqueness
    bad_fmt = ~df["id"].map(lambda s: bool(_ID_RE.match(s)))
    if bad_fmt.any():
        bad_ids = df.loc[bad_fmt, "id"].tolist()
        errors.append(
            "exclude_time: bad id format (expected EXC-00000); bad ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    dup = df["id"].duplicated(keep=False)
    if dup.any():
        dups = sorted(set(df.loc[dup, "id"].tolist()))
        errors.append("exclude_time: duplicate id(s): " + (", ".join(dups[:30]) + (" …" if len(dups) > 30 else "")))

    # dates parsing
    for c in ["start_dt", "end_dt", "created_at"]:
        df[c] = parse_dt_series_msk(df[c])

    for c in ["start_dt", "end_dt", "created_at"]:
        bad_dt = df[c].isna()
        if bad_dt.any():
            bad_ids = df.loc[bad_dt, "id"].tolist()
            errors.append(
                f"exclude_time: {c} not parseable to datetime; ids: "
                + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
            )

    ok_dates = df["start_dt"].notna() & df["end_dt"].notna()
    bad_range = ok_dates & (df["start_dt"] >= df["end_dt"])
    if bad_range.any():
        bad_ids = df.loc[bad_range, "id"].tolist()
        errors.append(
            "exclude_time: start_dt >= end_dt; ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    # warnings: overlaps
    active = df[(df["enabled"] == 1) & df["start_dt"].notna() & df["end_dt"].notna()].copy()
    if not active.empty:
        for analyzer_key in sorted({a for lst in active["_analyzers_list"] for a in lst}):
            sub = active[active["_analyzers_list"].map(lambda lst: analyzer_key in lst)]
            for partner, g in sub.groupby("partner", dropna=False):
                g = g.sort_values("start_dt")
                prev_end = None
                prev_id = None
                for _, row in g.iterrows():
                    if prev_end is not None and row["start_dt"] < prev_end:
                        warnings.append(
                            f"exclude_time overlap (WARNING): analyzer={analyzer_key}, partner={partner}: "
                            f"{prev_id} overlaps {row['id']}"
                        )
                    if prev_end is None or row["end_dt"] > prev_end:
                        prev_end = row["end_dt"]
                        prev_id = row["id"]

    # warnings: gaps
    try:
        nums = sorted(int(x.split("-")[1]) for x in df["id"] if _ID_RE.match(x))
        if nums:
            expected = set(range(nums[0], nums[-1] + 1))
            missing_nums = sorted(expected - set(nums))
            if missing_nums:
                warnings.append(f"exclude_time id sequence gaps (WARNING): count={len(missing_nums)}, e.g. {missing_nums[:10]}")
    except Exception:
        pass

    ok = len(errors) == 0
    return ValidationResult(ok=ok, errors=errors, warnings=warnings, df_norm=df)


def get_exclude_time_df(
    *,
    rules_xlsx_path: str = "",
    sheet_name: str = "exclude_time",
    notify: Optional[Callable[..., Any]] = None,
    chat_id: Optional[str] = None,
    cooldown_minutes: int = 60,
    force_sync: bool = False,
) -> pd.DataFrame:
    """
    Cached load + validation.
    Fatal -> notify (throttled) + raise
    Warning -> notify (throttled) + return df_norm
    """
    path = _get_local_rules_path(force_sync=force_sync)

    res = _read_sheet_cached(
        cache=_EXCLUDE_TIME_CACHE,
        path=path,
        sheet_name=sheet_name,
        validator=validate_exclude_time,
    )

    if res.errors:
        _maybe_notify(
            key="exclude.fatal",
            lines=res.errors,
            notify=notify,
            chat_id=chat_id,
            cooldown_minutes=cooldown_minutes,
            header="❌ rules.xlsx validation FAILED (exclude_time)",
        )
        raise RuntimeError("rules.xlsx validation fatal errors (exclude_time)")

    if res.warnings:
        _maybe_notify(
            key="exclude.warn",
            lines=res.warnings,
            notify=notify,
            chat_id=chat_id,
            cooldown_minutes=cooldown_minutes,
            header="⚠️ rules.xlsx validation warnings (exclude_time)",
        )

    return res.df_norm


def clear_rules_caches() -> None:
    _EXCLUDE_TIME_CACHE.clear()
    _WALLET_LIMITS_CACHE.clear()
    _JOB_PARAMS_CACHE.clear()
    _THRESHOLDS_PARTNER_CACHE.clear()
    _PARTNER_GROUPS_CACHE.clear()
    _NOTIFY_STATES["exclude.fatal"] = _NotifyState()
    _NOTIFY_STATES["exclude.warn"] = _NotifyState()
# =============================================================================
# rules_validate (aggregate)
# =============================================================================

def validate_access(df: pd.DataFrame) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    required = ["chat_id", "user_id", "level"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        errors.append(f"access: missing columns: {missing}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings, df_norm=df)

    if "enabled" not in df.columns:
        df["enabled"] = 1

    df["enabled"] = pd.to_numeric(df["enabled"], errors="coerce")
    bad_enabled = df["enabled"].isna() | ~df["enabled"].isin([0, 1])
    if bad_enabled.any():
        errors.append("access: enabled must be 0/1")

    for c in ["chat_id", "user_id", "level"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        if df[c].isna().any():
            errors.append(f"access: {c} must be numeric (non-empty)")

    ok = len(errors) == 0
    return ValidationResult(ok=ok, errors=errors, warnings=warnings, df_norm=df)


def validate_commands(df: pd.DataFrame) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    required = ["command", "required_level", "allow_private", "allow_groups"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        errors.append(f"commands: missing columns: {missing}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings, df_norm=df)

    if "enabled" not in df.columns:
        df["enabled"] = 1

    df["enabled"] = pd.to_numeric(df["enabled"], errors="coerce")
    for c in ["required_level", "allow_private", "allow_groups"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    if (~df["enabled"].isin([0, 1])).any():
        errors.append("commands: enabled must be 0/1")
    if df["command"].astype(str).str.strip().eq("").any():
        errors.append("commands: command empty")
    if df["required_level"].isna().any():
        errors.append("commands: required_level empty/non-numeric")
    if (~df["allow_private"].isin([0, 1])).any():
        errors.append("commands: allow_private must be 0/1")
    if (~df["allow_groups"].isin([0, 1])).any():
        errors.append("commands: allow_groups must be 0/1")

    # uniqueness among enabled=1
    active = df[df["enabled"] == 1].copy()
    if not active.empty and active["command"].duplicated().any():
        errors.append("commands: duplicate command among enabled=1")

    ok = len(errors) == 0
    return ValidationResult(ok=ok, errors=errors, warnings=warnings, df_norm=df)


def validate_schedules(df: pd.DataFrame) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    required = ["id", "enabled", "job_type", "schedule_type", "every_seconds", "cron", "jitter_sec", "max_runtime_sec", "coalesce"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        errors.append(f"schedules: missing columns: {missing}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings, df_norm=df)

    for c in ["enabled", "every_seconds", "jitter_sec", "max_runtime_sec", "coalesce"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    if (~df["enabled"].isin([0, 1])).any():
        errors.append("schedules: enabled must be 0/1")
    if df["job_type"].astype(str).str.strip().eq("").any():
        errors.append("schedules: job_type empty")
    if df["schedule_type"].astype(str).str.strip().eq("").any():
        errors.append("schedules: schedule_type empty")
    if (~df["coalesce"].isin([0, 1])).any():
        errors.append("schedules: coalesce must be 0/1")

    # basic sanity: at least one schedule field set when enabled=1
    active = df[df["enabled"] == 1].copy()
    if not active.empty:
        st = active["schedule_type"].astype(str).str.strip().str.lower()
        bad = []
        for i, r in active.iterrows():
            typ = str(r["schedule_type"]).strip().lower()
            if typ in ("every_seconds", "interval", "seconds"):
                if pd.isna(r["every_seconds"]) or int(r["every_seconds"]) <= 0:
                    bad.append(str(r["id"]))
            elif typ in ("cron",):
                if str(r["cron"]).strip() == "":
                    bad.append(str(r["id"]))
            # else: allow custom types (warn only)
            else:
                warnings.append(f"schedules: unknown schedule_type='{typ}' (id={r['id']})")

        if bad:
            errors.append("schedules: bad schedule config for ids: " + ", ".join(bad[:30]) + (" …" if len(bad) > 30 else ""))

    ok = len(errors) == 0
    return ValidationResult(ok=ok, errors=errors, warnings=warnings, df_norm=df)


def rules_validate_all(*, force_sync: bool = True) -> Tuple[List[str], List[str]]:
    """
    Returns (errors, warnings).
    errors => fatal
    warnings => non-fatal
    """
    path = _get_local_rules_path(force_sync=force_sync)

    validators = [
        ("access", validate_access),
        ("commands", validate_commands),
        ("schedules", validate_schedules),
        ("job_params", validate_job_params),
        ("wallet_limits", validate_wallet_limits),
        ("thresholds_partner", validate_thresholds_partner),
        ("partner_groups", validate_partner_groups),
        ("exclude_time", validate_exclude_time),
    ]

    errors: List[str] = []
    warnings: List[str] = []

    for sheet, fn in validators:
        try:
            df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
        except ValueError:
            errors.append(f"{sheet}: sheet missing")
            continue
        except Exception as e:
            errors.append(f"{sheet}: failed to read sheet: {e}")
            continue

        try:
            res = fn(df)
            errors.extend([f"{sheet}: {x}" for x in res.errors])
            warnings.extend([f"{sheet}: {x}" for x in res.warnings])
        except Exception as e:
            errors.append(f"{sheet}: validator crashed: {e}")

    return errors, warnings

def get_ui_layout_df(*, force_sync: bool = False) -> pd.DataFrame:
    path = _get_local_rules_path(force_sync=force_sync)
    df = pd.read_excel(path, sheet_name="ui_layout", engine="openpyxl")

    # normalize
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    for c in ["id", "view", "section", "key", "title", "group_code", "style", "notes"]:
        if c not in df.columns:
            df[c] = ""
    if "enabled" not in df.columns:
        df["enabled"] = 1
    if "order" not in df.columns:
        df["order"] = 0

    df["enabled"] = pd.to_numeric(df["enabled"], errors="coerce").fillna(0).astype(int)
    df["order"] = pd.to_numeric(df["order"], errors="coerce").fillna(0).astype(int)

    return df

# =============================================================================
# Hourly rules
# =============================================================================

def get_hourly_payins_df(*, force_sync: bool = False) -> pd.DataFrame:
    path = _get_local_rules_path(force_sync=force_sync)
    df = pd.read_excel(path, sheet_name="hourly_payins", engine="openpyxl")
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def get_hourly_payouts_df(*, force_sync: bool = False) -> pd.DataFrame:
    path = _get_local_rules_path(force_sync=force_sync)
    df = pd.read_excel(path, sheet_name="hourly_payouts", engine="openpyxl")
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def get_hourly_payout_methods_df(*, force_sync: bool = False) -> pd.DataFrame:
    path = _get_local_rules_path(force_sync=force_sync)
    df = pd.read_excel(path, sheet_name="hourly_payout_methods", engine="openpyxl")
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df