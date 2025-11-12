# utils/report_builder.py
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.styles import Font, PatternFill
from openpyxl.drawing.image import Image
from io import BytesIO
# utils/report_builder.py
def build_report(result: dict, report_path: str):
    """
    Сохраняет готовую Excel-книгу, собранную в analyzers/conversion.py
    """
    wb = result.get("workbook")
    if wb is None:
        raise ValueError("❌ В result нет 'workbook' для сохранения отчёта")

    wb.save(report_path)