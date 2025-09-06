# utils/report_builder.py
import pandas as pd

def build_report(result: dict, output_path: str):
    """
    Создаёт Excel-отчёт с двумя листами:
    - Summary: ключевые показатели
    - Data: исходная таблица
    """
    if "error" in result:
        raise ValueError(f"Невозможно создать отчёт: {result['error']}")

    summary = result.get("summary", {})
    df_data = result.get("data")

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # Лист Summary
        df_summary = pd.DataFrame(list(summary.items()), columns=["Metric", "Value"])
        df_summary.to_excel(writer, sheet_name="Summary", index=False)

        # Лист Data
        df_data.to_excel(writer, sheet_name="Data", index=False)
