from __future__ import annotations

import pandas as pd

from core.rules_v2.models import RulesSnapshot


class RulesLoader:

    def __init__(self, path: str):
        self.path = path

    def load(self) -> RulesSnapshot:

        xls = pd.ExcelFile(self.path)

        meta = self._load_meta(xls)
        partners = self._load_partners(xls)

        return RulesSnapshot(
            meta=meta,
            partners=partners,
            partner_groups={},
            methods={},
            limits={},
            thresholds={},
            exclusions={},
            reports={},
            access={},
        )

    def _load_meta(self, xls):

        if "meta" not in xls.sheet_names:
            return {}

        df = pd.read_excel(xls, "meta")

        result = {}

        for _, row in df.iterrows():
            key = str(row["key"]).strip()
            value = row["value"]

            result[key] = value

        return result

    def _load_partners(self, xls):

        if "partners" not in xls.sheet_names:
            return {}

        df = pd.read_excel(xls, "partners")

        result = {}

        for _, row in df.iterrows():

            key = str(row["partner_code"]).strip().lower()

            result[key] = {
                "display_name": row.get("display_name"),
                "enabled": row.get("enabled", 1),
            }

        return result