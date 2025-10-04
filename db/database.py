# db/database.py
"""Инфраструктура подключения к базе данных проекта."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.orm import Session, declarative_base, sessionmaker


load_dotenv()


def _get_database_url() -> str:
    """Прочитать URL подключения из окружения и валидировать его."""

    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL не задана. Установите переменную окружения DATABASE_URL.")
    return url


# читаем URL из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задана. Установите переменную окружения DATABASE_URL.")
def _should_echo_sql() -> bool:
    """Определить, нужно ли логировать SQL, по переменной окружения."""

    # При отладке поставьте echo=True, чтобы видеть SQL в логах
    engine = create_engine(DATABASE_URL, echo=False, future=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    flag = os.getenv("SQL_ECHO", "").lower()
    return flag in {"1", "true", "t", "yes", "y"}


engine = create_engine(_get_database_url(), echo=_should_echo_sql(), future=True)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

Base = declarative_base()

def create_tables():
    """
    Создать таблицы, описанные в db.models.
    Импорт моделей внутри функции -- чтобы избежать циклических импортов.
    """
    import db.models  # гарантируем регистрацию всех моделей в Base.metadata

def create_tables() -> None:
    """Создать таблицы, описанные в ``db.models``."""

    import db.models  # noqa: F401  # регистрируем все модели в Base.metadata

    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """Предоставить сессию SQLAlchemy с автоматическим управлением транзакцией."""

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
