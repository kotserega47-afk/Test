"""Export-only workbook formatting for WalletEditor registry (read-only report)."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from core.datetime_utils import EXCEL_DATETIME_FORMAT, parse_msk_datetime
from integrations.wallet_editor_registry_lifecycle import (
    ACTION_ADD_PARTNER,
    ACTION_REMOVE_PARTNER,
    ALL_RESULTS_COLUMNS,
    HOLD_COLUMNS,
    OPERATION_DATE_COLUMN,
    OTLEZKA_COLUMNS,
    RUNS_COLUMNS,
    parse_operation_date,
)

EXPORT_SHEET_RESULTS = "Результаты"
EXPORT_SHEET_RUNS = "Запуски"
EXPORT_SHEET_HOLD = "Hold"
EXPORT_SHEET_OTLEZKA = "Отлёжка"
EXPORT_SHEET_STATS = "Статистика"
EXPORT_SHEET_README = "README"

EXPORT_DATETIME_DISPLAY = "%d.%m.%Y %H:%M:%S"
EXPORT_DATE_DISPLAY = "%d.%m.%Y"
EXPORT_EXCEL_DATETIME_NUMBER_FORMAT = "dd.mm.yyyy hh:mm:ss"
HOLD_ADDED_DATE_COLUMN = "Дата добавления"
STATS_PARTNER_HEADER = "Партнёр"
STATS_TOTAL_HEADER = "Всего"
STATS_REMOVE_TITLE = "Remove Partner"
STATS_ADD_TITLE = "Add Partner"
STATS_TOTAL_ROW_LABEL = "Итого"

MAX_COLUMN_WIDTH = 50
MSK = ZoneInfo("Europe/Moscow")

ALL_RESULTS_DATETIME_COLUMNS = (
    OPERATION_DATE_COLUMN,
    "Дата отключения",
    "Дата включения",
)
RUNS_DATETIME_COLUMNS = ("started_at", "finished_at")
HOLD_DATETIME_COLUMNS = (HOLD_ADDED_DATE_COLUMN,)

_THIN = Side(style="thin")
_ALL_BORDERS = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_BOLD = Font(bold=True)


def sort_all_results_for_export(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    work = df.copy()
    work["_sort_op"] = work[OPERATION_DATE_COLUMN].map(
        lambda value: parse_operation_date(value) or date.min
    )
    work["_sort_idx"] = range(len(work))
    work = work.sort_values(["_sort_op", "_sort_idx"], ascending=[True, True], kind="mergesort")
    return work.drop(columns=["_sort_op", "_sort_idx"]).reset_index(drop=True)


def sort_runs_for_export(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    work = df.copy()
    work["_sort_started"] = work["started_at"].map(_parse_sort_datetime)
    work["_sort_idx"] = range(len(work))
    work = work.sort_values(["_sort_started", "_sort_idx"], ascending=[True, True], kind="mergesort")
    return work.drop(columns=["_sort_started", "_sort_idx"]).reset_index(drop=True)


def sort_hold_for_export(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or HOLD_ADDED_DATE_COLUMN not in df.columns:
        return df.reset_index(drop=True)
    work = df.copy()
    work["_sort_added"] = work[HOLD_ADDED_DATE_COLUMN].map(
        lambda value: parse_operation_date(value) or date.min
    )
    work["_sort_idx"] = range(len(work))
    work = work.sort_values(["_sort_added", "_sort_idx"], ascending=[True, True], kind="mergesort")
    return work.drop(columns=["_sort_added", "_sort_idx"]).reset_index(drop=True)


def parse_export_excel_datetime(value: object) -> datetime | None:
    """Parse a registry value into a naive datetime for Excel cells."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())

    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None

    parsed_dt = _try_parse_datetime(text)
    if parsed_dt is not None:
        return parsed_dt

    parsed_date = parse_operation_date(text)
    if parsed_date is not None:
        return datetime.combine(parsed_date, datetime.min.time())

    return None


def format_export_datetime_value(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, datetime):
        return value.strftime(EXPORT_DATETIME_DISPLAY)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).strftime(EXPORT_DATETIME_DISPLAY)

    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return ""

    parsed_dt = _try_parse_datetime(text)
    if parsed_dt is not None:
        return parsed_dt.strftime(EXPORT_DATETIME_DISPLAY)

    parsed_date = parse_operation_date(text)
    if parsed_date is not None:
        if " " in text or ":" in text:
            for fmt in (EXCEL_DATETIME_FORMAT, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(text.replace("Z", ""), fmt).strftime(
                        EXPORT_DATETIME_DISPLAY
                    )
                except ValueError:
                    continue
        return datetime.combine(parsed_date, datetime.min.time()).strftime(EXPORT_DATETIME_DISPLAY)

    return text


def prepare_export_frames(
    all_results: pd.DataFrame,
    runs: pd.DataFrame,
    hold: pd.DataFrame,
    otlezka: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    all_results = sort_all_results_for_export(all_results)
    runs = sort_runs_for_export(runs)
    hold = sort_hold_for_export(hold)
    return all_results, runs, hold, otlezka.reset_index(drop=True)


def _coerce_export_cell_value(
    col_name: str,
    value: object,
    datetime_columns: frozenset[str],
) -> tuple[object, bool]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "", False
    if col_name not in datetime_columns:
        return value, False

    parsed = parse_export_excel_datetime(value)
    if parsed is not None:
        return parsed, True
    text = str(value).strip()
    return (text if text else ""), False


def partners_from_otlezka(otlezka: pd.DataFrame) -> list[str]:
    if otlezka.empty or "partner" not in otlezka.columns:
        return []
    return sorted(
        {str(partner).strip() for partner in otlezka["partner"] if str(partner).strip()},
        key=str.casefold,
    )


def calendar_operation_dates_desc(all_results: pd.DataFrame) -> list[date]:
    if all_results.empty or OPERATION_DATE_COLUMN not in all_results.columns:
        return []

    parsed: list[date] = []
    for value in all_results[OPERATION_DATE_COLUMN]:
        op_date = parse_operation_date(value)
        if op_date is not None:
            parsed.append(op_date)
    if not parsed:
        return []

    min_date = min(parsed)
    max_date = max(parsed)
    dates: list[date] = []
    current = max_date
    while current >= min_date:
        dates.append(current)
        current -= timedelta(days=1)
    return dates


def build_statistics_sheet_rows(
    all_results: pd.DataFrame,
    otlezka: pd.DataFrame,
) -> list[list[object]]:
    partners = partners_from_otlezka(otlezka)
    dates = calendar_operation_dates_desc(all_results)
    rows: list[list[object]] = []
    rows.extend(
        _build_statistics_block(
            all_results,
            ACTION_REMOVE_PARTNER,
            STATS_REMOVE_TITLE,
            partners,
            dates,
        )
    )
    rows.append([])
    rows.extend(
        _build_statistics_block(
            all_results,
            ACTION_ADD_PARTNER,
            STATS_ADD_TITLE,
            partners,
            dates,
        )
    )
    return rows


def write_registry_export_workbook(
    local_path: Path,
    *,
    all_results: pd.DataFrame,
    runs: pd.DataFrame,
    hold: pd.DataFrame,
    otlezka: pd.DataFrame,
    readme_lines: list[str],
) -> None:
    stats_source = sort_all_results_for_export(all_results.copy())
    all_results, runs, hold, otlezka = prepare_export_frames(all_results, runs, hold, otlezka)

    local_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    default = wb.active
    if default is not None:
        wb.remove(default)

    _write_data_sheet(
        wb.create_sheet(EXPORT_SHEET_RESULTS),
        ALL_RESULTS_COLUMNS,
        all_results,
        frozenset(ALL_RESULTS_DATETIME_COLUMNS),
    )
    _write_data_sheet(
        wb.create_sheet(EXPORT_SHEET_RUNS),
        RUNS_COLUMNS,
        runs,
        frozenset(RUNS_DATETIME_COLUMNS),
    )
    _write_data_sheet(
        wb.create_sheet(EXPORT_SHEET_HOLD),
        HOLD_COLUMNS,
        hold,
        frozenset(HOLD_DATETIME_COLUMNS),
    )
    _write_data_sheet(wb.create_sheet(EXPORT_SHEET_OTLEZKA), OTLEZKA_COLUMNS, otlezka)
    _write_statistics_sheet(
        wb.create_sheet(EXPORT_SHEET_STATS),
        stats_source,
        otlezka,
    )
    _write_readme_sheet(wb.create_sheet(EXPORT_SHEET_README), readme_lines)

    _sanitize_workbook(wb)
    wb.save(local_path)
    wb.close()
    _strip_external_workbook_links(local_path)


def workbook_has_external_links(path: Path) -> bool:
    with zipfile.ZipFile(path) as archive:
        if any(name.startswith("xl/externalLinks/") for name in archive.namelist()):
            return True
        workbook_xml = archive.read("xl/workbook.xml").decode("utf-8", errors="replace")
        if "externalReference" in workbook_xml:
            return True
        for name in archive.namelist():
            if not name.startswith("xl/worksheets/sheet") or not name.endswith(".xml"):
                continue
            sheet_xml = archive.read(name).decode("utf-8", errors="replace")
            if "[all_results]" in sheet_xml.lower():
                return True
    return False


def _parse_sort_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    parsed = _try_parse_datetime(str(value).strip()) if value is not None else None
    if parsed is not None:
        return parsed.replace(tzinfo=None)
    parsed_date = parse_operation_date(value)
    if parsed_date is not None:
        return datetime.combine(parsed_date, datetime.min.time())
    return datetime.min


def _try_parse_datetime(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return parse_msk_datetime(text).replace(tzinfo=None)
    except ValueError:
        pass
    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        return parsed.replace(tzinfo=None)
    except ValueError:
        return None


def _build_statistics_block(
    all_results: pd.DataFrame,
    action: str,
    title: str,
    partners: list[str],
    dates: list[date],
) -> list[list[object]]:
    header = [STATS_PARTNER_HEADER, ""]
    header.extend(day.strftime(EXPORT_DATE_DISPLAY) for day in dates)
    header.append(STATS_TOTAL_HEADER)

    counts: dict[str, dict[date, int]] = {
        partner: dict.fromkeys(dates, 0) for partner in partners
    }
    if not all_results.empty and "action" in all_results.columns:
        subset = all_results[
            all_results["action"].astype(str).str.strip().str.lower() == action.lower()
        ]
        for _, row in subset.iterrows():
            partner = str(row.get("partner", "")).strip()
            op_date = parse_operation_date(row.get(OPERATION_DATE_COLUMN))
            if partner in counts and op_date in counts[partner]:
                counts[partner][op_date] += 1

    rows: list[list[object]] = [[title], header]
    column_totals = dict.fromkeys(dates, 0)
    grand_total = 0

    for partner in partners:
        partner_counts = counts[partner]
        row_total = sum(partner_counts.values())
        grand_total += row_total
        line = [partner, ""]
        for day in dates:
            count = partner_counts[day]
            column_totals[day] += count
            line.append(count)
        line.append(row_total)
        rows.append(line)

    total_row = [STATS_TOTAL_ROW_LABEL, ""]
    for day in dates:
        total_row.append(column_totals[day])
    total_row.append(grand_total)
    rows.append(total_row)
    return rows


def _write_data_sheet(
    ws: Worksheet,
    columns: list[str],
    df: pd.DataFrame,
    datetime_columns: frozenset[str] | None = None,
) -> None:
    max_row = max(len(df) + 1, 1)
    max_col = len(columns)
    datetime_columns = datetime_columns or frozenset()

    for col_idx, name in enumerate(columns, 1):
        ws.cell(row=1, column=col_idx, value=name)

    for row_offset in range(len(df)):
        row_idx = row_offset + 2
        for col_idx, col_name in enumerate(columns, 1):
            raw_value = df.iloc[row_offset].get(col_name, "")
            cell_value, is_datetime = _coerce_export_cell_value(
                col_name,
                raw_value,
                datetime_columns,
            )
            cell = ws.cell(row=row_idx, column=col_idx, value=cell_value)
            if is_datetime:
                cell.number_format = EXPORT_EXCEL_DATETIME_NUMBER_FORMAT

    _apply_sheet_formatting(
        ws,
        max_row=max_row,
        max_col=max_col,
        autofilter=ws.title
        in {EXPORT_SHEET_RESULTS, EXPORT_SHEET_RUNS, EXPORT_SHEET_HOLD, EXPORT_SHEET_OTLEZKA},
    )


def _write_statistics_sheet(
    ws: Worksheet,
    all_results: pd.DataFrame,
    otlezka: pd.DataFrame,
) -> None:
    rows = build_statistics_sheet_rows(all_results, otlezka)
    max_row = 0
    max_col = 0
    for row_idx, row in enumerate(rows, 1):
        max_row = max(max_row, row_idx)
        if not row:
            continue
        for col_idx, value in enumerate(row, 1):
            max_col = max(max_col, col_idx)
            ws.cell(row=row_idx, column=col_idx, value=value)

    if max_row == 0 or max_col == 0:
        return

    for row_idx in range(1, max_row + 1):
        first = ws.cell(row=row_idx, column=1).value
        if first in {STATS_REMOVE_TITLE, STATS_ADD_TITLE, STATS_TOTAL_ROW_LABEL}:
            for col_idx in range(1, max_col + 1):
                ws.cell(row=row_idx, column=col_idx).font = _BOLD

    header_rows = [
        row_idx
        for row_idx in range(1, max_row + 1)
        if ws.cell(row=row_idx, column=1).value == STATS_PARTNER_HEADER
    ]
    for row_idx in header_rows:
        for col_idx in range(1, max_col + 1):
            ws.cell(row=row_idx, column=col_idx).font = _BOLD

    _apply_sheet_formatting(ws, max_row=max_row, max_col=max_col, autofilter=False)


def _write_readme_sheet(ws: Worksheet, lines: list[str]) -> None:
    max_row = max(len(lines), 1)
    for row_idx, line in enumerate(lines, 1):
        ws.cell(row=row_idx, column=1, value=line)
    _apply_sheet_formatting(ws, max_row=max_row, max_col=1, autofilter=False)


def _apply_sheet_formatting(
    ws: Worksheet,
    *,
    max_row: int,
    max_col: int,
    autofilter: bool,
) -> None:
    if max_row < 1 or max_col < 1:
        return

    for col_idx in range(1, max_col + 1):
        ws.cell(row=1, column=col_idx).font = _BOLD

    for row_idx in range(1, max_row + 1):
        for col_idx in range(1, max_col + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = _ALL_BORDERS
            if cell.alignment is None or cell.alignment.horizontal is None:
                cell.alignment = Alignment(vertical="center")

    _apply_column_widths(ws, max_col=max_col)
    ws.freeze_panes = "A2" if max_row >= 2 else None
    if autofilter and max_row >= 1:
        ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{max_row}"


def _apply_column_widths(ws: Worksheet, *, max_col: int) -> None:
    for col_idx in range(1, max_col + 1):
        letter = get_column_letter(col_idx)
        max_len = 0
        for row_idx in range(1, ws.max_row + 1):
            value = ws.cell(row=row_idx, column=col_idx).value
            if value is None:
                continue
            max_len = max(max_len, len(str(value)))
        ws.column_dimensions[letter].width = min(max(max_len + 2, 8), MAX_COLUMN_WIDTH)


def _sanitize_workbook(wb: Workbook) -> None:
    """Ensure export workbook contains values only and no external link metadata."""
    if hasattr(wb, "defined_names") and wb.defined_names is not None:
        for name in list(wb.defined_names):
            attr_text = getattr(wb.defined_names[name], "attr_text", "") or ""
            if "!" in attr_text or "[" in attr_text:
                del wb.defined_names[name]

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.value = ""
                    cell.data_type = "s"


def _strip_external_workbook_links(path: Path) -> None:
    """Remove external workbook link parts from a saved xlsx package."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(path, "r") as source:
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                if item.filename.startswith("xl/externalLinks/"):
                    continue
                data = source.read(item.filename)
                if item.filename == "xl/workbook.xml":
                    text = data.decode("utf-8")
                    text = re.sub(
                        r"<externalReferences>.*?</externalReferences>",
                        "",
                        text,
                        flags=re.DOTALL,
                    )
                    text = re.sub(r"<externalReference[^>]*/>", "", text)
                    data = text.encode("utf-8")
                elif item.filename == "xl/_rels/workbook.xml.rels":
                    text = data.decode("utf-8")
                    text = re.sub(
                        r'<Relationship[^>]*Type="[^"]*/externalLink"[^>]*/>',
                        "",
                        text,
                    )
                    data = text.encode("utf-8")
                target.writestr(item, data)
    path.write_bytes(buffer.getvalue())
