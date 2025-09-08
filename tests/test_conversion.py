import os
import pandas as pd
import pytest
from analyzers.conversion import run

# Тестовые данные (создаются, если файла нет)
TEST_FILE = r"C:\Users\denis\Desktop\conversion.xlsx"
COLUMNS = {
    "card": "Карта",
    "status": "Статус",
    "datetime": "Дата/Время создания",
    "partner": "Партнер"
}

@pytest.fixture(scope="session", autouse=True)

def test_conversion_summary():
    """Проверка, что анализатор возвращает корректный summary"""
    result = run(TEST_FILE, COLUMNS)

    assert "summary" in result
    summary = result["summary"]

    # Проверяем основные ключи
    assert "total_cards" in summary
    assert "total_partners" in summary
    assert "problem_cards" in summary
    assert "total_rows" in summary

    # Должно быть 2 карты и 2 партнёра в тестовом файле
    assert summary["total_cards"] == 2
    assert summary["total_partners"] == 2
    assert summary["total_rows"] == 4

def test_conversion_dataframe():
    """Проверка, что в result['data'] есть ожидаемые колонки"""
    result = run(TEST_FILE, COLUMNS)
    df = result["data"]

    assert isinstance(df, pd.DataFrame)
    for col in ["card", "partner", "max_consecutive_errors"]:
        assert col in df.columns
