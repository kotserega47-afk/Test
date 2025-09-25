# init_db.py
from db.models import Base
from db.database import engine

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("✅ Таблицы в PostgreSQL созданы")
