# db/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# SQLite для разработки
DATABASE_URL = "sqlite:///./app.db"

engine = create_engine(DATABASE_URL, echo=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def init_db(Base):
    Base.metadata.create_all(bind=engine)
