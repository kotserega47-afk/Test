# scripts/export_card_events.py
import os
import psycopg2
import pandas as pd

# 🔑 параметры подключения (можно заменить на свои или подтянуть из .env)
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "your_db")
DB_USER = os.getenv("DB_USER", "your_user")
DB_PASS = os.getenv("DB_PASS", "your_password")

OUTPUT_FILE = "card_events_export.xlsx"


def export_card_events():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
    )

    query = """
        SELECT 
            c.card_number,
            e.status,
            e.amount,
            e.created_at,
            et.code AS error_code,
            et.description AS error_description
        FROM card_events e
        JOIN cards c ON e.card_id = c.id
        LEFT JOIN error_types et ON e.error_id = et.id
        ORDER BY e.created_at DESC
    """

    df = pd.read_sql(query, conn)

    conn.close()

    # Экспортируем в Excel
    df.to_excel(OUTPUT_FILE, index=False, engine="openpyxl")

    print(f"✅ Данные экспортированы в {OUTPUT_FILE}")


if __name__ == "__main__":
    export_card_events()
