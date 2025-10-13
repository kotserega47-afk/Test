# db/models.py
from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, ForeignKey,
    JSON, UniqueConstraint, func
)
from sqlalchemy.orm import relationship
from db.database import Base

class Card(Base):
    __tablename__ = "cards"

    id = Column(Integer, primary_key=True)
    card_number = Column(String, unique=True, nullable=False)
    partner = Column(String, nullable=True)
    kyc_status = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    events = relationship("CardEvent", back_populates="card")

class ErrorType(Base):
    __tablename__ = "error_types"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=True)

class CardEvent(Base):
    __tablename__ = "card_events"
    __table_args__ = (UniqueConstraint("card_id", "operation_id", name="uq_card_operation"),)

    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey("cards.id"), nullable=False)
    status = Column(String, nullable=False)
    amount = Column(Numeric, nullable=True)
    operation_id = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    error_id = Column(Integer, ForeignKey("error_types.id"), nullable=True)
    source_file = Column(String, nullable=True)
    snapshot_data = Column(JSON, nullable=True)
    imported_at = Column(DateTime, server_default=func.now())

    card = relationship("Card", back_populates="events")

class CardDisableHistory(Base):
    __tablename__ = "card_disable_history"

    id = Column(Integer, primary_key=True)
    card_number = Column(String, nullable=False)
    disabled_at = Column(DateTime, server_default=func.now())
