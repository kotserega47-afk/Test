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
    df = df[(df["enabled"] == 1) & (df["view"].astype(str).str.strip().str.lower() == view.lower())]
    if df.empty:
        return ""

    df["section"] = df["section"].astype(str)
    df["key"] = df["key"].astype(str)
    df["title"] = df["title"].astype(str)
    df["style"] = df["style"].astype(str).str.strip().str.lower()

    df = df.sort_values(["order"], kind="stable")

    out: List[str] = []

    for _, row in df.iterrows():
        key = row["key"].strip()
        title = row["title"].strip()
        style = row["style"] or "text"

        if key and key not in render_model:
            append_event(
                type="layout_key_missing",
                job_type=view,
                payload={"view": view, "key": key, "id": row.get("id", ""), "section": row.get("section", "")},
            )
            continue

        val = render_model.get(key, None)

        if val is None or val == "":
            if title and style in ("bold", "text"):
                out.append(_apply_style(title, style))
            continue

        if style == "list":
            items = val if isinstance(val, list) else [str(val)]
            if title:
                out.append(title)
            for i, it in enumerate(items, 1):
                out.append(f"{i}) {it}")
            continue

        if style == "hr":
            out.append(str(val))
            continue

        line = f"{title} {val}".strip() if title else str(val)
        out.append(_apply_style(line, style))

    return "\n".join(out).strip() + "\n"


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