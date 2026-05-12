"""
journal_daemon.py - cTrader Trade Journal Daemon

Runs continuously in the background:
  1. Connects to cTrader via TCP/Protobuf
  2. Authenticates app + account
  3. Backfills recent deal history into PostgreSQL
  4. Listens for live execution events and records them

Usage:
    python journal_daemon.py
"""

import os
import sys
import json
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from twisted.internet import reactor

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import *
from ctrader_open_api.messages.OpenApiMessages_pb2 import *
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import *

from journal.models import get_engine, get_session_factory, create_tables
from journal.recorder import (
    record_position_open,
    record_position_close,
    record_sl_tp_update,
    backfill_from_deals,
)

load_dotenv()

# ─── Config ──────────────────────────────────────────────────
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "live").lower()
DATABASE_URL = os.getenv("DATABASE_URL")
BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS", "30"))

TOKEN_FILE = "tokens.json"
if not os.path.exists(TOKEN_FILE):
    print("[ERROR] No tokens.json found. Run get_token.py first.")
    sys.exit(1)

with open(TOKEN_FILE) as f:
    tokens = json.load(f)

ACCESS_TOKEN = tokens["accessToken"]

HOST = (
    EndPoints.PROTOBUF_LIVE_HOST
    if ACCOUNT_TYPE == "live"
    else EndPoints.PROTOBUF_DEMO_HOST
)
PORT = EndPoints.PROTOBUF_PORT

# ─── Database ────────────────────────────────────────────────
engine = get_engine(DATABASE_URL)
create_tables(engine)
SessionFactory = get_session_factory(engine)

# ─── State ───────────────────────────────────────────────────
account_id = None
symbol_cache = {}
_client = None


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def extract_or_error(result):
    msg = Protobuf.extract(result)
    if msg.__class__.__name__ == "ProtoOAErrorRes":
        code = msg.errorCode if msg.HasField("errorCode") else "?"
        desc = msg.description if msg.HasField("description") else ""
        log(f"[API ERROR] {code} — {desc}")
        return None
    return msg


def on_error(failure):
    log(f"[ERROR] {failure}")


def format_side(trade_side):
    return "BUY" if trade_side == 1 else "SELL"


# ─── Auth Chain ──────────────────────────────────────────────


def connected(client):
    global _client
    _client = client
    log(f"Connected to {ACCOUNT_TYPE.upper()} ({HOST}:{PORT})")

    request = ProtoOAApplicationAuthReq()
    request.clientId = CLIENT_ID
    request.clientSecret = CLIENT_SECRET
    deferred = _client.send(request)
    deferred.addCallbacks(on_app_auth, on_error)


def on_app_auth(result):
    msg = extract_or_error(result)
    if msg is None:
        return
    log("App authenticated")

    request = ProtoOAGetAccountListByAccessTokenReq()
    request.accessToken = ACCESS_TOKEN
    deferred = _client.send(request)
    deferred.addCallbacks(on_account_list, on_error)


def on_account_list(result):
    msg = extract_or_error(result)
    if msg is None:
        return

    accounts = msg.ctidTraderAccount
    if not accounts:
        log("No accounts found!")
        reactor.stop()
        return

    global account_id
    env_account = os.getenv("ACCOUNT_ID")

    if env_account:
        account_id = int(env_account)
    else:
        is_live = ACCOUNT_TYPE == "live"
        matching = [a for a in accounts if a.isLive == is_live]
        account_id = (
            matching[0].ctidTraderAccountId
            if matching
            else accounts[0].ctidTraderAccountId
        )

    log(f"Using account: {account_id}")

    request = ProtoOAAccountAuthReq()
    request.ctidTraderAccountId = account_id
    request.accessToken = ACCESS_TOKEN
    deferred = _client.send(request)
    deferred.addCallbacks(on_account_auth, on_error)


def on_account_auth(result):
    msg = extract_or_error(result)
    if msg is None:
        return
    log(f"Account {account_id} authenticated")

    request = ProtoOASymbolsListReq()
    request.ctidTraderAccountId = account_id
    deferred = _client.send(request)
    deferred.addCallbacks(on_symbols_list, on_error)


def on_symbols_list(result):
    msg = extract_or_error(result)
    if msg is None:
        return

    for sym in msg.symbol:
        symbol_cache[sym.symbolId] = sym.symbolName
    log(f"Loaded {len(symbol_cache)} symbols")

    request = ProtoOAReconcileReq()
    request.ctidTraderAccountId = account_id
    deferred = _client.send(request)
    deferred.addCallbacks(on_reconcile, on_error)


def on_reconcile(result):
    msg = extract_or_error(result)
    if msg is None:
        return

    db = SessionFactory()
    try:
        count = 0
        for pos in msg.position:
            symbol = symbol_cache.get(
                pos.tradeData.symbolId, str(pos.tradeData.symbolId)
            )
            side = format_side(pos.tradeData.tradeSide)
            sl = pos.stopLoss if pos.HasField("stopLoss") else None
            tp = pos.takeProfit if pos.HasField("takeProfit") else None
            swap = pos.swap / 100 if pos.swap else 0

            record_position_open(
                db=db,
                position_id=pos.positionId,
                deal_id=0,
                symbol_id=pos.tradeData.symbolId,
                symbol=symbol,
                side=side,
                volume=pos.tradeData.volume / 100,
                entry_price=pos.price,
                entry_time_ms=pos.tradeData.openTimestamp,
                stop_loss=sl,
                take_profit=tp,
                swap=swap,
            )
            count += 1
        log(f"Reconciled {count} open position(s)")
    finally:
        db.close()

    backfill_deals()


def backfill_deals():
    log(f"Backfilling last {BACKFILL_DAYS} days...")

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (BACKFILL_DAYS * 24 * 60 * 60 * 1000)

    request = ProtoOADealListReq()
    request.ctidTraderAccountId = account_id
    request.fromTimestamp = start_ms
    request.toTimestamp = now_ms
    request.maxRows = 1000
    deferred = _client.send(request)
    deferred.addCallbacks(on_backfill_deals, on_error)


def on_backfill_deals(result):
    msg = extract_or_error(result)
    if msg is None:
        return

    db = SessionFactory()
    try:
        new_count, skipped = backfill_from_deals(db, msg.deal, symbol_cache)
        log(f"Backfill: {new_count} new, {skipped} skipped")
    finally:
        db.close()

    log("Daemon ready — listening for live trades...")


# ─── Live Event Handler ─────────────────────────────────────


def on_message(client, message):
    msg = Protobuf.extract(message)
    msg_type = msg.__class__.__name__

    if msg_type == "ProtoOAExecutionEvent":
        handle_execution_event(msg)
    elif msg_type == "ProtoHeartbeatEvent":
        pass
    elif msg_type == "ProtoOAErrorRes":
        code = msg.errorCode if msg.HasField("errorCode") else "?"
        desc = msg.description if msg.HasField("description") else ""
        log(f"[LIVE ERROR] {code}: {desc}")


def handle_execution_event(msg):
    exec_type = msg.executionType
    db = SessionFactory()

    try:
        if exec_type == 2 and msg.HasField("position"):
            pos = msg.position
            symbol = symbol_cache.get(
                pos.tradeData.symbolId, str(pos.tradeData.symbolId)
            )
            side = format_side(pos.tradeData.tradeSide)
            sl = pos.stopLoss if pos.HasField("stopLoss") else None
            tp = pos.takeProfit if pos.HasField("takeProfit") else None
            deal_id = msg.deal.dealId if msg.HasField("deal") else 0

            pos_status = pos.positionStatus if pos.HasField("positionStatus") else None

            if pos_status == 2:  # CLOSED
                if msg.HasField("deal") and msg.deal.HasField("closePositionDetail"):
                    cpd = msg.deal.closePositionDetail
                    trade = record_position_close(
                        db=db,
                        position_id=pos.positionId,
                        deal_id=deal_id,
                        exit_price=msg.deal.executionPrice,
                        exit_time_ms=msg.deal.executionTimestamp,
                        gross_profit=cpd.grossProfit / 100,
                        commission=(msg.deal.commission / 100)
                        if msg.deal.commission
                        else 0,
                        swap=(cpd.swap / 100) if cpd.swap else 0,
                    )
                    if trade:
                        pnl = f"{trade.net_profit:+.2f}" if trade.net_profit else "?"
                        log(
                            f"CLOSED: {symbol} {side} {trade.lots:.2f}L @ {trade.exit_price:.5f} | P&L: {pnl}"
                        )
            else:
                trade = record_position_open(
                    db=db,
                    position_id=pos.positionId,
                    deal_id=deal_id,
                    symbol_id=pos.tradeData.symbolId,
                    symbol=symbol,
                    side=side,
                    volume=pos.tradeData.volume / 100,
                    entry_price=pos.price,
                    entry_time_ms=pos.tradeData.openTimestamp,
                    stop_loss=sl,
                    take_profit=tp,
                )
                log(
                    f"OPENED: {symbol} {side} {trade.lots:.2f}L @ {trade.entry_price:.5f} [{trade.session}]"
                )

        elif exec_type == 3 and msg.HasField("position"):
            pos = msg.position
            sl = pos.stopLoss if pos.HasField("stopLoss") else None
            tp = pos.takeProfit if pos.HasField("takeProfit") else None
            trade = record_sl_tp_update(db, pos.positionId, sl, tp)
            if trade:
                symbol = symbol_cache.get(
                    pos.tradeData.symbolId, str(pos.tradeData.symbolId)
                )
                log(f"MODIFIED: {symbol} SL={sl} TP={tp}")

        elif exec_type in (4, 5, 6) and msg.HasField("order"):
            order = msg.order
            symbol = symbol_cache.get(
                order.tradeData.symbolId, str(order.tradeData.symbolId)
            )
            names = {4: "CANCELLED", 5: "EXPIRED", 6: "REJECTED"}
            log(f"{names.get(exec_type, 'EVENT')}: {symbol} order")

    finally:
        db.close()


def disconnected(client, reason):
    log(f"Disconnected: {reason}")
    log("Reconnecting in 5s...")
    reactor.callLater(5, start_client)


def start_client():
    client = Client(HOST, PORT, TcpProtocol)
    client.setConnectedCallback(connected)
    client.setDisconnectedCallback(disconnected)
    client.setMessageReceivedCallback(on_message)
    client.startService()


def main():
    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║   cTrader Trading Journal Daemon         ║")
    print("  ║   Read-only • Auto-logging • PostgreSQL  ║")
    print("  ╚══════════════════════════════════════════╝")
    print()
    log(f"Server:   {HOST}:{PORT} ({ACCOUNT_TYPE.upper()})")
    log(
        f"Database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else DATABASE_URL}"
    )
    log(f"Backfill: {BACKFILL_DAYS} days")
    print()

    start_client()
    reactor.run()


if __name__ == "__main__":
    main()
