# utils/report_builder.py
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.styles import Font, PatternFill
from openpyxl.drawing.image import Image
import matplotlib.pyplot as plt
from io import BytesIO

def build_report(result: dict, report_path: str):
    wb = Workbook()
    wb.remove(wb.active)

    # -----------------
    # Добавляем листы с данными
    # -----------------
    for sheet_name, df in result.get("data_sheets", {}).items():
        if df is None or df.empty:
            continue
        ws = wb.create_sheet(sheet_name)
        for r in dataframe_to_rows(df, index=False, header=True):
            ws.append(r)
        for cell in ws[1]:
            cell.font = Font(bold=True)

    # -----------------
    # Summary
    # -----------------
    summary_df = pd.DataFrame([result.get("summary", {})])
    ws_sum = wb.create_sheet("Summary")
    for r in dataframe_to_rows(summary_df, index=False, header=True):
        ws_sum.append(r)
    for cell in ws_sum[1]:
        cell.font = Font(bold=True)

    # -----------------
    # Stat - добавляем графики
    # -----------------
    stat_df = result.get("data_sheets", {}).get("Stat")
    if stat_df is not None and not stat_df.empty:
        ws_stat = wb["Stat"]
        # графики Сбер и Тинь
        for i, row in stat_df.iterrows():
            for bank in ["Сбер", "Тинь"]:
                errs = row[f"{bank} Ошибки"]
                succ = row[f"{bank} Успешно"]
                fig, ax = plt.subplots(figsize=(2, 0.2))
                ax.barh([0], [errs], color='red')
                ax.barh([0], [succ], left=[errs], color='green')
                ax.set_axis_off()
                buf = BytesIO()
                plt.savefig(buf, format='png', bbox_inches='tight', transparent=True)
                plt.close(fig)
                img = Image(buf)
                col = ws_stat.max_column + 1
                ws_stat.add_image(img, f"{chr(65 + col)}{i+2}")

    wb.save(report_path)
