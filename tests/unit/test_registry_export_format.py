"""Unit tests for registry export workbook formatting."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from integrations.wallet_editor_registry_db.registry_export_builder import (
    RegistryExportSummary,
    format_registry_export_summary,
)
from integrations.wallet_editor_registry_db.registry_export_format import (
    EXPORT_EXCEL_DATETIME_NUMBER_FORMAT,
    EXPORT_SHEET_HOLD,
    EXPORT_SHEET_README,
    EXPORT_SHEET_RESULTS,
    EXPORT_SHEET_RUNS,
    EXPORT_SHEET_STATS,
    MAX_COLUMN_WIDTH,
    STATS_ADD_TITLE,
    STATS_REMOVE_TITLE,
    STATS_TOTAL_HEADER,
    STATS_TOTAL_ROW_LABEL,
    build_statistics_sheet_rows,
    calendar_operation_dates_desc,
    format_export_datetime_value,
    partners_from_otlezka,
    prepare_export_frames,
    sort_all_results_for_export,
    sort_runs_for_export,
    workbook_has_external_links,
    write_registry_export_workbook,
)
from integrations.wallet_editor_registry_lifecycle import (
    ACTION_ADD_PARTNER,
    ACTION_REMOVE_PARTNER,
    ALL_RESULTS_COLUMNS,
    HOLD_COLUMNS,
    OPERATION_DATE_COLUMN,
    OTLEZKA_COLUMNS,
    RUNS_COLUMNS,
)


def _all_results_row(**overrides) -> dict:
    row = {col: "" for col in ALL_RESULTS_COLUMNS}
    row.update(overrides)
    return row


def _sample_frames():
    all_results = pd.DataFrame(
        [
            _all_results_row(
                **{
                    OPERATION_DATE_COLUMN: "02.06.2026 10:00:00",
                    "partner": "Beta",
                    "action": ACTION_REMOVE_PARTNER,
                }
            ),
            _all_results_row(
                **{
                    OPERATION_DATE_COLUMN: "01.06.2026 09:00:00",
                    "partner": "Alpha",
                    "action": ACTION_REMOVE_PARTNER,
                }
            ),
            _all_results_row(
                **{
                    OPERATION_DATE_COLUMN: "01.06.2026 11:00:00",
                    "partner": "Alpha",
                    "action": ACTION_ADD_PARTNER,
                }
            ),
            _all_results_row(
                **{
                    OPERATION_DATE_COLUMN: "03.06.2026 12:00:00",
                    "partner": "Gamma",
                    "action": ACTION_ADD_PARTNER,
                }
            ),
        ]
    )
    runs = pd.DataFrame(
        [
            {col: "" for col in RUNS_COLUMNS}
            | {
                "started_at": "02.06.2026 08:00:00",
                "finished_at": "02.06.2026 08:05:00",
            },
            {col: "" for col in RUNS_COLUMNS}
            | {
                "started_at": "01.06.2026 07:00:00",
                "finished_at": "01.06.2026 07:05:00",
            },
        ]
    )
    hold = pd.DataFrame(
        [
            {
                "Дата добавления": "02.06.2026",
                "card": "4111",
                "partner": "Beta",
                "comment": "",
            },
            {
                "Дата добавления": "01.06.2026",
                "card": "4222",
                "partner": "Alpha",
                "comment": "",
            },
        ]
    )
    otlezka = pd.DataFrame(
        [
            {"partner": "Gamma", "Полные дни": 30, "comment": ""},
            {"partner": "Beta", "Полные дни": 30, "comment": ""},
            {"partner": "Alpha", "Полные дни": 30, "comment": ""},
            {"partner": "Delta", "Полные дни": 30, "comment": ""},
        ]
    )
    return all_results, runs, hold, otlezka


class TestExportSortingAndFormatting:
    def test_sort_all_results_by_operation_date_ascending(self):
        all_results, _, _, _ = _sample_frames()
        sorted_df = sort_all_results_for_export(all_results)
        dates = sorted_df[OPERATION_DATE_COLUMN].tolist()
        assert dates[0].startswith("01.06.2026")
        assert dates[-1].startswith("03.06.2026")

    def test_sort_runs_by_started_at_ascending(self):
        _, runs, _, _ = _sample_frames()
        sorted_df = sort_runs_for_export(runs)
        assert sorted_df.iloc[0]["started_at"].startswith("01.06.2026")
        assert sorted_df.iloc[1]["started_at"].startswith("02.06.2026")

    def test_format_export_datetime_value(self):
        assert format_export_datetime_value("2026-06-03T14:30:45+03:00") == "03.06.2026 14:30:45"
        assert format_export_datetime_value("03.06.2026") == "03.06.2026 00:00:00"
        assert (
            format_export_datetime_value(datetime(2026, 6, 3, 14, 30, 45))
            == "03.06.2026 14:30:45"
        )

    def test_prepare_export_frames_preserves_raw_datetime_values(self):
        all_results, runs, hold, _ = _sample_frames()
        prepared_all, prepared_runs, prepared_hold, _ = prepare_export_frames(
            all_results,
            runs,
            hold,
            pd.DataFrame(columns=OTLEZKA_COLUMNS),
        )
        assert prepared_all.iloc[0][OPERATION_DATE_COLUMN] == "01.06.2026 09:00:00"
        assert prepared_runs.iloc[0]["started_at"] == "01.06.2026 07:00:00"
        assert prepared_hold.iloc[0]["Дата добавления"] == "01.06.2026"


class TestStatisticsSheet:
    def test_partners_from_otlezka_alphabetical(self):
        _, _, _, otlezka = _sample_frames()
        assert partners_from_otlezka(otlezka) == ["Alpha", "Beta", "Delta", "Gamma"]

    def test_calendar_dates_have_no_gaps(self):
        all_results, _, _, _ = _sample_frames()
        dates = calendar_operation_dates_desc(all_results)
        assert dates == [date(2026, 6, 3), date(2026, 6, 2), date(2026, 6, 1)]

    def test_build_statistics_uses_otlezka_partners_and_zeros(self):
        all_results, _, _, otlezka = _sample_frames()
        rows = build_statistics_sheet_rows(all_results, otlezka)
        add_idx = next(i for i, row in enumerate(rows) if row and row[0] == STATS_ADD_TITLE)
        remove_header = next(row for row in rows if row and row[0] == "Партнёр")
        add_header = rows[add_idx + 1]
        assert remove_header[2:5] == add_header[2:5] == [
            "03.06.2026",
            "02.06.2026",
            "01.06.2026",
        ]
        delta_row = next(row for row in rows[:add_idx] if row and row[0] == "Delta")
        assert delta_row[2:] == [0, 0, 0, 0]
        assert STATS_REMOVE_TITLE in {row[0] for row in rows if row}
        assert STATS_ADD_TITLE in {row[0] for row in rows if row}

    def test_statistics_totals_and_vsego_column(self):
        all_results, _, _, otlezka = _sample_frames()
        rows = build_statistics_sheet_rows(all_results, otlezka)
        add_idx = next(i for i, row in enumerate(rows) if row and row[0] == STATS_ADD_TITLE)
        remove_total = next(
            row
            for row in rows[:add_idx]
            if row and row[0] == STATS_TOTAL_ROW_LABEL
        )
        assert remove_total[-1] == 2


class TestExportWorkbookPresentation:
    def test_workbook_has_russian_sheets_and_formatting(self, tmp_path):
        all_results, runs, hold, otlezka = _sample_frames()
        output = tmp_path / "export.xlsx"
        write_registry_export_workbook(
            output,
            all_results=all_results,
            runs=runs,
            hold=hold,
            otlezka=otlezka,
            readme_lines=["Реестр WalletEditor", "PostgreSQL", "/registry_export"],
        )

        assert workbook_has_external_links(output) is False

        wb = load_workbook(output)
        assert wb.sheetnames == [
            EXPORT_SHEET_RESULTS,
            EXPORT_SHEET_RUNS,
            "Hold",
            "Отлёжка",
            EXPORT_SHEET_STATS,
            EXPORT_SHEET_README,
        ]

        results_ws = wb[EXPORT_SHEET_RESULTS]
        assert results_ws.freeze_panes == "A2"
        assert results_ws.auto_filter.ref is not None
        assert results_ws.cell(row=1, column=1).font.bold is True
        assert isinstance(results_ws.cell(row=2, column=1).value, datetime)
        assert results_ws.cell(row=2, column=1).border.left.style == "thin"
        assert results_ws.cell(row=2, column=5).border.left.style == "thin"

        stats_ws = wb[EXPORT_SHEET_STATS]
        assert stats_ws.cell(row=1, column=1).value == STATS_REMOVE_TITLE
        assert stats_ws.cell(row=5, column=1).value == "Delta"
        assert stats_ws.cell(row=5, column=3).value == 0

        readme_ws = wb[EXPORT_SHEET_README]
        assert "Реестр WalletEditor" in readme_ws.cell(row=1, column=1).value
        assert readme_ws.auto_filter.ref is None

        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    assert cell.data_type != "f"
            for col_idx in range(1, ws.max_column + 1):
                width = ws.column_dimensions[get_column_letter(col_idx)].width
                if width is not None:
                    assert width <= MAX_COLUMN_WIDTH

        wb.close()

    def test_column_width_capped_at_50(self, tmp_path):
        all_results, runs, hold, otlezka = _sample_frames()
        all_results.at[0, "comment"] = "x" * 100
        write_registry_export_workbook(
            tmp_path / "wide.xlsx",
            all_results=all_results,
            runs=runs,
            hold=hold,
            otlezka=otlezka,
            readme_lines=["README"],
        )
        wb = load_workbook(tmp_path / "wide.xlsx")
        comment_col = ALL_RESULTS_COLUMNS.index("comment") + 1
        width = wb[EXPORT_SHEET_RESULTS].column_dimensions[
            get_column_letter(comment_col)
        ].width
        assert width <= MAX_COLUMN_WIDTH
        wb.close()


class TestExcelDateTimeCells:
    @staticmethod
    def _column_index(columns: list[str], name: str) -> int:
        return columns.index(name) + 1

    def test_results_datetime_columns_are_excel_datetime(self, tmp_path):
        all_results, runs, hold, otlezka = _sample_frames()
        all_results.at[0, "Дата отключения"] = "01.06.2026 08:00:00"
        all_results.at[0, "Дата включения"] = "02.06.2026 09:00:00"
        output = tmp_path / "export.xlsx"
        write_registry_export_workbook(
            output,
            all_results=all_results,
            runs=runs,
            hold=hold,
            otlezka=otlezka,
            readme_lines=["README"],
        )
        wb = load_workbook(output)
        ws = wb[EXPORT_SHEET_RESULTS]
        data_row = 4  # 02.06.2026 row after ascending sort
        for col_name in (
            OPERATION_DATE_COLUMN,
            "Дата отключения",
            "Дата включения",
        ):
            col_idx = self._column_index(ALL_RESULTS_COLUMNS, col_name)
            cell = ws.cell(row=data_row, column=col_idx)
            assert isinstance(cell.value, datetime), col_name
            assert cell.data_type == "d", col_name
            assert cell.number_format == EXPORT_EXCEL_DATETIME_NUMBER_FORMAT, col_name
        wb.close()

    def test_runs_datetime_columns_are_excel_datetime(self, tmp_path):
        all_results, runs, hold, otlezka = _sample_frames()
        output = tmp_path / "export.xlsx"
        write_registry_export_workbook(
            output,
            all_results=all_results,
            runs=runs,
            hold=hold,
            otlezka=otlezka,
            readme_lines=["README"],
        )
        wb = load_workbook(output)
        ws = wb[EXPORT_SHEET_RUNS]
        for col_name in ("started_at", "finished_at"):
            col_idx = self._column_index(RUNS_COLUMNS, col_name)
            cell = ws.cell(row=2, column=col_idx)
            assert isinstance(cell.value, datetime)
            assert cell.data_type == "d"
            assert cell.number_format == EXPORT_EXCEL_DATETIME_NUMBER_FORMAT
        wb.close()

    def test_hold_added_date_is_excel_datetime(self, tmp_path):
        all_results, runs, hold, otlezka = _sample_frames()
        output = tmp_path / "export.xlsx"
        write_registry_export_workbook(
            output,
            all_results=all_results,
            runs=runs,
            hold=hold,
            otlezka=otlezka,
            readme_lines=["README"],
        )
        wb = load_workbook(output)
        ws = wb[EXPORT_SHEET_HOLD]
        col_idx = self._column_index(HOLD_COLUMNS, "Дата добавления")
        cell = ws.cell(row=2, column=col_idx)
        assert isinstance(cell.value, datetime)
        assert cell.data_type == "d"
        assert cell.number_format == EXPORT_EXCEL_DATETIME_NUMBER_FORMAT
        wb.close()

    def test_invalid_date_stays_text_and_export_succeeds(self, tmp_path):
        all_results, runs, hold, otlezka = _sample_frames()
        all_results.at[1, "Дата включения"] = "Нет даты отлёжки"
        output = tmp_path / "export.xlsx"
        write_registry_export_workbook(
            output,
            all_results=all_results,
            runs=runs,
            hold=hold,
            otlezka=otlezka,
            readme_lines=["README"],
        )
        wb = load_workbook(output)
        ws = wb[EXPORT_SHEET_RESULTS]
        col_idx = self._column_index(ALL_RESULTS_COLUMNS, "Дата включения")
        cell = ws.cell(row=2, column=col_idx)
        assert cell.value == "Нет даты отлёжки"
        assert cell.data_type == "s"
        wb.close()


class TestTelegramSummaryFormatting:
    def test_summary_uses_display_datetime_format(self):
        summary = RegistryExportSummary(
            all_results_rows=1,
            runs_rows=1,
            hold_rows=1,
            otlezka_rows=1,
            last_manual_sync_at="2026-07-01T18:40:27.388258+00:00",
            snapshot_hash_short="abc",
            manual_sync_degraded=False,
            generated_at="2026-07-01T22:28:42+03:00",
            filename="wallet_editor_export.xlsx",
        )
        text = format_registry_export_summary(summary)
        assert "generated at: 01.07.2026 22:28:42" in text
        assert "last manual sync: 01.07.2026 18:40:27" in text
        assert "T" not in text.split("generated at:")[1].splitlines()[0]
