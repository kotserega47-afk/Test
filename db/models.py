# db/models.py
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Card(Base):
    __tablename__ = "cards"

    id = Column(Integer, primary_key=True)
    card_number = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    events = relationship("CardEvent", back_populates="card")


class CardEvent(Base):
    __tablename__ = "card_events"

    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, nullable=False)  # success / error
    source_file = Column(String)
    event_time = Column(DateTime, nullable=False)
    loaded_at = Column(DateTime, server_default=func.now())

    card = relationship("Card", back_populates="events")

    __table_args__ = (
        UniqueConstraint("card_id", "status", "event_time", name="uix_card_event"),
    )


class File(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True)
    filename = Column(String, unique=True, nullable=False)
    analyzed_method = Column(String)
    imported_at = Column(DateTime, server_default=func.now())
