"""
ctrader_client.py - cTrader Open API Client (Read-Only Dashboard)

Connects to cTrader backend via TCP/Protobuf, authenticates,
and displays account info, open positions, pending orders,
deal history, and live execution events.

NO trade execution — read-only access only.

Usage:
    python ctrader_client.py
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

load_dotenv()

# ─── Configuration ────────────────────────────────────────────
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "live").lower()

# Load tokens
TOKEN_FILE = "tokens.json"
if not os.path.exists(TOKEN_FILE):
    print("ERROR: No tokens.json found. Run get_token.py first.")
    sys.exit(1)

with open(TOKEN_FILE) as f:
    tokens = json.load(f)

ACCESS_TOKEN = tokens["accessToken"]

# Pick host based on account type
HOST = EndPoints.PROTOBUF_LIVE_HOST if ACCOUNT_TYPE == "live" else EndPoints.PROTOBUF_DEMO_HOST
PORT = EndPoints.PROTOBUF_PORT

# ─── State ────────────────────────────────────────────────────
account_id = None
symbol_cache = {}
_client = None


# ─── Helpers ──────────────────────────────────────────────────

def ts_to_str(timestamp_ms):
    """Convert millisecond timestamp to readable string."""
    if not timestamp_ms:
        return "N/A"
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_side(trade_side):
    """Convert trade side enum to string."""
    return "BUY" if trade_side == 1 else "SELL"


def print_separator(title=""):
    width = 60
    if title:
        print(f"\n{'─' * 5} {title} {'─' * (width - len(title) - 7)}")
    else:
        print(f"{'─' * width}")


def extract_or_error(result):
    """
    Extract the protobuf message from a response.
    If cTrader returned an error, print it and return None.
    """
    msg = Protobuf.extract(result)
    if msg.__class__.__name__ == "ProtoOAErrorRes":
        error_code = msg.errorCode if msg.HasField("errorCode") else "unknown"
        description = msg.description if msg.HasField("description") else "no description"
        print(f"  [API ERROR] Code: {error_code} — {description}")
        return None
    return msg


def on_error(failure):
    print(f"  [ERROR] {failure}")


# ─── Connection & Auth Chain ─────────────────────────────────
#
# Flow: connect → app auth → list accounts → account auth
#       → trader info → load symbols → reconcile → deal history
#       → listen for live events

def connected(client):
    global _client
    _client = client
    print(f"  Connected to {ACCOUNT_TYPE.upper()} server ({HOST}:{PORT})")
    print_separator("Step 1: App Authentication")

    request = ProtoOAApplicationAuthReq()
    request.clientId = CLIENT_ID
    request.clientSecret = CLIENT_SECRET
    deferred = _client.send(request)
    deferred.addCallbacks(on_app_auth, on_error)


def on_app_auth(result):
    msg = extract_or_error(result)
    if msg is None:
        return

    print("  App authenticated successfully")
    print_separator("Step 2: Fetching Accounts")

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
        print("  No trading accounts found for this token!")
        reactor.stop()
        return

    print(f"  Found {len(accounts)} account(s):\n")
    for i, acc in enumerate(accounts):
        acc_type = "LIVE" if acc.isLive else "DEMO"
        print(f"    [{i}] Account ID: {acc.ctidTraderAccountId}  |  {acc_type}  |  Broker: {acc.traderLogin}")

    global account_id
    env_account = os.getenv("ACCOUNT_ID")

    if env_account:
        account_id = int(env_account)
        print(f"\n  Using account from .env: {account_id}")
    elif len(accounts) == 1:
        account_id = accounts[0].ctidTraderAccountId
        print(f"\n  Auto-selected only account: {account_id}")
    else:
        account_id = accounts[0].ctidTraderAccountId
        print(f"\n  Auto-selected first account: {account_id}")
        print(f"  (Set ACCOUNT_ID in .env to pick a specific one)")

    print_separator("Step 3: Account Authentication")
    request = ProtoOAAccountAuthReq()
    request.ctidTraderAccountId = account_id
    request.accessToken = ACCESS_TOKEN
    deferred = _client.send(request)
    deferred.addCallbacks(on_account_auth, on_error)


def on_account_auth(result):
    msg = extract_or_error(result)
    if msg is None:
        return

    print(f"  Account {account_id} authenticated")
    print_separator("Step 4: Account Info")

    request = ProtoOATraderReq()
    request.ctidTraderAccountId = account_id
    deferred = _client.send(request)
    deferred.addCallbacks(on_trader_info, on_error)


def on_trader_info(result):
    msg = extract_or_error(result)
    if msg is None:
        return

    trader = msg.trader
    balance = trader.balance / 100  # balance is in cents

    print(f"  Balance:         {balance:,.2f}")
    print(f"  Leverage:        1:{trader.leverageInCents // 100}")
    print(f"  Account ID:      {trader.ctidTraderAccountId}")
    print(f"  Registration:    {ts_to_str(trader.registrationTimestamp)}")

    fetch_symbols()


# ─── Symbol Cache ─────────────────────────────────────────────

def fetch_symbols():
    print_separator("Loading Symbols")
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
    print(f"  Loaded {len(symbol_cache)} symbols")

    fetch_reconcile()


# ─── Reconcile (Open Positions + Pending Orders) ─────────────

def fetch_reconcile():
    print_separator("Open Positions & Pending Orders")
    request = ProtoOAReconcileReq()
    request.ctidTraderAccountId = account_id
    deferred = _client.send(request)
    deferred.addCallbacks(on_reconcile, on_error)


def on_reconcile(result):
    msg = extract_or_error(result)
    if msg is None:
        return

    # ── Open Positions ──
    positions = msg.position
    if positions:
        print(f"\n  Open Positions ({len(positions)}):\n")
        print(f"  {'Symbol':<12} {'Side':<6} {'Volume':<10} {'Entry Price':<14} {'SL':<12} {'TP':<12} {'Swap':<10} {'Opened'}")
        print(f"  {'─'*12} {'─'*6} {'─'*10} {'─'*14} {'─'*12} {'─'*12} {'─'*10} {'─'*20}")

        for pos in positions:
            symbol = symbol_cache.get(pos.tradeData.symbolId, str(pos.tradeData.symbolId))
            side = format_side(pos.tradeData.tradeSide)
            volume = pos.tradeData.volume / 100
            entry_price = pos.price
            sl = pos.stopLoss if pos.HasField("stopLoss") else "—"
            tp = pos.takeProfit if pos.HasField("takeProfit") else "—"
            swap = pos.swap / 100 if pos.swap else 0
            opened = ts_to_str(pos.tradeData.openTimestamp)

            print(f"  {symbol:<12} {side:<6} {volume:<10.2f} {entry_price:<14.5f} {str(sl):<12} {str(tp):<12} {swap:<10.2f} {opened}")
    else:
        print("\n  No open positions.")

    # ── Pending Orders ──
    orders = msg.order
    if orders:
        print(f"\n  Pending Orders ({len(orders)}):\n")
        print(f"  {'Symbol':<12} {'Side':<6} {'Type':<12} {'Volume':<10} {'Price':<14} {'Created'}")
        print(f"  {'─'*12} {'─'*6} {'─'*12} {'─'*10} {'─'*14} {'─'*20}")

        order_types = {1: "MARKET", 2: "LIMIT", 3: "STOP", 4: "STOP_LIMIT"}
        for order in orders:
            symbol = symbol_cache.get(order.tradeData.symbolId, str(order.tradeData.symbolId))
            side = format_side(order.tradeData.tradeSide)
            o_type = order_types.get(order.orderType, str(order.orderType))
            volume = order.tradeData.volume / 100
            price = order.limitPrice if order.HasField("limitPrice") else (order.stopPrice if order.HasField("stopPrice") else 0)
            created = ts_to_str(order.tradeData.openTimestamp)

            print(f"  {symbol:<12} {side:<6} {o_type:<12} {volume:<10.2f} {price:<14.5f} {created}")
    else:
        print("\n  No pending orders.")

    fetch_recent_deals()


# ─── Deal History ─────────────────────────────────────────────

def fetch_recent_deals():
    print_separator("Recent Deals (Last 7 Days)")

    now_ms = int(time.time() * 1000)
    week_ago_ms = now_ms - (7 * 24 * 60 * 60 * 1000)

    request = ProtoOADealListReq()
    request.ctidTraderAccountId = account_id
    request.fromTimestamp = week_ago_ms
    request.toTimestamp = now_ms
    request.maxRows = 50
    deferred = _client.send(request)
    deferred.addCallbacks(on_deal_list, on_error)


def on_deal_list(result):
    msg = extract_or_error(result)
    if msg is None:
        return

    deals = msg.deal
    if deals:
        print(f"\n  Found {len(deals)} deal(s):\n")
        print(f"  {'Symbol':<12} {'Side':<6} {'Volume':<10} {'Price':<14} {'P&L':<12} {'Commission':<12} {'Time'}")
        print(f"  {'─'*12} {'─'*6} {'─'*10} {'─'*14} {'─'*12} {'─'*12} {'─'*20}")

        for deal in deals:
            symbol = symbol_cache.get(deal.symbolId, str(deal.symbolId))
            side = format_side(deal.tradeSide)
            volume = deal.volume / 100
            price = deal.executionPrice
            pnl = deal.closePositionDetail.grossProfit / 100 if deal.HasField("closePositionDetail") else 0
            commission = deal.commission / 100 if deal.commission else 0
            exec_time = ts_to_str(deal.executionTimestamp)

            pnl_str = f"{pnl:+.2f}" if pnl != 0 else "—"
            print(f"  {symbol:<12} {side:<6} {volume:<10.2f} {price:<14.5f} {pnl_str:<12} {commission:<12.2f} {exec_time}")
    else:
        print("\n  No deals in the last 7 days.")

    start_listening()


# ─── Live Event Listener (read-only) ─────────────────────────

def start_listening():
    print_separator("Live Events (listening...)")
    print("  Watching for position/order changes. Press Ctrl+C to stop.\n")


def on_message(client, message):
    """Handle incoming messages — only display, never trade."""
    msg = Protobuf.extract(message)
    msg_type = msg.__class__.__name__

    if msg_type == "ProtoOAExecutionEvent":
        handle_execution_event(msg)
    elif msg_type == "ProtoOASpotEvent":
        pass  # price tick, ignore unless subscribed
    elif msg_type == "ProtoHeartbeatEvent":
        pass  # keepalive
    elif msg_type == "ProtoOAErrorRes":
        error_code = msg.errorCode if msg.HasField("errorCode") else "?"
        desc = msg.description if msg.HasField("description") else ""
        print(f"  [LIVE ERROR] {error_code}: {desc}")


def handle_execution_event(msg):
    """Display real-time execution events (position opens, closes, fills)."""
    exec_types = {
        1: "ORDER_ACCEPTED",
        2: "ORDER_FILLED",
        3: "ORDER_REPLACED",
        4: "ORDER_CANCELLED",
        5: "ORDER_EXPIRED",
        6: "ORDER_REJECTED",
        7: "ORDER_CANCEL_REJECTED",
    }

    exec_type = exec_types.get(msg.executionType, f"TYPE_{msg.executionType}")
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")

    if msg.HasField("position"):
        pos = msg.position
        symbol = symbol_cache.get(pos.tradeData.symbolId, str(pos.tradeData.symbolId))
        side = format_side(pos.tradeData.tradeSide)
        volume = pos.tradeData.volume / 100
        price = pos.price
        print(f"  [{timestamp}] {exec_type}: {symbol} {side} {volume:.2f} @ {price:.5f}")

    elif msg.HasField("order"):
        order = msg.order
        symbol = symbol_cache.get(order.tradeData.symbolId, str(order.tradeData.symbolId))
        side = format_side(order.tradeData.tradeSide)
        volume = order.tradeData.volume / 100
        print(f"  [{timestamp}] {exec_type}: {symbol} {side} {volume:.2f}")


# ─── Disconnection ────────────────────────────────────────────

def disconnected(client, reason):
    print(f"\n  Disconnected: {reason}")


# ─── Main ─────────────────────────────────────────────────────

def main():
    print("\n=== cTrader Dashboard (Read-Only) ===\n")
    print(f"  Server:  {HOST}:{PORT} ({ACCOUNT_TYPE.upper()})")
    print(f"  Token:   {ACCESS_TOKEN[:20]}...")
    print()

    client = Client(HOST, PORT, TcpProtocol)
    client.setConnectedCallback(connected)
    client.setDisconnectedCallback(disconnected)
    client.setMessageReceivedCallback(on_message)

    client.startService()
    reactor.run()


if __name__ == "__main__":
    main()
