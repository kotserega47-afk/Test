from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from core.rules_v2.bridge_legacy import (
    build_snapshot_v2_from_legacy,
    load_legacy_workbook,
)
from core.rules_v2.indexes import RulesIndexes, build_indexes
from core.rules_v2.models import RulesSnapshotV2


DEFAULT_RULES_XLSX = "rules.xlsx"


def _resolve_rules_path(path: str | Path | None = None) -> Path:
    if path is not None:
        p = Path(path)
    else:
        env_path = (os.getenv("RULES_XLSX_PATH") or "").strip()
        p = Path(env_path) if env_path else Path(DEFAULT_RULES_XLSX)

    return p.expanduser().resolve()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    return out


def read_rules_excel(
    path: str | Path | None = None,
    *,
    only_sheets: Iterable[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Читает rules.xlsx как набор DataFrame'ов.
    Это low-level helper для диагностики и bridge/debug сценариев.

    Parameters
    ----------
    path:
        Путь к workbook. Если не передан, берётся RULES_XLSX_PATH или rules.xlsx.
    only_sheets:
        Опционально — ограничить набор листов.

    Returns
    -------
    dict[str, pd.DataFrame]
        Ключ = имя листа из workbook, значение = DataFrame с подрезанными именами колонок.
    """
    resolved = _resolve_rules_path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"rules.xlsx not found: {resolved}")

    sheets = load_legacy_workbook(resolved)

    if only_sheets is not None:
        wanted = {str(x).strip() for x in only_sheets if str(x).strip()}
        sheets = {name: df for name, df in sheets.items() if name in wanted}

    return {name: _normalize_columns(df) for name, df in sheets.items()}


@dataclass(slots=True)
class RulesWorkbook:
    path: Path
    sheets: dict[str, pd.DataFrame]

    def get_sheet(self, sheet_name: str, default: pd.DataFrame | None = None) -> pd.DataFrame | None:
        return self.sheets.get(sheet_name, default)

    @property
    def sheet_names(self) -> list[str]:
        return list(self.sheets.keys())


class RulesLoader:
    """
    Тонкий фасад над v2 bridge.

    Не занимается ручным парсингом листов в модели.
    Единственный источник truth:
    - read_rules_excel() -> raw DataFrame sheets
    - build_snapshot_v2_from_legacy() -> RulesSnapshotV2
    """

    def __init__(self, path: str | Path | None = None):
        self.path = _resolve_rules_path(path)

    def load_workbook(
        self,
        *,
        only_sheets: Iterable[str] | None = None,
    ) -> RulesWorkbook:
        sheets = read_rules_excel(self.path, only_sheets=only_sheets)
        return RulesWorkbook(path=self.path, sheets=sheets)

    def load_snapshot(self) -> RulesSnapshotV2:
        if not self.path.exists():
            raise FileNotFoundError(f"rules.xlsx not found: {self.path}")
        return build_snapshot_v2_from_legacy(self.path)

    def load_indexes(self) -> RulesIndexes:
        snapshot = self.load_snapshot()
        return build_indexes(snapshot)

    def load(self) -> RulesSnapshotV2:
        """
        Backward-compatible alias.
        """
        return self.load_snapshot()