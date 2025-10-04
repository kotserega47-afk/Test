# db/models.py

from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey
from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from db.database import Base


class Card(Base):
    __tablename__ = "cards"

    id = Column(Integer, primary_key=True)
    card_number = Column(String, unique=True, nullable=False)
    pool_id = Column(String, nullable=True)
    direction = Column(String, nullable=True)
    balance = Column(Numeric, nullable=True)
    replenishment_method = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    bakai_customer_id = Column(String, nullable=True)

    first_success_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    first_error_at = Column(DateTime, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    total_success_amount = Column(Numeric, default=0)

    events = relationship("CardEvent", back_populates="card")


class ErrorType(Base):
    __tablename__ = "error_types"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)
    description = Column(String)
    created_at = Column(DateTime, server_default=func.now())

    events = relationship("CardEvent", back_populates="error")


class CardEvent(Base):
    __tablename__ = "card_events"

    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey("cards.id"), nullable=False)
    status = Column(String, nullable=False)  # success / error
    amount = Column(Numeric, nullable=True)
    operation_id = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, nullable=False)
    error_id = Column(Integer, ForeignKey("error_types.id"), nullable=True)
    source_file = Column(String, nullable=True)
    snapshot_data = Column(JSON, nullable=True)
    imported_at = Column(DateTime, server_default=func.now())

    card = relationship("Card", back_populates="events")
    error = relationship("ErrorType", back_populates="events")


class CardDisableHistory(Base):
    __tablename__ = 'card_disable_history'

    id = Column(Integer, primary_key=True)
    card_number = Column(String, nullable=False)
    disabled_at = Column(DateTime, nullable=False)