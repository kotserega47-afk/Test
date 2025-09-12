# analyzers/transactions.py
import pandas as pd
import yaml

# Загружаем конфигурацию
with open("config/analysis_map.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

# Получаем колонку для анализа
ANALYSIS_COLUMN = config.get("transactions", {}).get("column", "Amount")

def analyze(file_path: str) -> dict:
    """
    Универсальный анализ CSV/Excel с транзакциями.
    Колонка для анализа берётся из YAML-конфига.
    Возвращает словарь с ключевыми параметрами и DataFrame.
    """
    try:
        # Читаем файл
        if file_path.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file_path)
        else:
            df = pd.read_csv(file_path)

        # Стандартизируем имена колонок
        df.columns = df.columns.str.strip().str.lower()
        col = ANALYSIS_COLUMN.strip().lower()

        # Проверка наличия колонки
        if col not in df.columns:
            raise ValueError(f"В файле нет колонки '{ANALYSIS_COLUMN}'")

        total_transactions = len(df)
        total_amount = df[col].sum()

        summary = {
            "total_transactions": int(total_transactions),
            "total_amount": float(total_amount),
        }

        return {"summary": summary, "data": df}

    except Exception as e:
        return {"error": str(e)}