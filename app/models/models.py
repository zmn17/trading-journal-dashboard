"""
journal/models.py - Database models for the trading journal
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, DateTime,
    Text, ForeignKey, Index, create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(BigInteger, unique=True, nullable=False, index=True)
    deal_id_open = Column(BigInteger)
    deal_id_close = Column(BigInteger)

    symbol_id = Column(BigInteger, nullable=False)
    symbol = Column(String(30), nullable=False)

    side = Column(String(4), nullable=False)
    volume = Column(Float, nullable=False)
    lots = Column(Float)

    entry_price = Column(Float)
    entry_time = Column(DateTime(timezone=True))

    exit_price = Column(Float)
    exit_time = Column(DateTime(timezone=True))

    stop_loss = Column(Float)
    take_profit = Column(Float)

    gross_profit = Column(Float)
    commission = Column(Float)
    swap = Column(Float)
    net_profit = Column(Float)

    session = Column(String(20))
    status = Column(String(10), default="open")

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    notes = relationship("TradeNote", back_populates="trade", cascade="all, delete-orphan")
    screenshots = relationship("Screenshot", back_populates="trade", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_trades_symbol_entry", "symbol", "entry_time"),
        Index("ix_trades_session", "session"),
        Index("ix_trades_status", "status"),
    )

    def __repr__(self):
        return f"<Trade {self.symbol} {self.side} {self.lots}L [{self.status}]>"


class TradeNote(Base):
    __tablename__ = "trade_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    note_type = Column(String(20), default="general")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    trade = relationship("Trade", back_populates="notes")


class Screenshot(Base):
    __tablename__ = "screenshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False)
    file_path = Column(String(500), nullable=False)
    caption = Column(String(200))
    chart_timeframe = Column(String(10))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    trade = relationship("Trade", back_populates="screenshots")


def get_engine(database_url: str):
    return create_engine(database_url, echo=False, pool_pre_ping=True)


def get_session_factory(engine):
    return sessionmaker(bind=engine)


def create_tables(engine):
    Base.metadata.create_all(engine)
