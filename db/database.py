# db/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Настройки подключения к PostgreSQL
DATABASE_URL = "postgresql+psycopg2://user:password@localhost:5432/mydatabase"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def create_tables():
    """Создать все таблицы, определённые в models.py"""
    from db.models import Card, CardEvent, ErrorType
    Base.metadata.create_all(bind=engine)
