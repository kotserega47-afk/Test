# init_db.py
from db.models import Base
from db.database import engine

from db.database import create_tables

if __name__ == "__main__":
    create_tables()
    print("Таблицы созданы!")