# reporters/hourly_reporter.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from analyzers.hourly_analyzer import HourlyDTO
from core.config_manager import get_ui_layout_df


@dataclass(frozen=True)
class RenderedReport:
    text: str


def _fmt_int(v: float) -> str:
    try:
        n = int(round(float(v)))
        return f"{n:,}".replace(",", " ")
    except Exception:
        return "0"


def _render_by_layout(*, view: str, layout_df, render_model: Dict[str, Any]) -> str:
    df = layout_df.copy()
    df = df[(df["enabled"] == 1) & (df["view"].astype(str).str.strip().str.lower() == view.lower())]
    if df.empty:
        return ""

    # стабильный порядок: section, order
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

        val = render_model.get(key, None)

        # if key missing -> skip silently (можно warning позже)
        if val is None or val == "":
            # но title можно вывести и без val (например "Выплаты:")
            if title and style in ("bold", "text"):
                out.append(_apply_style(title, style))
            continue

        if style == "list":
            # val must be list[str]
            items = val if isinstance(val, list) else [str(val)]
            # если title задан — выводим перед списком
            if title:
                out.append(title)
            for i, it in enumerate(items, 1):
                out.append(f"{i}) {it}")
            continue

        if style == "hr":
            out.append(str(val))
            continue

        # text/bold/dt/...
        if title:
            line = f"{title} {val}".strip()
        else:
            line = str(val)

        out.append(_apply_style(line, style))

    return "\n".join(out).strip() + "\n"


def _apply_style(s: str, style: str) -> str:
    if style == "bold":
        return f"*{s}*"  # можно позже заменить на markdown/bold если используешь parse_mode
    return s


def render_hourly(dto: HourlyDTO, *, job: str = "hourly") -> RenderedReport:
    """
    Pure rendering.
    Layout is controlled via rules.xlsx -> ui_layout (view='hourly').
    No Telegram here.
    """
    layout_df = get_ui_layout_df(force_sync=False)

    # build items exactly as твой текущий формат
    payout_items: List[str] = []
    for b in dto.payout:
        payout_items.append(f"{b.title}:")
        for m in b.methods:
            c = f" ({m.comment})" if m.comment else ""
            payout_items.append(f" - {m.title} – {_fmt_int(m.amount)}{c}")

    payin_items: List[str] = []
    for r in dto.payin:
        c = f" ({r.comment})" if r.comment else ""
        payin_items.append(f"{r.title} – {_fmt_int(r.amount)}{c}")

    render_model: Dict[str, Any] = {
        "header.period": f"на {dto.header_date.strftime('%d.%m')} с 00:00 по {dto.end_dt.strftime('%H:%M')}",
        "payouts.items": payout_items,
        "separator.line": "_______________________",
        "payins.items": payin_items,
    }

    text = _render_by_layout(view="hourly", layout_df=layout_df, render_model=render_model)
    return RenderedReport(text=text.strip())
