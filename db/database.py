# db/database.py
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool  # 🔧 ДЛЯ ОПТИМИЗАЦИИ

# -----------------------------
# Настройка окружения
# -----------------------------
load_dotenv()

def _get_database_url() -> str:
    """Возвращает строку подключения к БД"""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL не задан. Установите переменную окружения DATABASE_URL.")
    return url

def _should_echo_sql() -> bool:
    """Определяет, нужно ли логировать SQL"""
    val = os.getenv("SQL_ECHO", "").strip().lower()
    return val in {"1", "true", "t", "yes", "y"}

# -----------------------------
# 🚨 ОПТИМИЗИРОВАННАЯ Инициализация SQLAlchemy
# -----------------------------
engine = create_engine(
    _get_database_url(),
    echo=_should_echo_sql(),
    future=True,
    # 🔧 КРИТИЧЕСКИЕ ОПТИМИЗАЦИИ ПАМЯТИ:
    pool_size=5,           # Ограничиваем пул соединений
    max_overflow=10,       # Максимальное количество сверх pool_size
    pool_pre_ping=True,    # Проверка соединения перед использованием
    pool_recycle=3600,     # Пересоздавать соединения каждый час
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # 🔧 Важно для производительности
)

Base = declarative_base()

# -----------------------------
# Служебные функции
# -----------------------------
def create_tables() -> None:
    """Создание таблиц, если их ещё нет"""
    import db.models  # noqa: F401
    Base.metadata.create_all(bind=engine)

@contextmanager
def get_session() -> Iterator[Session]:
    """Контекстный менеджер безопасной сессии"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        # 🔧 ОПТИМИЗАЦИЯ: Принудительная сборка мусора после сессии
        import gc
        gc.collect()