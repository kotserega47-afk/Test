from dotenv import load_dotenv
import os
import psycopg2

# Загружаем переменные окружения
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
print("URL:", DATABASE_URL)

try:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT current_database(), version();")
    print("Connected:", cur.fetchone())
    cur.close()
    conn.close()
except Exception as e:
    print("Ошибка подключения:", e)
