# reporters/wallet_reporter.py
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from core.event_log import append_event
from reporters.wallet_render_model import build_wallet_render_model
from analyzers.wallet_analyzer import WalletStatsDTO


@dataclass(frozen=True)
class RenderedReport:
    main_text: str
    alerts_text: str


def _is_empty_value(value) -> bool:
    if value is None:
        return True

    if isinstance(value, str):
        return value.strip() == ""

    if isinstance(value, list):
        if not value:
            return True

        if all(isinstance(x, str) for x in value):
            return all(str(x).strip() == "" for x in value)

        if all(isinstance(x, list) for x in value):
            flat = []
            for block in value:
                flat.extend([str(line) for line in block])
            return all(line.strip() == "" for line in flat)

    return False


def _apply_style(value, style: str) -> List[str]:
    style = (style or "text").strip().lower()

    if style == "blank":
        return [""]

    if style == "hr":
        return ["_______________________"]

    if _is_empty_value(value):
        return []

    if style == "text":
        if isinstance(value, list):
            return [str(x) for x in value]
        return [str(value)]

    if style == "block":
        if not isinstance(value, list):
            return [str(value)]

        lines: List[str] = []
        first_block = True

        for block in value:
            if block is None:
                continue

            if isinstance(block, str):
                block_lines = [block]
            else:
                block_lines = [str(line) for line in block if str(line).strip() != ""]

            if not block_lines:
                continue

            if not first_block:
                lines.append("")

            lines.extend(block_lines)
            first_block = False

        return lines

    if isinstance(value, list):
        return [str(x) for x in value]
    return [str(value)]


def _load_layout(force_sync: bool = False) -> pd.DataFrame:
    from core.config_manager import get_ui_layout_df

    df = get_ui_layout_df(force_sync=force_sync)
    if df is None or df.empty:
        append_event(
            type="ui_layout_missing",
            job_type="wallet",
            payload={"view": "wallet"},
        )
        raise RuntimeError(
            "ui_layout: wallet layout not found. Check rules.xlsx sheet 'ui_layout'."
        )

    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]

    required_cols = {"enabled", "view", "section", "key", "style", "order"}
    missing = [c for c in required_cols if c not in out.columns]
    if missing:
        raise RuntimeError(
            f"ui_layout: missing required columns for wallet: {', '.join(sorted(missing))}"
        )

    out["view"] = out["view"].fillna("").astype(str).str.strip().str.lower()
    out["section"] = out["section"].fillna("").astype(str).str.strip().str.lower()
    out["enabled"] = pd.to_numeric(out["enabled"], errors="coerce").fillna(0).astype(int)
    out["order"] = pd.to_numeric(out["order"], errors="coerce").fillna(999999).astype(int)

    wallet_rows = out[(out["enabled"] == 1) & (out["view"] == "wallet")].copy()
    if wallet_rows.empty:
        append_event(
            type="ui_layout_missing",
            job_type="wallet",
            payload={"view": "wallet"},
        )
        raise RuntimeError(
            "ui_layout: no enabled rows for view='wallet'. Check rules.xlsx sheet 'ui_layout'."
        )

    return wallet_rows.sort_values("order", kind="stable")


def _render_part(df: pd.DataFrame, render_model: dict) -> str:
    result_lines: List[str] = []

    for _, row in df.iterrows():
        key = str(row.get("key", "")).strip()
        style = str(row.get("style", "text")).strip().lower()

        if not key:
            continue

        if key not in render_model:
            append_event(
                type="layout_key_missing",
                job_type="wallet",
                payload={"view": "wallet", "key": key},
            )
            continue

        value = render_model.get(key)
        if style in {"text", "block"} and _is_empty_value(value):
            continue

        styled = _apply_style(value, style)
        for line in styled:
            if line == "" and result_lines and result_lines[-1] == "":
                continue
            result_lines.append(line)

    while result_lines and result_lines[-1] == "":
        result_lines.pop()

    return "\n".join(result_lines).strip()


def render_wallet(
    dto: WalletStatsDTO,
    *,
    job: str = "wallet",
    layout_df: Optional[pd.DataFrame] = None,
    rules_force_sync: bool = False,
) -> RenderedReport:
    _ = job
    rm = build_wallet_render_model(dto)
    render_model = rm.model
    df = _load_layout(force_sync=rules_force_sync) if layout_df is None else layout_df.copy()

    df.columns = [str(c).strip().lower() for c in df.columns]
    if "section" not in df.columns:
        raise RuntimeError("ui_layout: missing required column 'section' for wallet rendering")

    df["section"] = df["section"].fillna("").astype(str).str.strip().str.lower()

    main_sections = {"stuck", "header", "partners"}
    alerts_sections = {"alerts"}

    main_df = df[df["section"].isin(main_sections)].copy()
    alerts_df = df[df["section"].isin(alerts_sections)].copy()

    main_text = _render_part(main_df, render_model)
    alerts_text = _render_part(alerts_df, render_model)

    return RenderedReport(
        main_text=main_text,
        alerts_text=alerts_text,
    )