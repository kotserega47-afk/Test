import sys

import pandas as pd


def test_events_from_excluded_periods_are_skipped(tmp_path, monkeypatch):
    db_path = tmp_path / "test_conversion.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    for module_name in ("db.database", "db.models", "load_data"):
        if module_name in sys.modules:
            del sys.modules[module_name]

    import db.database  # noqa: WPS433  # импорт после смены окружения
    import db.models  # noqa: WPS433
    import load_data  # noqa: WPS433

    from db.database import Base, SessionLocal, engine
    from db.models import CardEvent

    Base.metadata.create_all(bind=engine)

    session = SessionLocal()

    card_df = pd.DataFrame([
        {
            "Карта": "123456",
            "Пул": "Пул А-Мобайл",
            "Направление": "in",
            "Баланс": 0,
            "Метод пополнения": "sbp",
            "Имя": "Иван",
            "Фамилия": "Иванов",
            "Bakai customer_id": "1",
        }
    ])

    conversion_df = pd.DataFrame([
        {
            "Карта": "123456",
            "Статус": "success",
            "ID операции": "operation-blocked",
            "Дата/Время создания": "26.09.2025 18:30:00",
            "Партнер": "HH (Амобайл Тинькофф)",
            "Сумма": 1000,
        },
        {
            "Карта": "123456",
            "Статус": "success",
            "ID операции": "operation-allowed",
            "Дата/Время создания": "26.09.2025 20:00:00",
            "Партнер": "HH (Амобайл Тинькофф)",
            "Сумма": 2000,
        },
    ])

    try:
        load_data.process_conversion(card_df, conversion_df, session)

        events = session.query(CardEvent).all()

        assert len(events) == 1
        assert events[0].operation_id == "operation-allowed"
    finally:
        session.close()