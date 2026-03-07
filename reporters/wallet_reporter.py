# reporters/wallet_reporter.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

import pandas as pd

from analyzers.wallet_analyzer import WalletStatsDTO
from core.config_manager import get_ui_layout_df
from core.event_log import append_event
from reporters.wallet_render_model import build_wallet_render_model


@dataclass(frozen=True)
class RenderedReport:
    text: str


def _default_wallet_layout() -> pd.DataFrame:
    return pd.DataFrame([
        {"enabled": 1, "view": "wallet", "section": "stuck", "order": 10, "key": "stuck.title", "style": "text"},
        {"enabled": 1, "view": "wallet", "section": "stuck", "order": 20, "key": "stuck.items", "style": "text"},
        {"enabled": 1, "view": "wallet", "section": "header", "order": 30, "key": "header.title", "style": "text"},
        {"enabled": 1, "view": "wallet", "section": "header", "order": 40, "key": "header.window", "style": "text"},
        {"enabled": 1, "view": "wallet", "section": "partners", "order": 50, "key": "partners.blocks", "style": "block"},
        {"enabled": 1, "view": "wallet", "section": "alerts", "order": 60, "key": "alerts.title", "style": "text"},
        {"enabled": 1, "view": "wallet", "section": "alerts", "order": 70, "key": "alerts.blocks", "style": "block"},
    ])


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, list):
        return len(value) == 0
    return False


def _apply_style(value: Any, style: str) -> List[str]:
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
        lines: List[str] = []
        for idx, block in enumerate(value):
            if idx > 0:
                lines.append("")
            if isinstance(block, list):
                lines.extend([str(x) for x in block])
            else:
                lines.append(str(block))
        return lines

    if isinstance(value, list):
        return [str(x) for x in value]
    return [str(value)]


def _load_layout(force_sync: bool = False) -> pd.DataFrame:
    df = get_ui_layout_df(force_sync=force_sync)
    if df is None or df.empty:
        return _default_wallet_layout()

    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    for c in ["enabled", "view", "key", "style", "order"]:
        if c not in out.columns:
            out[c] = "" if c != "enabled" else 1

    out["view"] = out["view"].fillna("").astype(str).str.strip().str.lower()
    out["enabled"] = pd.to_numeric(out["enabled"], errors="coerce").fillna(0).astype(int)
    out["order"] = pd.to_numeric(out["order"], errors="coerce").fillna(999999).astype(int)

    wallet_rows = out[(out["enabled"] == 1) & (out["view"] == "wallet")].copy()
    if wallet_rows.empty:
        return _default_wallet_layout()

    return wallet_rows.sort_values("order", kind="stable")


def render_wallet(
    dto: WalletStatsDTO,
    *,
    job: str = "wallet",
    layout_df: Optional[pd.DataFrame] = None,
    rules_force_sync: bool = False,
) -> RenderedReport:
    _ = job  # совместимость по сигнатуре
    rm = build_wallet_render_model(dto)
    render_model = rm.model
    df = _load_layout(force_sync=rules_force_sync) if layout_df is None else layout_df.copy()

    result_lines: List[str] = []

    for _, row in df.iterrows():
        key = str(row.get("key", "")).strip()
        style = str(row.get("style", "text")).strip().lower()

        if not key:
            continue

        if key not in render_model:
            append_event(
                event_type="layout_key_missing",
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

    return RenderedReport(text="\n".join(result_lines).strip())
