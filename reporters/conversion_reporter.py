"""Presentation layer for conversion analysis (Excel + Telegram text, no I/O)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

import pandas as pd
from openpyxl import Workbook

from analyzers.conversion_dto import ConversionAnalysisResult, SpecialCardsState
from utils.excel_utils import flatten_lists_in_df, write_df_to_sheet

PROBLEM_BATCH_SIZE = 500
OFF_SHEET_COLUMNS = [
    "card",
    "partner",
    "original_partner",
    "max_consecutive_errors",
    "threshold",
    "status",
    "partner_list",
]


def _prepare_off_sheet_df(problem: pd.DataFrame) -> pd.DataFrame:
    df = flatten_lists_in_df(problem.sort_values(by=["partner", "card"]).copy())
    ordered = [c for c in OFF_SHEET_COLUMNS if c in df.columns]
    rest = [c for c in df.columns if c not in ordered]
    return df[ordered + rest]


@dataclass(frozen=True)
class ConversionTelegramRender:
    """Ready-to-send Telegram texts produced from analysis output."""

    problem_messages: tuple[str, ...]
    problem_columns_error: str | None
    no_problems_message: str | None
    summary_message: str
    file_caption: str
    problem_cards_count: int = 0


def render_excel(analysis: ConversionAnalysisResult) -> Workbook:
    """Build conversion Excel workbook without saving to disk."""
    problem = analysis.problem_cards
    cards_in_work_by_partner = analysis.cards_in_work_by_partner
    cards_in_work_by_pool = analysis.cards_in_work_by_pool

    wb = Workbook()
    wb.remove(wb.active)

    if not problem.empty:
        write_df_to_sheet(
            wb,
            "Отключить",
            _prepare_off_sheet_df(problem),
        )

    if not cards_in_work_by_partner.empty:
        write_df_to_sheet(
            wb,
            "Карт в работе",
            cards_in_work_by_partner.reset_index().rename(
                columns={"partner_display": "Партнёр", "card": "Карт в работе"}
            ),
        )

    if not cards_in_work_by_pool.empty:
        write_df_to_sheet(
            wb,
            "Карт в работе (Пулы)",
            cards_in_work_by_pool.reset_index().rename(
                columns={"pool": "Пул", "card": "Карт в работе"}
            ),
        )

    return wb


def render_problem_messages(problem_df: pd.DataFrame) -> tuple[tuple[str, ...], str | None, int]:
    """
    Render batched problem-card Telegram messages.
    Returns (messages, columns_error_message, cards_count).
    """
    if problem_df.empty:
        return (), None, 0

    cols_ok = all(c in problem_df.columns for c in ["card", "partner", "max_consecutive_errors"])
    if not cols_ok:
        return (), "⚠️ Пропущено формирование списка: отсутствуют нужные колонки.", 0

    msg_lines = [
        f"{row['card']} {row['partner']} {row['max_consecutive_errors']}"
        for _, row in (
            problem_df[["card", "partner", "max_consecutive_errors"]]
            .dropna()
            .astype(str)
            .drop_duplicates()
            .sort_values(by=["partner", "card"])
            .iterrows()
        )
    ]

    if not msg_lines:
        return (), None, 0

    messages = []
    for i in range(0, len(msg_lines), PROBLEM_BATCH_SIZE):
        chunk = msg_lines[i:i + PROBLEM_BATCH_SIZE]
        messages.append("🚫 Карты на отключение:\n" + "\n".join(chunk))
    return tuple(messages), None, len(msg_lines)


def render_no_problems_message() -> str:
    return "ℹ️ Нет карт, превысивших порог ошибок."


def render_summary_message(conv_file: str, analysis: ConversionAnalysisResult) -> str:
    summary = analysis.summary
    cards_in_work_by_partner = analysis.cards_in_work_by_partner
    msg_lines = [f"• {p}: {n}" for p, n in cards_in_work_by_partner.items()]
    return (
        f"✅ Анализ *{os.path.basename(conv_file)}* завершён.\n"
        f"Карт в работе по партнёрам:\n" + "\n".join(msg_lines) + "\n"
        f"На отключение: {summary.get('Карты на отключение', '—')}"
    )


def render_file_caption(conv_file: str) -> str:
    return f"📊 Отчёт по {os.path.basename(conv_file)}"


def render_telegram(analysis: ConversionAnalysisResult, conv_file: str) -> ConversionTelegramRender:
    """Render all post-analysis Telegram texts."""
    problem_messages, columns_error, cards_count = render_problem_messages(analysis.problem_cards)
    no_problems_message = None
    if not problem_messages and columns_error is None and analysis.problem_cards.empty:
        no_problems_message = render_no_problems_message()

    return ConversionTelegramRender(
        problem_messages=problem_messages,
        problem_columns_error=columns_error,
        no_problems_message=no_problems_message,
        summary_message=render_summary_message(conv_file, analysis),
        file_caption=render_file_caption(conv_file),
        problem_cards_count=cards_count,
    )


def render_special_cards_loaded_message(special_state: SpecialCardsState, today: date) -> str | None:
    if not special_state.special_loaded or special_state.df_special is None:
        return None

    today_special = special_state.df_special[special_state.df_special["start_date"] == today]
    if not today_special.empty:
        counts = today_special["partner_norm"].value_counts()
        stats = "\n".join([f"• {p}: {int(c)}" for p, c in counts.items()])
        return f"📊 Добавленные special-карты за {today.strftime('%d.%m.%Y')}:\n{stats}"
    return f"ℹ️ За {today.strftime('%d.%m.%Y')} новых special-карт не добавлено."


def render_special_cards_load_failure(*, error: bool = False) -> str:
    if error:
        return (
            "⚠️ Ошибка при загрузке special_cards.xlsx из Dropbox.\n"
            "Анализ выполнен без ограничений для специальных карт."
        )
    return (
        "⚠️ Файл special_cards.xlsx не найден в Dropbox.\n"
        "Анализ выполнен без ограничений для специальных карт."
    )


def render_special_cards_latest_date_message(special_state: SpecialCardsState) -> str | None:
    if not special_state.special_loaded:
        return None
    if pd.notna(special_state.latest_special_date):
        return (
            f"📅 Последняя дата в special_cards.xlsx: "
            f"{special_state.latest_special_date.strftime('%d.%m.%Y')}"
        )
    return "ℹ️ В special_cards.xlsx нет валидных дат."
