"""
api/main.py - FastAPI backend for the Trading Journal Dashboard

Endpoints:
  GET  /api/trades          — Trade log with filters
  GET  /api/trades/{id}     — Single trade with notes + screenshots
  GET  /api/stats           — Performance stats (win rate, PF, etc.)
  GET  /api/stats/sessions  — P&L breakdown by session
  GET  /api/stats/symbols   — P&L breakdown by symbol
  GET  /api/stats/equity    — Cumulative equity curve data
  GET  /api/stats/calendar  — Calendar heatmap data
  POST /api/trades/{id}/notes       — Add a note
  DELETE /api/trades/{id}/notes/{nid} — Delete a note
  POST /api/trades/{id}/screenshots — Upload screenshot
  DELETE /api/trades/{id}/screenshots/{sid} — Delete screenshot

Run:
  uvicorn api.main:app --reload --port 8000
"""

import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc

from journal.models import get_engine, get_session_factory, Trade, TradeNote, Screenshot

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = get_engine(DATABASE_URL)
SessionFactory = get_session_factory(engine)

UPLOAD_DIR = Path("uploads/screenshots")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Trading Journal API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


# ─── Helpers ──────────────────────────────────────────────────


def trade_to_dict(t: Trade) -> dict:
    return {
        "id": t.id,
        "position_id": t.position_id,
        "symbol": t.symbol,
        "side": t.side,
        "lots": t.lots,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "entry_time": t.entry_time.isoformat() if t.entry_time else None,
        "exit_time": t.exit_time.isoformat() if t.exit_time else None,
        "stop_loss": t.stop_loss,
        "take_profit": t.take_profit,
        "gross_profit": t.gross_profit,
        "commission": t.commission,
        "swap": t.swap,
        "net_profit": t.net_profit,
        "session": t.session,
        "status": t.status,
        "notes": [
            {
                "id": n.id,
                "content": n.content,
                "note_type": n.note_type,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in t.notes
        ],
        "screenshots": [
            {
                "id": s.id,
                "file_path": f"/uploads/screenshots/{Path(s.file_path).name}",
                "caption": s.caption,
                "chart_timeframe": s.chart_timeframe,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in t.screenshots
        ],
    }


def parse_period(period: str) -> datetime:
    now = datetime.now(timezone.utc)
    periods = {
        "today": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "week": now - timedelta(days=7),
        "month": now - timedelta(days=30),
        "3months": now - timedelta(days=90),
        "year": now - timedelta(days=365),
        "all": datetime(2000, 1, 1, tzinfo=timezone.utc),
    }
    return periods.get(period, periods["all"])


# ─── Trade Log ────────────────────────────────────────────────


@app.get("/api/trades")
def get_trades(
    period: str = Query("all"),
    symbol: str | None = Query(None),
    side: str | None = Query(None),
    session: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(200),
    offset: int = Query(0),
):
    db = SessionFactory()
    try:
        q = db.query(Trade)

        since = parse_period(period)
        q = q.filter(Trade.entry_time >= since)

        if symbol:
            q = q.filter(Trade.symbol == symbol.upper())
        if side:
            q = q.filter(Trade.side == side.upper())
        if session:
            q = q.filter(Trade.session == session.lower())
        if status:
            q = q.filter(Trade.status == status.lower())

        total = q.count()
        trades = q.order_by(desc(Trade.entry_time)).offset(offset).limit(limit).all()

        return {
            "trades": [trade_to_dict(t) for t in trades],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


@app.get("/api/trades/{trade_id}")
def get_trade(trade_id: int):
    db = SessionFactory()
    try:
        trade = db.query(Trade).filter_by(id=trade_id).first()
        if not trade:
            raise HTTPException(404, "Trade not found")
        return trade_to_dict(trade)
    finally:
        db.close()


# ─── Stats ────────────────────────────────────────────────────


@app.get("/api/stats")
def get_stats(period: str = Query("all")):
    db = SessionFactory()
    try:
        since = parse_period(period)
        all_trades = db.query(Trade).filter(Trade.entry_time >= since).all()
        closed = [
            t for t in all_trades if t.status == "closed" and t.net_profit is not None
        ]

        if not closed:
            return {
                "total_trades": 0,
                "open_trades": len([t for t in all_trades if t.status == "open"]),
                "winners": 0,
                "losers": 0,
                "breakeven": 0,
                "win_rate": 0,
                "net_pnl": 0,
                "gross_pnl": 0,
                "avg_winner": 0,
                "avg_loser": 0,
                "best_trade": None,
                "worst_trade": None,
                "profit_factor": 0,
                "avg_rr": 0,
            }

        winners = [t for t in closed if t.net_profit > 0]
        losers = [t for t in closed if t.net_profit < 0]
        breakeven = [t for t in closed if t.net_profit == 0]

        gross_wins = sum(t.net_profit for t in winners)
        gross_losses = abs(sum(t.net_profit for t in losers))
        avg_win = gross_wins / len(winners) if winners else 0
        avg_loss = gross_losses / len(losers) if losers else 0

        best = max(closed, key=lambda t: t.net_profit)
        worst = min(closed, key=lambda t: t.net_profit)

        return {
            "total_trades": len(closed),
            "open_trades": len([t for t in all_trades if t.status == "open"]),
            "winners": len(winners),
            "losers": len(losers),
            "breakeven": len(breakeven),
            "win_rate": round(len(winners) / len(closed) * 100, 1),
            "net_pnl": round(sum(t.net_profit for t in closed), 2),
            "gross_pnl": round(
                sum(t.gross_profit for t in closed if t.gross_profit), 2
            ),
            "avg_winner": round(avg_win, 2),
            "avg_loser": round(-avg_loss, 2),
            "best_trade": {
                "id": best.id,
                "symbol": best.symbol,
                "pnl": round(best.net_profit, 2),
            },
            "worst_trade": {
                "id": worst.id,
                "symbol": worst.symbol,
                "pnl": round(worst.net_profit, 2),
            },
            "profit_factor": round(gross_wins / gross_losses, 2)
            if gross_losses > 0
            else None,
            "avg_rr": round(avg_win / avg_loss, 2) if avg_loss > 0 else None,
        }
    finally:
        db.close()


@app.get("/api/stats/sessions")
def get_session_stats(period: str = Query("all")):
    db = SessionFactory()
    try:
        since = parse_period(period)
        closed = (
            db.query(Trade)
            .filter(
                Trade.entry_time >= since,
                Trade.status == "closed",
                Trade.net_profit.isnot(None),
            )
            .all()
        )

        sessions = {}
        for t in closed:
            s = t.session or "unknown"
            if s not in sessions:
                sessions[s] = {
                    "session": s,
                    "count": 0,
                    "pnl": 0,
                    "winners": 0,
                    "losers": 0,
                }
            sessions[s]["count"] += 1
            sessions[s]["pnl"] = round(sessions[s]["pnl"] + t.net_profit, 2)
            if t.net_profit > 0:
                sessions[s]["winners"] += 1
            elif t.net_profit < 0:
                sessions[s]["losers"] += 1

        for s in sessions.values():
            s["win_rate"] = (
                round(s["winners"] / s["count"] * 100, 1) if s["count"] > 0 else 0
            )

        return list(sessions.values())
    finally:
        db.close()


@app.get("/api/stats/symbols")
def get_symbol_stats(period: str = Query("all")):
    db = SessionFactory()
    try:
        since = parse_period(period)
        closed = (
            db.query(Trade)
            .filter(
                Trade.entry_time >= since,
                Trade.status == "closed",
                Trade.net_profit.isnot(None),
            )
            .all()
        )

        symbols = {}
        for t in closed:
            if t.symbol not in symbols:
                symbols[t.symbol] = {
                    "symbol": t.symbol,
                    "count": 0,
                    "pnl": 0,
                    "winners": 0,
                    "losers": 0,
                }
            symbols[t.symbol]["count"] += 1
            symbols[t.symbol]["pnl"] = round(symbols[t.symbol]["pnl"] + t.net_profit, 2)
            if t.net_profit > 0:
                symbols[t.symbol]["winners"] += 1
            elif t.net_profit < 0:
                symbols[t.symbol]["losers"] += 1

        for s in symbols.values():
            s["win_rate"] = (
                round(s["winners"] / s["count"] * 100, 1) if s["count"] > 0 else 0
            )

        return sorted(symbols.values(), key=lambda x: x["pnl"], reverse=True)
    finally:
        db.close()


@app.get("/api/stats/equity")
def get_equity_curve(period: str = Query("all")):
    db = SessionFactory()
    try:
        since = parse_period(period)
        closed = (
            db.query(Trade)
            .filter(
                Trade.entry_time >= since,
                Trade.status == "closed",
                Trade.net_profit.isnot(None),
            )
            .order_by(Trade.exit_time)
            .all()
        )

        cumulative = 0
        points = []
        for t in closed:
            cumulative = round(cumulative + t.net_profit, 2)
            points.append(
                {
                    "date": t.exit_time.isoformat() if t.exit_time else None,
                    "pnl": t.net_profit,
                    "cumulative": cumulative,
                    "symbol": t.symbol,
                    "trade_id": t.id,
                }
            )

        return points
    finally:
        db.close()


@app.get("/api/stats/calendar")
def get_calendar_data(period: str = Query("all")):
    db = SessionFactory()
    try:
        since = parse_period(period)
        closed = (
            db.query(Trade)
            .filter(
                Trade.entry_time >= since,
                Trade.status == "closed",
                Trade.net_profit.isnot(None),
            )
            .all()
        )

        days = {}
        for t in closed:
            if not t.exit_time:
                continue
            day = t.exit_time.strftime("%Y-%m-%d")
            if day not in days:
                days[day] = {
                    "date": day,
                    "pnl": 0,
                    "count": 0,
                    "winners": 0,
                    "losers": 0,
                }
            days[day]["pnl"] = round(days[day]["pnl"] + t.net_profit, 2)
            days[day]["count"] += 1
            if t.net_profit > 0:
                days[day]["winners"] += 1
            elif t.net_profit < 0:
                days[day]["losers"] += 1

        return sorted(days.values(), key=lambda x: x["date"])
    finally:
        db.close()


# ─── Unique filter values ────────────────────────────────────


@app.get("/api/filters")
def get_filter_options():
    db = SessionFactory()
    try:
        symbols = [
            r[0] for r in db.query(Trade.symbol).distinct().order_by(Trade.symbol).all()
        ]
        sessions = [
            r[0]
            for r in db.query(Trade.session).distinct().order_by(Trade.session).all()
            if r[0]
        ]
        return {"symbols": symbols, "sessions": sessions}
    finally:
        db.close()


# ─── Notes ────────────────────────────────────────────────────


@app.post("/api/trades/{trade_id}/notes")
def add_note(trade_id: int, content: str = Form(...), note_type: str = Form("general")):
    db = SessionFactory()
    try:
        trade = db.query(Trade).filter_by(id=trade_id).first()
        if not trade:
            raise HTTPException(404, "Trade not found")

        note = TradeNote(trade_id=trade_id, content=content, note_type=note_type)
        db.add(note)
        db.commit()
        db.refresh(note)

        return {
            "id": note.id,
            "content": note.content,
            "note_type": note.note_type,
            "created_at": note.created_at.isoformat() if note.created_at else None,
        }
    finally:
        db.close()


@app.delete("/api/trades/{trade_id}/notes/{note_id}")
def delete_note(trade_id: int, note_id: int):
    db = SessionFactory()
    try:
        note = db.query(TradeNote).filter_by(id=note_id, trade_id=trade_id).first()
        if not note:
            raise HTTPException(404, "Note not found")
        db.delete(note)
        db.commit()
        return {"deleted": True}
    finally:
        db.close()


# ─── Screenshots ─────────────────────────────────────────────


@app.post("/api/trades/{trade_id}/screenshots")
async def upload_screenshot(
    trade_id: int,
    file: UploadFile = File(...),
    caption: str = Form(""),
    chart_timeframe: str = Form(""),
):
    db = SessionFactory()
    try:
        trade = db.query(Trade).filter_by(id=trade_id).first()
        if not trade:
            raise HTTPException(404, "Trade not found")

        ext = Path(file.filename).suffix if file.filename else ".png"
        filename = f"trade_{trade_id}_{int(datetime.now().timestamp())}{ext}"
        filepath = UPLOAD_DIR / filename

        with open(filepath, "wb") as f:
            shutil.copyfileobj(file.file, f)

        screenshot = Screenshot(
            trade_id=trade_id,
            file_path=str(filepath),
            caption=caption or None,
            chart_timeframe=chart_timeframe or None,
        )
        db.add(screenshot)
        db.commit()
        db.refresh(screenshot)

        return {
            "id": screenshot.id,
            "file_path": f"/uploads/screenshots/{filename}",
            "caption": screenshot.caption,
            "chart_timeframe": screenshot.chart_timeframe,
        }
    finally:
        db.close()


@app.delete("/api/trades/{trade_id}/screenshots/{screenshot_id}")
def delete_screenshot(trade_id: int, screenshot_id: int):
    db = SessionFactory()
    try:
        screenshot = (
            db.query(Screenshot).filter_by(id=screenshot_id, trade_id=trade_id).first()
        )
        if not screenshot:
            raise HTTPException(404, "Screenshot not found")

        try:
            os.remove(screenshot.file_path)
        except FileNotFoundError:
            pass

        db.delete(screenshot)
        db.commit()
        return {"deleted": True}
    finally:
        db.close()
