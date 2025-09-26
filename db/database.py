# db/database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# читаем URL из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задана. Установите переменную окружения DATABASE_URL.")

# При отладке поставьте echo=True, чтобы видеть SQL в логах
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def create_tables():
    """
    Создать таблицы, описанные в db.models.
    Импорт моделей внутри функции -- чтобы избежать циклических импортов.
    """
    import db.models  # гарантируем регистрацию всех моделей в Base.metadata
    Base.metadata.create_all(bind=engine)
