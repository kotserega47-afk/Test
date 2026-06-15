"""Unit tests for Bakai rate parser helpers and scoped widget scraping."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from integrations import bakai_monitor_playwright as bakai


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.145", "1.145"),
        ("1,145", "1.145"),
        ("1\xa0.145", "1.145"),
        (" 1 145 ", "1145"),
        ("87.000", "87.000"),
    ],
)
def test_normalize_rate_text(raw: str, expected: str) -> None:
    assert bakai.normalize_rate_text(raw) == expected


@pytest.mark.parametrize(
    ("buy_text", "expected"),
    [
        ("1.145", 1.145),
        ("1,145", 1.145),
        ("1\xa0.145", 1.145),
        ("  87.000  ", 87.0),
    ],
)
def test_parse_buy_rate_text(buy_text: str, expected: float) -> None:
    assert bakai.parse_buy_rate_text(buy_text) == expected


def test_parse_buy_rate_text_fails_with_clear_stage() -> None:
    with pytest.raises(RuntimeError, match="buy rate parse failed"):
        bakai.parse_buy_rate_text("n/a")


BAKAI_WIDGET_HTML = """
<!DOCTYPE html>
<html><body style="height:3000px">
<div style="height:2800px"></div>
<div class="CurrencyWidget_widget_content">
  <select>
    <option value="cash">cash</option>
    <option value="transfer">transfer</option>
  </select>
  <table>
    <thead><tr><th></th><th>buy</th><th>sell</th></tr></thead>
    <tbody>
      <tr>
        <th><img src="/assets/images/calculator/currency_usd.svg" alt="USD"></th>
        <th>87.000</th><th>87.500</th>
      </tr>
      <tr>
        <th><img src="/assets/images/calculator/currency_eur.svg" alt="EUR"></th>
        <th>101.100</th><th>102.100</th>
      </tr>
      <tr>
        <th><img src="/assets/images/calculator/currency_rub.svg" alt="RUB"></th>
        <th>1.145</th><th>1.245</th>
      </tr>
    </tbody>
  </table>
</div>
</body></html>
"""


def test_scrape_rub_buy_rate_from_fixture_html() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.set_content(BAKAI_WIDGET_HTML)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(200)

        buy_rate = bakai._extract_rub_buy_rate_from_loaded_page(page)

        context.close()
        browser.close()

    assert buy_rate == 1.145
