from db.database import get_session
from sqlalchemy import text

with get_session() as session:
    session.execute(text("TRUNCATE TABLE card_events RESTART IDENTITY CASCADE;"))
    session.execute(text("TRUNCATE TABLE cards RESTART IDENTITY CASCADE;"))
    session.execute(text("TRUNCATE TABLE error_types RESTART IDENTITY CASCADE;"))
    session.commit()

print("✅ Таблицы очищены")
