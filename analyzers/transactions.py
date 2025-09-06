# analyzers/transactions.py
import pandas as pd


def analyze(file_path: str) -> dict:
    """
    Минимальный анализ CSV/Excel с транзакциями.
    Ожидается, что в файле есть колонка 'Amount'.

    Возвращает словарь с ключевыми параметрами и DataFrame.
    """
    try:
        if file_path.endswith(".xlsx"):
            df = pd.read_excel(file_path)
        else:
            df = pd.read_csv(file_path)

        # Проверяем, что колонка Amount есть
        if "Amount" not in df.columns:
            raise ValueError("В файле нет колонки 'Amount'")

        total_transactions = len(df)
        total_amount = df["Amount"].sum()

        summary = {
            "total_transactions": int(total_transactions),
            "total_amount": float(total_amount),
        }

        return {"summary": summary, "data": df}

    except Exception as e:
        return {"error": str(e)}
