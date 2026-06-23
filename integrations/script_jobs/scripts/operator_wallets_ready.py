"""Operator report: wallets in working statuses grouped by partner."""

from __future__ import annotations

import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from core.config_manager import get_job_params
from core.job_runner import get_status
from integrations.script_jobs.antares_wallets_export import download_antares_wallets_export
from integrations.script_jobs.types import ScriptExecutionContext, ScriptResult
from main import CONVERSION_COLUMNS
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["MAIN"]
log = get_logger(name, icon)

MSK_TZ = ZoneInfo("Europe/Moscow")

PARTNER_COL = CONVERSION_COLUMNS["partner"]
STATUS_COL = CONVERSION_COLUMNS["status"]
CARD_COL = CONVERSION_COLUMNS["card"]

DEFAULT_TEXT_LIMIT_CHARS = 3500
DEFAULT_TOP_N = 25
MISSING_PARTNER_LABEL = "(без партнёра)"


def normalize_status(value: str) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text


READY_STATUS_LABELS = (
    "Готов к работе",
    "Активный вход",
    "Активный выход",
)
READY_STATUSES_NORM = frozenset(
    normalize_status(label) for label in READY_STATUS_LABELS
)


def _resolve_column(df: pd.DataFrame, expected: str) -> str:
    expected_norm = normalize_status(expected)
    for col in df.columns:
        if normalize_status(str(col)) == expected_norm:
            return str(col)
    raise ValueError(f"missing column: {expected}")


def _partner_names(cell: object) -> list[str]:
    raw = str(cell or "").strip()
    if not raw:
        return [MISSING_PARTNER_LABEL]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or [MISSING_PARTNER_LABEL]


def count_ready_wallets_by_partner(df: pd.DataFrame) -> list[tuple[str, int]]:
    """Count unique cards per partner for rows in READY statuses."""
    partner_col = _resolve_column(df, PARTNER_COL)
    status_col = _resolve_column(df, STATUS_COL)
    card_col = _resolve_column(df, CARD_COL)

    partner_cards: dict[str, set[str]] = defaultdict(set)
    row_index = 0

    for row in df.itertuples(index=False):
        row_index += 1
        row_map = dict(zip(df.columns, row))
        status = normalize_status(row_map.get(status_col, ""))
        if status not in READY_STATUSES_NORM:
            continue

        card_raw = str(row_map.get(card_col, "") or "").strip()
        card_key = card_raw if card_raw else f"__row_{row_index}"

        for partner in _partner_names(row_map.get(partner_col, "")):
            partner_cards[partner].add(card_key)

    items = [(partner, len(cards)) for partner, cards in partner_cards.items()]
    items.sort(key=lambda item: (-item[1], item[0].casefold()))
    return items


def _status_header_line() -> str:
    return " / ".join(READY_STATUS_LABELS)


def format_report_text(
    rows: list[tuple[str, int]],
    *,
    total: int,
    truncated: bool,
    shown_count: int,
) -> str:
    lines = [
        "📊 Кошельки в рабочих статусах",
        f"Статусы: {_status_header_line()}",
        "",
    ]

    if not rows:
        lines.append("Нет кошельков в выбранных статусах.")
    else:
        display_rows = rows[:shown_count] if truncated else rows
        for partner, count in display_rows:
            lines.append(f"{partner} — {count}")
        if truncated:
            lines.append("")
            lines.append(f"Показаны топ-{shown_count} из {len(rows)} партнёров. Полный отчёт — в файле.")

    lines.append("")
    lines.append(f"Итого: {total}")
    return "\n".join(lines)


def build_report_xlsx(rows: list[tuple[str, int]], *, path: str) -> str:
    generated_at = datetime.now(MSK_TZ).strftime("%Y-%m-%d %H:%M:%S MSK")
    out = pd.DataFrame(
        {
            "partner": [partner for partner, _ in rows],
            "ready_wallets_count": [count for _, count in rows],
            "generated_at": generated_at,
            "statuses_included": _status_header_line(),
        }
    )
    out.to_excel(path, index=False)
    return path


def _load_params(context: ScriptExecutionContext) -> tuple[int, int]:
    params = get_job_params(job=context.job_type)
    text_limit = params.get("text_limit_chars", DEFAULT_TEXT_LIMIT_CHARS)
    top_n = params.get("top_n", DEFAULT_TOP_N)
    try:
        text_limit = max(500, int(text_limit))
    except (TypeError, ValueError):
        text_limit = DEFAULT_TEXT_LIMIT_CHARS
    try:
        top_n = max(1, int(top_n))
    except (TypeError, ValueError):
        top_n = DEFAULT_TOP_N
    return text_limit, top_n


def _warn_if_antares_jobs_running() -> None:
    try:
        running = get_status()
    except Exception:
        return
    for jt in ("wallet", "download"):
        if jt in running:
            log.warning(
                "[operator_wallets_ready] concurrent Antares job running job_type=%s "
                "(script uses independent lock; Playwright overlap possible)",
                jt,
            )


def run_operator_wallets_ready(context: ScriptExecutionContext) -> ScriptResult:
    _warn_if_antares_jobs_running()
    text_limit, top_n = _load_params(context)

    try:
        export_path = download_antares_wallets_export()
        df = pd.read_excel(export_path, dtype=str)
        rows = count_ready_wallets_by_partner(df)
    except Exception as exc:
        log.exception("[operator_wallets_ready] failed script_key=%s", context.script_key)
        return ScriptResult(
            status="failed",
            text=f"❌ Не удалось сформировать отчёт: {type(exc).__name__}",
            metadata={"error_class": type(exc).__name__},
        )

    total = sum(count for _, count in rows)
    full_text = format_report_text(rows, total=total, truncated=False, shown_count=len(rows))
    truncated = len(full_text) > text_limit or len(rows) > top_n
    shown_count = min(top_n, len(rows)) if truncated else len(rows)
    text = format_report_text(
        rows,
        total=total,
        truncated=truncated,
        shown_count=shown_count,
    )

    files: tuple[str, ...] = ()
    if truncated and rows:
        tmp_dir = os.path.join(tempfile.gettempdir(), "script_jobs")
        os.makedirs(tmp_dir, exist_ok=True)
        xlsx_path = os.path.join(
            tmp_dir,
            f"operator_wallets_ready_{datetime.now(MSK_TZ).strftime('%Y%m%d_%H%M%S')}.xlsx",
        )
        build_report_xlsx(rows, path=xlsx_path)
        files = (xlsx_path,)

    return ScriptResult(
        status="ok",
        text=text,
        files=files,
        metadata={
            "partners": len(rows),
            "total_ready_wallets": total,
            "truncated": truncated,
        },
    )
