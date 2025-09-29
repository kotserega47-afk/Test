import os
import tempfile
import sys
from pathlib import Path

import pandas as pd
import pytest

# Ensure DATABASE_URL is set before importing database-related modules
_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from db.database import Base, SessionLocal, engine, create_tables  # noqa: E402
from db.models import CardEvent  # noqa: E402
from load_data import process_conversion  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def cleanup_db_file():
    try:
        yield
    finally:
        if os.path.exists(_db_path):
            os.remove(_db_path)


@pytest.fixture
def session():
    create_tables()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_process_conversion_skips_excluded_intervals(session):
    card_df = pd.DataFrame(columns=["Карта"])

    conversion_df = pd.DataFrame(
        [
            {
                "Карта": "1111",
                "ID операции": "op-before",
                "Статус": "SUCCESS",
                "Инфо": None,
                "Сумма": 100,
                "Дата/Время создания": "26.09.2025 17:59:59",
                "Партнер": "HH (Аврора Сбер)",
            },
            {
                "Карта": "2222",
                "ID операции": "op-excluded",
                "Статус": "SUCCESS",
                "Инфо": None,
                "Сумма": 200,
                "Дата/Время создания": "26.09.2025 18:30:00",
                "Партнер": "HH (Аврора Сбер)",
            },
            {
                "Карта": "3333",
                "ID операции": "op-after",
                "Статус": "ERROR",
                "Инфо": "E001",
                "Сумма": None,
                "Дата/Время создания": "26.09.2025 19:00:01",
                "Партнер": "HH (Аврора Сбер)",
            },
        ]
    )

    process_conversion(card_df, conversion_df, session)

    events = session.query(CardEvent).order_by(CardEvent.operation_id).all()

    assert [event.operation_id for event in events] == ["op-after", "op-before"]
    assert all(event.operation_id != "op-excluded" for event in events)
    assert len(events) == 2
