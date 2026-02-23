# core/config_manager.py
from __future__ import annotations
import pandas as pd
import os
import re
import time

import hashlib

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Tuple, Dict, List
from core.rules_provider import get_rules_snapshot


# =============================================================================
# Public API (what you call from analyzers)
# =============================================================================
#
# exclude_df = get_exclude_time_df(
#     rules_xlsx_path=os.getenv("RULES_XLSX_PATH"),
#     notify=send_message_sync,   # optional
#     chat_id=CHAT_ID,            # optional
# )
#
# - Caches by (mtime, size): if unchanged -> DOES NOT read Excel
# - On change -> waits file stable (Dropbox), loads sheet, validates & normalizes
# - Fatal -> (throttled) notify + raise RuntimeError
# - Warning -> (throttled) notify + returns df_norm
#
# =============================================================================


# ---------- Result models ----------

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


# ---------- Module-level cache (simple & effective for single-process) ----------

_EXCLUDE_TIME_CACHE: Dict[str, _RulesCache] = {}         # path -> cache
_NOTIFY_STATES: Dict[str, _NotifyState] = {              # keyed by "exclude.fatal"/"exclude.warn"
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
            notify(text, chat_id=chat_id)  # send_message_sync-style
        else:
            notify(text)
    except TypeError:
        # fallback: maybe notify only accepts 1 positional
        try:
            notify(text)
        except Exception:
            pass
    except Exception:
        # never let notification crash the analyzer
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
    """
    Anti-spam:
      - send immediately if content hash changed
      - else send at most once per cooldown_minutes
    """
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


# =============================================================================
# Validation: exclude_time
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
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in _REQUIRED_WALLET_LIMITS_COLS if c not in df.columns]
    if missing:
        errors.append(f"wallet_limits: missing columns: {missing}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings, df_norm=df)

    # Optional columns
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

    # method/comment normalize (wildcard supported)
    df["method"] = df["method"].astype(str).fillna("").str.strip().str.upper()
    df["method"] = df["method"].replace({"*": ""})  # treat '*' as wildcard == empty
    df["comment"] = df["comment"].astype(str).fillna("").str.strip()

    # analyzers parse
    def _parse_analyzers(s: str) -> list[str]:
        parts = [p.strip().lower() for p in (s or "").split(",")]
        return sorted(set(p for p in parts if p))

    df["_analyzers_list"] = df["analyzers"].map(_parse_analyzers)

    bad_an = df[(df["enabled"] == 1) & (df["_analyzers_list"].map(len) == 0)]
    if not bad_an.empty:
        errors.append("wallet_limits: analyzers empty for enabled=1")

    # scope validation
    valid_scopes = {"partner", "group"}
    bad_scope = df[~df["scope"].isin(valid_scopes)]
    if not bad_scope.empty:
        errors.append("wallet_limits: invalid scope (allowed: partner, group)")

    # limit_value required
    bad_val = df[(df["enabled"] == 1) & df["limit_value"].isna()]
    if not bad_val.empty:
        errors.append("wallet_limits: limit_value empty/non-numeric")

    # ---- uniqueness check (FIXED: includes method) ----
    active = df[df["enabled"] == 1].copy()
    if not active.empty:
        ex = active.explode("_analyzers_list").rename(columns={"_analyzers_list": "analyzer"})

        # normalize scope_value differently for partner vs group:
        # partner must match your normalize_partner_name (same as hourly)
        ex["scope_value_key"] = ex["scope_value"]
        is_partner = ex["scope"] == "partner"
        ex.loc[is_partner, "scope_value_key"] = ex.loc[is_partner, "scope_value"].map(normalize_partner_name)
        # group should be stable code
        ex.loc[~is_partner, "scope_value_key"] = ex.loc[~is_partner, "scope_value"].astype(str).str.strip().str.lower()

        # method already upper; empty is wildcard
        dup = (
            ex.groupby(["analyzer", "scope", "scope_value_key", "limit_type", "method"])
              .size()
              .reset_index(name="cnt")
        )
        if (dup["cnt"] > 1).any():
            errors.append("wallet_limits: duplicate active rules for same key (per analyzer/scope/method)")

        # Optional extra guard: prevent both wildcard and specific duplicates? (оставим как future)

    ok = len(errors) == 0
    return ValidationResult(ok=ok, errors=errors, warnings=warnings, df_norm=df)

def get_wallet_limits_df(
    *,
    rules_xlsx_path: str,
    sheet_name: str = "wallet_limits",
) -> pd.DataFrame:

    rs = get_rules_snapshot(force_sync=False)
    rules_xlsx_path = rs.local_path
    path = Path(rules_xlsx_path)

    df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
    res = validate_wallet_limits(df)

    if res.errors:
        raise RuntimeError("rules.xlsx validation fatal errors (wallet_limits)")

    return res.df_norm

def validate_exclude_time(df: pd.DataFrame) -> ValidationResult:
    """
    Contract:
      Fatal:
        - required columns missing
        - bad/duplicate id
        - enabled not in {0,1} / missing
        - dates not parseable (start_dt/end_dt/created_at)
        - start_dt >= end_dt
      Warning:
        - overlaps per analyzer per partner (enabled=1)
        - gaps in id numbering
    Returns:
      ValidationResult(ok, errors, warnings, df_norm)
    """
    errors: List[str] = []
    warnings: List[str] = []

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in _REQUIRED_EXCLUDE_COLS if c not in df.columns]

    if missing:
        errors.append(f"exclude_time: missing columns: {missing}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings, df_norm=df)

    # normalize text columns
    for c in ["id", "analyzers", "partner", "reason", "created_by"]:
        df[c] = df[c].map(_norm_str)

    def _parse_analyzers(s: str) -> list[str]:
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

    # enabled: strict 0/1
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

    # id format
    bad_fmt = ~df["id"].map(lambda s: bool(_ID_RE.match(s)))
    if bad_fmt.any():
        bad_ids = df.loc[bad_fmt, "id"].tolist()
        errors.append(
            "exclude_time: bad id format (expected EXC-00000); bad ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    # id uniqueness
    dup = df["id"].duplicated(keep=False)
    if dup.any():
        dups = sorted(set(df.loc[dup, "id"].tolist()))
        errors.append(
            "exclude_time: duplicate id(s): " + (", ".join(dups[:30]) + (" …" if len(dups) > 30 else ""))
        )

    # dates parsing (dayfirst=True for your dd.mm.yyyy)
    for c in ["start_dt", "end_dt", "created_at"]:
        df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)

    for c in ["start_dt", "end_dt", "created_at"]:
        bad = df[c].isna()
        if bad.any():
            bad_ids = df.loc[bad, "id"].tolist()
            errors.append(
                f"exclude_time: {c} not parseable to datetime; ids: "
                + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
            )

    # start < end
    ok_dates = df["start_dt"].notna() & df["end_dt"].notna()
    bad_range = ok_dates & (df["start_dt"] >= df["end_dt"])
    if bad_range.any():
        bad_ids = df.loc[bad_range, "id"].tolist()
        errors.append(
            "exclude_time: start_dt >= end_dt; ids: "
            + (", ".join(bad_ids[:30]) + (" …" if len(bad_ids) > 30 else ""))
        )

    # Warning: overlaps per analyzer per partner (enabled=1)
    active = df[(df["enabled"] == 1) & df["start_dt"].notna() & df["end_dt"].notna()].copy()

    if not active.empty:
        # перебираем каждый analyzer отдельно
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

    # Warning: gaps in id numbering
    try:
        nums = sorted(int(x.split("-")[1]) for x in df["id"] if _ID_RE.match(x))
        if nums:
            expected = set(range(nums[0], nums[-1] + 1))
            missing_nums = sorted(expected - set(nums))
            if missing_nums:
                warnings.append(
                    f"exclude_time id sequence gaps (WARNING): count={len(missing_nums)}, e.g. {missing_nums[:10]}"
                )
    except Exception:
        pass

    ok = len(errors) == 0
    return ValidationResult(ok=ok, errors=errors, warnings=warnings, df_norm=df)


# =============================================================================
# Gate: cached load + Dropbox safety + notify throttling
# =============================================================================

def get_exclude_time_df(
    *,
    rules_xlsx_path: str,
    sheet_name: str = "exclude_time",
    notify: Optional[Callable[..., Any]] = None,
    chat_id: Optional[str] = None,
    cooldown_minutes: int = 60,
) -> pd.DataFrame:
    """
    Returns validated+normalized exclude_time DataFrame.
    Uses cache keyed by (mtime, size) so repeated cycles won't re-read Excel.

    Raises RuntimeError on fatal validation issues.
    """
    if not rules_xlsx_path:
        raise RuntimeError("rules_xlsx_path is empty")

    # RULES_XLSX_PATH приходит как dropbox path. Берём локальный snapshot rules.xlsx.
    rs = get_rules_snapshot(force_sync=False)
    rules_xlsx_path = rs.local_path

    path = Path(rules_xlsx_path)

    # stat key for cache
    st = path.stat()
    stat_key = (st.st_mtime, st.st_size)

    cache = _EXCLUDE_TIME_CACHE.setdefault(str(path), _RulesCache())

    # cache hit: do not read xlsx at all
    if cache.stat_key == stat_key and cache.result is not None:
        res = cache.result
    else:
        df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
        res = validate_exclude_time(df)
        cache.stat_key = stat_key
        cache.result = res

    # notify (throttled)
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


# =============================================================================
# Optional: small utilities you may want later
# =============================================================================

def clear_rules_caches() -> None:
    """Useful for tests / manual resets."""
    _EXCLUDE_TIME_CACHE.clear()
    _NOTIFY_STATES["exclude.fatal"] = _NotifyState()
    _NOTIFY_STATES["exclude.warn"] = _NotifyState()

