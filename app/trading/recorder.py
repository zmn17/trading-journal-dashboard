"""
journal/recorder.py - Trade recorder

Handles inserting and updating trades in PostgreSQL.
Called by the daemon on execution events.
"""

from datetime import datetime, timezone
from sqlalchemy.orm import Session as DBSession

from app.models.models import Trade
from app.trading.sessions import detect_session


def ts_to_datetime(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def record_position_open(
    db: DBSession,
    position_id: int,
    deal_id: int,
    symbol_id: int,
    symbol: str,
    side: str,
    volume: float,
    entry_price: float,
    entry_time_ms: int,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    swap: float = 0,
) -> Trade:
    """Record a new position. Skips if already exists."""
    existing = db.query(Trade).filter_by(position_id=position_id).first()
    if existing:
        return existing

    entry_dt = ts_to_datetime(entry_time_ms)

    trade = Trade(
        position_id=position_id,
        deal_id_open=deal_id,
        symbol_id=symbol_id,
        symbol=symbol,
        side=side,
        volume=volume,
        lots=volume / 100000,
        entry_price=entry_price,
        entry_time=entry_dt,
        stop_loss=stop_loss,
        take_profit=take_profit,
        swap=swap,
        session=detect_session(entry_dt),
        status="open",
    )

    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


def record_position_close(
    db: DBSession,
    position_id: int,
    deal_id: int,
    exit_price: float,
    exit_time_ms: int,
    gross_profit: float,
    commission: float,
    swap: float,
) -> Trade | None:
    """Update trade with exit details. Returns None if not tracked."""
    trade = db.query(Trade).filter_by(position_id=position_id).first()
    if not trade:
        return None

    trade.deal_id_close = deal_id
    trade.exit_price = exit_price
    trade.exit_time = ts_to_datetime(exit_time_ms)
    trade.gross_profit = gross_profit
    trade.commission = commission
    trade.swap = swap
    trade.net_profit = gross_profit - abs(commission) - abs(swap)
    trade.status = "closed"

    db.commit()
    db.refresh(trade)
    return trade


def record_sl_tp_update(
    db: DBSession,
    position_id: int,
    stop_loss: float | None,
    take_profit: float | None,
) -> Trade | None:
    trade = db.query(Trade).filter_by(position_id=position_id, status="open").first()
    if not trade:
        return None

    trade.stop_loss = stop_loss
    trade.take_profit = take_profit
    db.commit()
    return trade


def backfill_from_deals(
    db: DBSession,
    deals: list,
    symbol_cache: dict,
) -> tuple[int, int]:
    """
    Backfill trades from historical deal list.
    Groups deals by position_id to reconstruct full trade lifecycle.
    Returns (new_count, skipped_count).
    """
    positions: dict[int, list] = {}
    for deal in deals:
        pid = deal.positionId
        if pid not in positions:
            positions[pid] = []
        positions[pid].append(deal)

    new_count = 0
    skipped = 0

    for position_id, pos_deals in positions.items():
        if db.query(Trade).filter_by(position_id=position_id).first():
            skipped += 1
            continue

        pos_deals.sort(key=lambda d: d.executionTimestamp)

        open_deal = None
        close_deal = None

        for d in pos_deals:
            if d.HasField("closePositionDetail"):
                close_deal = d
            elif open_deal is None:
                open_deal = d

        if not open_deal:
            continue

        symbol = symbol_cache.get(open_deal.symbolId, str(open_deal.symbolId))
        side = "BUY" if open_deal.tradeSide == 1 else "SELL"
        entry_dt = ts_to_datetime(open_deal.executionTimestamp)

        trade = Trade(
            position_id=position_id,
            deal_id_open=open_deal.dealId,
            symbol_id=open_deal.symbolId,
            symbol=symbol,
            side=side,
            volume=open_deal.volume / 100,
            lots=(open_deal.volume / 100) / 100000,
            entry_price=open_deal.executionPrice,
            entry_time=entry_dt,
            session=detect_session(entry_dt),
            status="open",
        )

        if close_deal:
            trade.deal_id_close = close_deal.dealId
            trade.exit_price = close_deal.executionPrice
            trade.exit_time = ts_to_datetime(close_deal.executionTimestamp)
            trade.gross_profit = close_deal.closePositionDetail.grossProfit / 100
            trade.commission = (
                (close_deal.commission / 100) if close_deal.commission else 0
            )
            swap_val = (
                close_deal.closePositionDetail.swap / 100
                if close_deal.closePositionDetail.swap
                else 0
            )
            trade.swap = swap_val
            trade.net_profit = (
                trade.gross_profit - abs(trade.commission) - abs(swap_val)
            )
            trade.status = "closed"

        db.add(trade)
        new_count += 1

    db.commit()
    return new_count, skipped
