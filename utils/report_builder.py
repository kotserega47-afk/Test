# utils/report_builder.py
import pandas as pd

def build_report(result: dict, report_path: str):
    with pd.ExcelWriter(report_path, engine='openpyxl') as writer:
        for sheet_name, df in result.get("data_sheets", {}).items():
            if df is not None and not df.empty:
                df.to_excel(writer, sheet_name=sheet_name, index=False)

        summary_df = pd.DataFrame([result.get("summary", {})])
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
