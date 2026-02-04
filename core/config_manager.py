# core/config_manager.py
from __future__ import annotations

import os
import re
import time
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Tuple, Dict, List

import pandas as pd


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


def _wait_file_stable(
    path: Path,
    *,
    checks: int = 3,
    interval_sec: float = 0.25,
    timeout_sec: float = 8.0,
) -> None:
    """
    Dropbox can be mid-sync (size/mtime changing). We wait until stable N checks.
    """
    start = time.time()
    last: Optional[Tuple[int, float]] = None
    stable = 0

    while True:
        try:
            st = path.stat()
            cur = (st.st_size, st.st_mtime)
        except FileNotFoundError:
            cur = None

        if cur is not None and cur == last:
            stable += 1
        else:
            stable = 0
            last = cur

        if cur is not None and stable >= (checks - 1):
            return

        if time.time() - start > timeout_sec:
            raise TimeoutError(f"File not stable: {path}")

        time.sleep(interval_sec)


def _detect_dropbox_conflicts(folder: Path, base_name: str) -> List[str]:
    """
    If Dropbox created "conflicted copy" Excel files, refuse to proceed.
    """
    # Examples: "rules (conflicted copy 2026-01-29).xlsx"
    conflicts = []
    for p in folder.glob(f"{base_name}*conflicted copy*.xlsx"):
        conflicts.append(p.name)
    return conflicts


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
    "analyzer",
    "partner",
    "start_dt",
    "end_dt",
    "reason",
    "created_by",
    "created_at",
]

_ID_RE = re.compile(r"^EXC-\d{5}$")


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
        - overlaps for same analyzer+partner (enabled=1)
        - gaps in id numbering
    Returns:
      ValidationResult(ok, errors, warnings, df_norm)
    """
    errors: List[str] = []
    warnings: List[str] = []

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in _REQUIRED_EXCLUDE_COLS if c not in df.columns]
    if missing:
        errors.append(f"exclude_time: missing columns: {missing}")
        return ValidationResult(ok=False, errors=errors, warnings=warnings, df_norm=df)

    # normalize text columns
    for c in ["id", "analyzer", "partner", "reason", "created_by"]:
        df[c] = df[c].map(_norm_str)

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

    # Warning: overlaps per analyzer+partner among enabled=1
    active = df[(df["enabled"] == 1) & df["start_dt"].notna() & df["end_dt"].notna()].copy()
    if not active.empty:
        for (analyzer, partner), g in active.groupby(["analyzer", "partner"], dropna=False):
            g = g.sort_values("start_dt")
            prev_end = None
            prev_id = None
            for _, row in g.iterrows():
                if prev_end is not None and row["start_dt"] < prev_end:
                    warnings.append(
                        f"exclude_time overlap (WARNING): analyzer={analyzer}, partner={partner}: "
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

    path = Path(rules_xlsx_path)

    # Guard: Dropbox conflicted copies
    conflicts = _detect_dropbox_conflicts(path.parent, base_name=path.stem)
    if conflicts:
        msg = "❌ Dropbox conflict detected рядом с rules.xlsx:\n" + "\n".join(f"- {x}" for x in conflicts)
        _safe_notify(notify, chat_id, msg)
        raise RuntimeError(msg)

    # stat key for cache
    st = path.stat()
    stat_key = (st.st_mtime, st.st_size)

    cache = _EXCLUDE_TIME_CACHE.setdefault(str(path), _RulesCache())

    # cache hit: do not read xlsx at all
    if cache.stat_key == stat_key and cache.result is not None:
        res = cache.result
    else:
        _wait_file_stable(path)
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
