from __future__ import annotations
import pandas as pd
import sys

from dataclasses import dataclass
from datetime import datetime
from core.datetime_utils import now_msk
from pathlib import Path
from typing import Any, Iterable
from core.datetime_utils import parse_msk_series

@dataclass
class CheckMessage:
    level: str  # "ERROR" | "WARN" | "INFO"
    where: str
    message: str


def _excel_sheets(path: Path) -> list[str]:
    xl = pd.ExcelFile(path, engine="openpyxl")
    return list(xl.sheet_names)


def _read_sheet(path: Path, sheet: str) -> pd.DataFrame:
    # Excel часто содержит “хвосты” пустых строк — они не должны считаться правилами.
    df = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")
    return df.dropna(how="all")


def _missing_cols(df: pd.DataFrame, required: Iterable[str]) -> list[str]:
    req = list(required)
    return [c for c in req if c not in df.columns]


def _as_int01(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0).astype(int)


def _is_enabled_col(df: pd.DataFrame) -> bool:
    return "enabled" in df.columns


def _active_df(df: pd.DataFrame) -> pd.DataFrame:
    if not _is_enabled_col(df):
        return df
    enabled = _as_int01(df["enabled"])
    return df[enabled.eq(1)]


def _safe_str(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _parse_analyzers_csv(val: Any) -> list[str]:
    s = _safe_str(val)
    if not s:
        return []
    return [p.strip().lower() for p in s.split(",") if p.strip()]


def check_rules_xlsx(path: Path) -> list[CheckMessage]:
    msgs: list[CheckMessage] = []

    sheets = _excel_sheets(path)
    msgs.append(CheckMessage("INFO", "workbook", f"Sheets ({len(sheets)}): {', '.join(sheets)}"))

    required_sheets = {
        "exclude_time": ("id", "enabled", "analyzers", "start_dt", "end_dt", "reason"),
        "access": ("chat_id", "level", "enabled"),
        "commands": ("command", "required_level", "allow_private", "allow_groups", "enabled"),
    }
    optional_sheets = {
        "meta": ("version", "updated_at"),
        "thresholds_partner": ("id", "enabled", "analyzer", "partner", "metric", "reason"),
        "wallet_limits": ("id", "enabled", "analyzers", "scope", "scope_value", "limit_type", "limit_value", "reason"),
    }

    # --- presence ---
    for sh in required_sheets:
        if sh not in sheets:
            msgs.append(CheckMessage("ERROR", sh, "Missing required sheet"))

    for sh in optional_sheets:
        if sh not in sheets:
            msgs.append(CheckMessage("WARN", sh, "Missing optional sheet"))

    # --- meta ---
    if "meta" in sheets:
        df = _read_sheet(path, "meta")
        miss = _missing_cols(df, optional_sheets["meta"])
        if miss:
            msgs.append(CheckMessage("WARN", "meta", f"Missing columns: {miss}"))
        else:
            # Many files use key/value format; we accept both.
            version_val = None
            if set(df.columns) >= {"key", "value"}:
                m = df.set_index("key")["value"].to_dict()
                version_val = m.get("version")
            elif "version" in df.columns and len(df) > 0:
                version_val = df["version"].iloc[0]
            if version_val is not None:
                try:
                    ver = int(float(str(version_val).strip()))
                    if ver != 3:
                        msgs.append(CheckMessage("WARN", "meta", f"Contract version is {ver}, expected 3"))
                except Exception:
                    msgs.append(CheckMessage("WARN", "meta", f"Cannot parse contract version: {version_val!r}"))

    # --- exclude_time ---
    if "exclude_time" in sheets:
        df = _read_sheet(path, "exclude_time")
        miss = _missing_cols(df, required_sheets["exclude_time"])
        if miss:
            msgs.append(CheckMessage("ERROR", "exclude_time", f"Missing columns: {miss}"))
        else:
            active = _active_df(df)
            msgs.append(CheckMessage("INFO", "exclude_time", f"Rows={len(df)}, active={len(active)}"))

            # analyzers required for enabled=1
            an = active["analyzers"].apply(_parse_analyzers_csv)
            empty_an = an.map(len).eq(0)
            if empty_an.any():
                msgs.append(CheckMessage("ERROR", "exclude_time", f"enabled=1 but analyzers empty in {int(empty_an.sum())} row(s)"))

            # dates sanity
            start = parse_msk_series(df["start_dt"])
            end = parse_msk_series(df["end_dt"])
            bad_dt = start.isna() | end.isna()
            if bad_dt.any():
                msgs.append(CheckMessage("ERROR", "exclude_time", f"Unparseable start_dt/end_dt in {int(bad_dt.sum())} row(s)"))
            bad_range = (~bad_dt) & (start >= end)
            if bad_range.any():
                msgs.append(CheckMessage("ERROR", "exclude_time", f"start_dt >= end_dt in {int(bad_range.sum())} row(s)"))

    # --- thresholds_partner ---
    if "thresholds_partner" in sheets:
        df = _read_sheet(path, "thresholds_partner")
        miss = _missing_cols(df, optional_sheets["thresholds_partner"])
        if miss:
            msgs.append(CheckMessage("ERROR", "thresholds_partner", f"Missing columns: {miss}"))
        else:
            active = _active_df(df)
            msgs.append(CheckMessage("INFO", "thresholds_partner", f"Rows={len(df)}, active={len(active)}"))

            # uniqueness among active: (analyzer, partner, metric)
            key_cols = ["analyzer", "partner", "metric"]
            if len(active) > 0:
                dup = active.duplicated(subset=key_cols, keep=False)
                if dup.any():
                    msgs.append(CheckMessage("ERROR", "thresholds_partner", f"Duplicate active rules by {tuple(key_cols)}: {int(dup.sum())} row(s)"))

            # XOR: exactly one of threshold_min/threshold_max
            if "threshold_min" in df.columns or "threshold_max" in df.columns:
                tmin = pd.to_numeric(df.get("threshold_min"), errors="coerce")
                tmax = pd.to_numeric(df.get("threshold_max"), errors="coerce")
                both = tmin.notna() & tmax.notna()
                none = tmin.isna() & tmax.isna()
                if both.any():
                    msgs.append(CheckMessage("ERROR", "thresholds_partner", f"XOR violated (both min & max filled) in {int(both.sum())} row(s)"))
                if none.any():
                    msgs.append(CheckMessage("ERROR", "thresholds_partner", f"XOR violated (both min & max empty) in {int(none.sum())} row(s)"))

    # --- wallet_limits ---
    if "wallet_limits" in sheets:
        df = _read_sheet(path, "wallet_limits")
        miss = _missing_cols(df, optional_sheets["wallet_limits"])
        if miss:
            msgs.append(CheckMessage("ERROR", "wallet_limits", f"Missing columns: {miss}"))
        else:
            active = _active_df(df)
            msgs.append(CheckMessage("INFO", "wallet_limits", f"Rows={len(df)}, active={len(active)}"))

            # enabled=1 -> analyzers non-empty
            an = active["analyzers"].apply(_parse_analyzers_csv)
            empty_an = an.map(len).eq(0)
            if empty_an.any():
                msgs.append(CheckMessage("ERROR", "wallet_limits", f"enabled=1 but analyzers empty in {int(empty_an.sum())} row(s)"))

            # allowed scope and limit_type
            scope = df["scope"].astype(str).str.strip().str.lower()
            bad_scope = ~scope.isin({"partner", "group"})
            if bad_scope.any():
                msgs.append(CheckMessage("ERROR", "wallet_limits", f"Invalid scope in {int(bad_scope.sum())} row(s) (allowed: partner, group)"))

            lt = df["limit_type"].astype(str).str.strip().str.lower()
            bad_lt = ~lt.isin({"daily_max_amount"})
            if bad_lt.any():
                msgs.append(CheckMessage("WARN", "wallet_limits", f"Non-standard limit_type in {int(bad_lt.sum())} row(s) (expected: daily_max_amount)"))

            # limit_value numeric
            lv = pd.to_numeric(df["limit_value"], errors="coerce")
            bad_lv = lv.isna()
            if bad_lv.any():
                msgs.append(CheckMessage("ERROR", "wallet_limits", f"Non-numeric/empty limit_value in {int(bad_lv.sum())} row(s)"))

            # uniqueness among active: (analyzers, scope, scope_value, limit_type)
            key_cols = ["analyzers", "scope", "scope_value", "limit_type"]
            if len(active) > 0:
                dup = active.duplicated(subset=key_cols, keep=False)
                if dup.any():
                    msgs.append(CheckMessage("ERROR", "wallet_limits", f"Duplicate active rules by {tuple(key_cols)}: {int(dup.sum())} row(s)"))

    # --- access ---
    if "access" in sheets:
        df = _read_sheet(path, "access")
        miss = _missing_cols(df, required_sheets["access"])
        if miss:
            # AccessRules in code tolerates missing enabled (defaults to 1), but contract says enabled required.
            # We'll treat as WARN for enabled, ERROR for core fields.
            core_miss = [c for c in miss if c in {"chat_id", "level"}]
            if core_miss:
                msgs.append(CheckMessage("ERROR", "access", f"Missing columns: {core_miss}"))
            if "enabled" in miss:
                msgs.append(CheckMessage("WARN", "access", "Missing column 'enabled' (code defaults to 1)"))
        else:
            active = _active_df(df)
            msgs.append(CheckMessage("INFO", "access", f"Rows={len(df)}, active={len(active)}"))

            # chat_id must be private or numeric
            def _ok_chat(v: Any) -> bool:
                s = _safe_str(v).lower()
                if s == "private":
                    return True
                s2 = s.lstrip("-")
                return s2.replace(".", "", 1).isdigit()

            bad = ~df["chat_id"].apply(_ok_chat)
            if bad.any():
                examples = df.loc[bad, "chat_id"].head(5).tolist()
                msgs.append(CheckMessage("ERROR", "access", f"Invalid chat_id values (examples): {examples}"))

    # --- commands ---
    if "commands" in sheets:
        df = _read_sheet(path, "commands")
        miss = _missing_cols(df, required_sheets["commands"])
        if miss:
            core_miss = [c for c in miss if c in {"command", "required_level", "allow_private", "allow_groups"}]
            if core_miss:
                msgs.append(CheckMessage("ERROR", "commands", f"Missing columns: {core_miss}"))
            if "enabled" in miss:
                msgs.append(CheckMessage("WARN", "commands", "Missing column 'enabled' (code defaults to 1)"))
        else:
            active = _active_df(df)
            msgs.append(CheckMessage("INFO", "commands", f"Rows={len(df)}, active={len(active)}"))

            for c in ("allow_private", "allow_groups"):
                vals = set(_as_int01(df[c]).unique().tolist())
                if not vals.issubset({0, 1}):
                    msgs.append(CheckMessage("ERROR", "commands", f"{c} must be 0/1, got {sorted(vals)}"))

    return msgs


def _print_report(msgs: list[CheckMessage]) -> int:
    errs = [m for m in msgs if m.level == "ERROR"]
    warns = [m for m in msgs if m.level == "WARN"]

    def _emit(level: str) -> None:
        for m in msgs:
            if m.level != level:
                continue
            print(f"[{m.level}] {m.where}: {m.message}")

    print(f"Checked at: {now_msk().isoformat(timespec='seconds')}")
    _emit("INFO")
    _emit("WARN")
    _emit("ERROR")

    print()
    print(f"Summary: errors={len(errs)}, warnings={len(warns)}")
    return 0 if len(errs) == 0 else 1


def main(argv: list[str]) -> int:
    xlsx_path = Path(argv[1]) if len(argv) > 1 else Path(r"c:\Users\denis\Downloads\rules.xlsx")
    if not xlsx_path.exists():
        print(f"[ERROR] workbook: File not found: {xlsx_path}")
        return 2
    msgs = check_rules_xlsx(xlsx_path)
    return _print_report(msgs)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

