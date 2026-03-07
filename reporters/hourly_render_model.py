from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from analyzers.hourly_analyzer import HourlyDTO


@dataclass(frozen=True)
class HourlyRenderModel:
    model: Dict[str, Any]


def _fmt_amount(v) -> str:
    """
    Contract: amount formatting is allowed here (presentation layer),
    reporter must not format anything.
    """
    try:
        n = int(round(float(v)))
        return f"{n:,}".replace(",", " ")
    except Exception:
        return "0"

def _parse_source_partners(value) -> List[str]:
    if value is None:
        return []
    s = str(value).strip()
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]

def build_hourly_render_model(dto: HourlyDTO) -> HourlyRenderModel:
    from core.config_manager import (
        get_hourly_payins_df,
        get_hourly_payouts_df,
        get_hourly_payout_methods_df,
    )

    # ===== загрузка rules =====

    payins_df = get_hourly_payins_df()
    payouts_df = get_hourly_payouts_df()
    methods_df = get_hourly_payout_methods_df()

    # ===== индексы факта =====
    # dto.* уже содержит факт по source partner / method.
    # Здесь строим словари для быстрого суммирования по source_partners из rules.

    fact_payins_by_partner: Dict[str, float] = {}
    for r in dto.payin:
        key = str(r.entity_code).strip()
        fact_payins_by_partner[key] = fact_payins_by_partner.get(key, 0.0) + float(r.amount or 0.0)

    fact_payout_by_partner_method: Dict[tuple[str, str], float] = {}
    for block in dto.payout:
        partner_key = str(block.group_code).strip()
        for m in block.methods:
            method_key = str(m.method_code).strip().upper()
            k = (partner_key, method_key)
            fact_payout_by_partner_method[k] = fact_payout_by_partner_method.get(k, 0.0) + float(m.amount or 0.0)
    # ===== payouts =====

    payout_items: List[str] = []

    groups = payouts_df[payouts_df["enabled"] == 1].sort_values("sort_order")

    for i, (_, g) in enumerate(groups.iterrows(), start=1):

        group_code = g["group_code"]
        title = g["display_name"]

        title_clean = str(title).rstrip(":").strip()
        payout_items.append(f"{i}) {title_clean}:")

        methods = (
            methods_df[
                (methods_df["group_code"] == group_code)
                & (methods_df["enabled"] == 1)
                ]
            .sort_values("sort_order")
        )
        group_sources = _parse_source_partners(g.get("source_partners", ""))

        for _, m in methods.iterrows():
            method_code = str(m["method_code"]).strip().upper()

            method_sources = _parse_source_partners(m.get("source_partners", ""))
            sources = method_sources if method_sources else group_sources

            amount = 0.0
            for src in sources:
                amount += fact_payout_by_partner_method.get((src, method_code), 0.0)

            comment = m.get("comment", "")
            c = f" ({comment})" if comment else ""

            payout_items.append(
                f" - {m['method_name']} – {_fmt_amount(amount)}{c}"
            )

        if int(g.get("group_break_after", 0)) == 1:
            payout_items.append("")

    # ===== payins =====

    payin_items: List[str] = []

    rows = payins_df[payins_df["enabled"] == 1].sort_values("sort_order")

    for i, (_, r) in enumerate(rows.iterrows(), start=1):

        sources = _parse_source_partners(r.get("source_partners", ""))

        amount = 0.0
        for src in sources:
            amount += fact_payins_by_partner.get(src, 0.0)

        comment = r.get("comment", "")
        c = f" ({comment})" if comment else ""

        payin_items.append(
            f"{i}) {r['display_name']} – {_fmt_amount(amount)}{c}"
        )

        if int(r.get("group_break_after", 0)) == 1:
            payin_items.append("")
    return HourlyRenderModel(
        model={
            "header.period": f"Данные на {dto.header_date.strftime('%d.%m')} с 00:00 по {dto.end_dt.strftime('%H:%M')}",
            "payouts.title": "Выплаты:",
            "payouts.items": payout_items,
            "separator.line": "_______________________",
            "payins.title": "Поступления:",
            "payins.items": payin_items,
        }
    )