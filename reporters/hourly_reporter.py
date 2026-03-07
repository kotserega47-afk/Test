# reporters/hourly_reporter.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from analyzers.hourly_analyzer import HourlyDTO
from core.config_manager import get_ui_layout_df
from core.event_log import append_event
from reporters.hourly_render_model import build_hourly_render_model


@dataclass(frozen=True)
class RenderedReport:
    text: str


def _apply_style(s: str, style: str) -> str:
    if style == "bold":
        return f"*{s}*"
    return s


def _render_by_layout(*, view: str, layout_df, render_model: Dict[str, Any]) -> str:
    df = layout_df.copy()
    df = df[
        (df["enabled"] == 1)
        & (df["view"].astype(str).str.strip().str.lower() == view.lower())
    ]
    if df.empty:
        return ""

    for col in ["section", "key", "title", "style"]:
        if col not in df.columns:
            df[col] = ""

    df["section"] = df["section"].where(df["section"].notna(), "")
    df["key"] = df["key"].where(df["key"].notna(), "")
    df["title"] = df["title"].where(df["title"].notna(), "")
    df["style"] = df["style"].where(df["style"].notna(), "")

    df["section"] = df["section"].astype(str).str.strip()
    df["key"] = df["key"].astype(str).str.strip()
    df["title"] = df["title"].astype(str).str.strip()
    df["style"] = df["style"].astype(str).str.strip().str.lower()

    df["title"] = df["title"].replace("nan", "")
    df["key"] = df["key"].replace("nan", "")
    df["style"] = df["style"].replace("nan", "")

    # ВАЖНО: сохраняем порядок строк из Excel, не пересортировываем по order
    out: List[str] = []

    for _, row in df.iterrows():
        key = row.get("key", "")
        title = row.get("title", "")
        style = row.get("style", "") or "text"

        if key and key not in render_model:
            append_event(
                type="layout_key_missing",
                job_type=view,
                payload={
                    "view": view,
                    "key": key,
                    "id": row.get("id", ""),
                    "section": row.get("section", ""),
                },
            )
            continue

        val = render_model.get(key, None) if key else None

        if style == "hr":
            if val is None or str(val).strip() == "":
                out.append("_______________________")
            else:
                out.append(str(val).strip())
            continue

        if val is None or val == "":
            if title and style in ("bold", "text"):
                out.append(_apply_style(title, style))
            continue

        if isinstance(val, list):
            if title:
                out.append(_apply_style(title, style))
            for item in val:
                if item is None:
                    out.append("")
                else:
                    out.append(str(item).rstrip())
            continue

        line = f"{title} {val}".strip() if title else str(val).strip()
        out.append(_apply_style(line, style))

    return "\n".join(out).strip()


def render_hourly(dto: HourlyDTO, *, job: str = "hourly") -> RenderedReport:
    """
    Pure rendering.
    Layout is controlled via rules.xlsx -> ui_layout (view='hourly').
    No Telegram here.
    """
    layout_df = get_ui_layout_df(force_sync=False)

    rm = build_hourly_render_model(dto)
    text = _render_by_layout(view="hourly", layout_df=layout_df, render_model=rm.model)
    return RenderedReport(text=text.strip())