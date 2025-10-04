"""Инфраструктура подключения к базе данных проекта."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

load_dotenv()


def _get_database_url() -> str:
    """Прочитать URL подключения из окружения и привести к корректному виду."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL не задана. Установите переменную окружения DATABASE_URL.")

    # Принудительно добавляем psycopg2-драйвер (иначе Windows может выбросить UnicodeDecodeError)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    return url


def _should_echo_sql() -> bool:
    """Определить, нужно ли логировать SQL по переменной окружения."""
    flag = os.getenv("SQL_ECHO", "").lower()
    return flag in {"1", "true", "t", "yes", "y"}


# Создание движка с правильной кодировкой
engine = create_engine(
    _get_database_url(),
    echo=_should_echo_sql(),
    future=True,
    connect_args={"client_encoding": "utf8"},
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

Base = declarative_base()


def create_tables() -> None:
    """Создать таблицы, описанные в db.models."""
    import db.models  # noqa: F401 — регистрируем модели
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
