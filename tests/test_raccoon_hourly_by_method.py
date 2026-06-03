"""Unit tests for Raccoon 10-min payin report grouped by method."""
import os
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:ABCDEF-test-token-for-unittest")
os.environ.setdefault("TELEGRAM_CHAT_ID_HOURLY_RACCOON", "-payin-10m-chat")
os.environ.setdefault("TELEGRAM_CHAT_ID_RACCOON_WALLET", "-hourly-wallet-chat")

import pandas as pd

from analyzers.raccoon_hourly_report import (
    METHOD_EMPTY_LABEL,
    _calc_fingerprint,
    _method_display,
    aggregate_payin_by_method,
    fmt_int,
    format_report,
)
from utils.normalization import normalize_partner_name

MSK = ZoneInfo("Europe/Moscow")


def _df(rows: list[dict]) -> pd.DataFrame:
    """Минимальный payin-подобный frame после prepare_data."""
    out = []
    for r in rows:
        partner = r["Партнер"]
        out.append({
            "Партнер": partner,
            "norm": r.get("norm", normalize_partner_name(partner)),
            "Сумма": r["Сумма"],
            "method_display": r.get("method_display", _method_display(r.get("Метод пополнения"))),
            "Дата/Время создания": r.get(
                "Дата/Время создания",
                datetime(2026, 5, 20, 12, 0, tzinfo=MSK),
            ),
        })
    return pd.DataFrame(out)


class TestAggregateByMethod(unittest.TestCase):
    def test_group_by_method_and_partner(self):
        df = _df([
            {"Партнер": "A (1)", "Сумма": 100, "method_display": "SBP"},
            {"Партнер": "B (2)", "Сумма": 50, "method_display": "SBP"},
        ])
        blocks = aggregate_payin_by_method(df)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["method"], "SBP")
        self.assertEqual(blocks[0]["total"], 150)
        self.assertEqual(sum(p["amount"] for p in blocks[0]["partners"]), 150)

    def test_methods_sorted_by_total_desc(self):
        df = _df([
            {"Партнер": "A (1)", "Сумма": 10, "method_display": "Low"},
            {"Партнер": "B (2)", "Сумма": 100, "method_display": "High"},
        ])
        blocks = aggregate_payin_by_method(df)
        self.assertEqual([b["method"] for b in blocks], ["High", "Low"])

    def test_partners_sorted_by_amount_desc(self):
        df = _df([
            {"Партнер": "Small (1)", "Сумма": 10, "method_display": "M"},
            {"Партнер": "Big (2)", "Сумма": 90, "method_display": "M"},
        ])
        partners = aggregate_payin_by_method(df)[0]["partners"]
        self.assertEqual([p["title"] for p in partners], ["Big (2)", "Small (1)"])

    def test_same_partner_in_two_methods_not_collapsed(self):
        norm = normalize_partner_name("Same (1)")
        df = _df([
            {"Партнер": "Same (1)", "norm": norm, "Сумма": 10, "method_display": "M1"},
            {"Партнер": "Same (1)", "norm": norm, "Сумма": 20, "method_display": "M2"},
        ])
        blocks = aggregate_payin_by_method(df)
        self.assertEqual(len(blocks), 2)
        for b in blocks:
            self.assertEqual(len(b["partners"]), 1)
            self.assertEqual(b["partners"][0]["title"], "Same (1)")
        amounts = {b["method"]: b["partners"][0]["amount"] for b in blocks}
        self.assertEqual(amounts, {"M2": 20, "M1": 10})

    def test_empty_method_rendered_as_without_method(self):
        self.assertEqual(_method_display(None), METHOD_EMPTY_LABEL)
        self.assertEqual(_method_display(""), METHOD_EMPTY_LABEL)
        self.assertEqual(_method_display(float("nan")), METHOD_EMPTY_LABEL)
        df = _df([{"Партнер": "A (1)", "Сумма": 1, "Метод пополнения": None}])
        blocks = aggregate_payin_by_method(df)
        self.assertEqual(blocks[0]["method"], METHOD_EMPTY_LABEL)


class TestFormatReport(unittest.TestCase):
    def _render(self, rows: list[dict]) -> str:
        df = _df(rows)
        blocks = aggregate_payin_by_method(df)
        end = datetime(2026, 5, 20, 14, 30, tzinfo=MSK)
        return format_report(blocks, date(2026, 5, 20), end, df["Сумма"].sum())

    def test_partner_not_in_yaml_still_rendered(self):
        txt = self._render([{"Партнер": "NotInYaml (999)", "Сумма": 150000, "method_display": "Card"}])
        self.assertIn("NotInYaml (999)", txt)
        self.assertIn("150 000", txt)
        self.assertNotIn("❓ Не в конфиге", txt)
        self.assertNotIn("Поступления:", txt)

    def test_amount_format_without_decimals_currency(self):
        txt = self._render([{"Партнер": "A (1)", "Сумма": 150000, "method_display": "M"}])
        self.assertIn("150 000", txt)
        self.assertNotIn("150000.75", txt)
        self.assertNotIn("₽", txt)
        self.assertNotIn("RUB", txt)

    def test_same_partner_two_methods_in_message(self):
        norm = normalize_partner_name("Same (1)")
        txt = self._render([
            {"Партнер": "Same (1)", "norm": norm, "Сумма": 10, "method_display": "Alpha"},
            {"Партнер": "Same (1)", "norm": norm, "Сумма": 20, "method_display": "Beta"},
        ])
        self.assertEqual(txt.count("Same (1)"), 2)
        compact = txt.replace(" ", "")
        self.assertIn("Beta—20", compact)
        self.assertIn("Alpha—10", compact)


class TestFmtInt(unittest.TestCase):
    def test_amount_format_helper(self):
        self.assertEqual(fmt_int(150000), "150 000")


class TestFingerprintRegression(unittest.TestCase):
    def _fp(self, rows: list[dict], day=None):
        df = _df(rows)
        return _calc_fingerprint(df, None, day or date(2026, 5, 20))

    def test_same_rows_same_hash(self):
        rows = [
            {"Партнер": "A (1)", "Сумма": 100},
            {"Партнер": "B (2)", "Сумма": 50, "method_display": "SBP"},
        ]
        h1 = self._fp(rows)["hash"]
        h2 = self._fp(rows)["hash"]
        self.assertEqual(h1, h2)

    def test_new_paid_row_changed_hash(self):
        base = [{"Партнер": "A (1)", "Сумма": 100}]
        extra = base + [
            {
                "Партнер": "C (3)",
                "Сумма": 1,
                "Дата/Время создания": datetime(2026, 5, 20, 13, 0, tzinfo=MSK),
            }
        ]
        self.assertNotEqual(self._fp(base)["hash"], self._fp(extra)["hash"])


if __name__ == "__main__":
    unittest.main()
