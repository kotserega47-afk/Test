from __future__ import annotations

from pathlib import Path

import pandas as pd


def collect_unique_values(df: pd.DataFrame, column: str) -> list[str]:
    if column not in df.columns:
        return []
    values = (
        df[column]
        .dropna()
        .astype(str)
        .map(str.strip)
    )
    return sorted(v for v in values.unique().tolist() if v)


def main() -> None:
    path = Path("rules.xlsx")
    xls = pd.ExcelFile(path)

    result: dict[str, list[str]] = {}

    for sheet in xls.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet).rename(columns=lambda c: str(c).strip())

        for col in ["partner", "scope_value", "source_partners", "group_name", "method", "command"]:
            vals = collect_unique_values(df, col)
            if vals:
                result[f"{sheet}.{col}"] = vals

    out_rows = []
    for source, values in result.items():
        for v in values:
            out_rows.append({"source": source, "raw_value": v})

    out_df = pd.DataFrame(out_rows)
    out_df.to_excel("migration_identities.xlsx", index=False)

    print("Saved migration_identities.xlsx")


if __name__ == "__main__":
    main()