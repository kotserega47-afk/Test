from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from openpyxl import Workbook

from db.database import get_session
from db.models import Card, CardEvent, ErrorType
from utils.excel_utils import flatten_lists_in_df, write_df_to_sheet
from integrations.telegram_bot import send_file_sync


def load_card_events(days: int = 1) -> pd.DataFrame:
    """
    Загружает события по картам за последние `days` дней.
    """
    since = datetime.utcnow() - timedelta(days=days)
    with get_session() as session:
        query = (
            session.query(
                Card.card_number.label("card"),
                CardEvent.status,
                CardEvent.amount,
                CardEvent.created_at,
                ErrorType.code.label("error_code"),
                ErrorType.description.label("error_description"),
            )
            .join(CardEvent.card)
            .outerjoin(CardEvent.error)
            .filter(CardEvent.created_at >= since)
            .order_by(CardEvent.created_at.desc())
        )
        return pd.DataFrame(
            query.all(),
            columns=[c["name"] for c in query.column_descriptions],
        )


def build_workbook(df: pd.DataFrame) -> Path:
    """
    Создаёт Excel-отчёт и сохраняет в папку reports.
    """
    wb = Workbook()
    wb.remove(wb.active)

    write_df_to_sheet(wb, "card_events", flatten_lists_in_df(df))

    output_path = Path("reports") / f"card_events_{datetime.utcnow():%Y%m%d_%H%M}.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path


def run(days: int = 1) -> None:
    """
    Формирует отчёт и отправляет в Telegram.
    """
    df = load_card_events(days)
    if df.empty:
        send_file_sync(None, caption=f"Событий за {days} дн. не найдено")
        return

    report_path = build_workbook(df)
    send_file_sync(report_path, caption=f"Отчёт по событиям карт за {days} дн.")
    report_path.unlink(missing_ok=True)


if __name__ == "__main__":
    run()
