# reporters/wallet_reporter.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from analyzers.wallet_analyzer import WalletDTO, WalletPartnerBlock, WalletMethodRow
from core.config_manager import get_job_params


@dataclass(frozen=True)
class RenderedReport:
    text: str


def _fmt_int(v: float) -> str:
    try:
        n = int(round(float(v)))
        return f"{n:,}".replace(",", " ")
    except Exception:
        return "0"


def _limit_suffix(row: WalletMethodRow) -> str:
    # comment/reason come from rules.xlsx wallet_limits
    c = (row.limit.comment or "").strip()
    r = (row.limit.reason or "").strip()
    parts = []
    if c:
        parts.append(c)
    if r:
        parts.append(r)
    return f" ({' / '.join(parts)})" if parts else ""


def render_wallet(dto: WalletDTO, *, job: str = "wallet") -> RenderedReport:
    """
    Pure rendering.
    Ordering / layout is controlled via rules.xlsx -> job_params (json), key: layout_json.

    layout_json example:
    {
      "wallet_layout": [
        {"group": ["A-мобайл", "АБХСбер (116)"], "spacing": 1}
      ]
    }
    Keys refer to dto.blocks[].group (preferred) OR dto.blocks[].partner (fallback).
    """
    params = get_job_params(job=job)
    layout = params.get("layout_json") or {}
    wallet_layout = layout.get("wallet_layout") or []

    # build lookups
    by_group: Dict[str, List[WalletPartnerBlock]] = {}
    by_partner: Dict[str, WalletPartnerBlock] = {}
    for b in dto.blocks:
        if b.group:
            by_group.setdefault(b.group, []).append(b)
        by_partner[b.partner] = b

    # stable sorting inside group
    for g in by_group:
        by_group[g] = sorted(by_group[g], key=lambda x: x.partner.lower())

    lines: List[str] = []
    lines.append(f"Данные на {dto.report_day.strftime('%d.%m')}")
    lines.append("")
    lines.append("Выплаты:")

    counter = 1

    def emit_block(title: str, blocks: List[WalletPartnerBlock]) -> None:
        nonlocal counter
        lines.append(f"{counter}) {title}:")
        # If multiple partners in one group: print per partner as sub-headers
        if len(blocks) == 1 and blocks[0].partner == title:
            b = blocks[0]
            for mr in b.rows:
                lines.append(f" - {mr.method} – {_fmt_int(mr.amount)}{_limit_suffix(mr)}")
        else:
            for b in blocks:
                lines.append(f" • {b.partner}:")
                for mr in b.rows:
                    lines.append(f"   - {mr.method} – {_fmt_int(mr.amount)}{_limit_suffix(mr)}")

        counter += 1

    if wallet_layout:
        # layout-driven
        for block in wallet_layout:
            group_keys = block.get("group") or []
            spacing = int(block.get("spacing") or 0)

            for key in group_keys:
                # prefer group match; else partner match
                if key in by_group:
                    emit_block(key, by_group[key])
                elif key in by_partner:
                    emit_block(key, [by_partner[key]])
                else:
                    continue

            for _ in range(spacing):
                lines.append("")
    else:
        # fallback: group->partner->method
        # groups first, then partners without group
        used_partners = set()
        for g in sorted(by_group.keys(), key=lambda x: x.lower()):
            emit_block(g, by_group[g])
            used_partners |= {b.partner for b in by_group[g]}

        rest = [b for b in dto.blocks if b.partner not in used_partners]
        for b in sorted(rest, key=lambda x: x.partner.lower()):
            emit_block(b.partner, [b])

    return RenderedReport(text="\n".join(lines))