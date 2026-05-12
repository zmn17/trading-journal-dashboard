"""
relay_server.py - cTrader → WebSocket relay for ESP32 TFT chart

Connects to cTrader via TCP/Protobuf, subscribes to spot prices
and live trendbars, fetches historical candles, tracks open positions,
and relays everything to connected ESP32 clients over WebSocket as JSON.

Architecture:
  cTrader (TCP/Protobuf/SSL) → relay_server.py → WebSocket (JSON) → ESP32

WebSocket messages sent to ESP32:
  {"type":"candles","symbol":"EURUSD","period":"M5","data":[[ts,o,h,l,c],...]}
  {"type":"tick","symbol":"EURUSD","bid":1.08450,"ask":1.08465,"ts":1234567890}
  {"type":"candle_update","symbol":"EURUSD","period":"M5","candle":[ts,o,h,l,c]}
  {"type":"positions","data":[{"symbol":"EURUSD","side":"BUY","price":1.084,"lots":0.1},...]}
  {"type":"config","symbols":["EURUSD","XAUUSD","GBPUSD"],"periods":["M1","M5","M15","H1"]}

WebSocket messages received from ESP32:
  {"cmd":"subscribe","symbol":"EURUSD","period":"M5"}
  {"cmd":"get_positions"}

Usage:
  python relay_server.py
"""

import os
import sys
import json
import time
import asyncio
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from dotenv import load_dotenv

# Twisted must run in its own thread
from twisted.internet import reactor as twisted_reactor

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import *
from ctrader_open_api.messages.OpenApiMessages_pb2 import *
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import *

import websockets
from websockets.asyncio.server import serve

load_dotenv()

# ─── Config ──────────────────────────────────────────────────
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "demo").lower()
DATABASE_URL = os.getenv("DATABASE_URL", "")
WS_PORT = int(os.getenv("WS_PORT", "8765"))

# Symbols to track (symbol_name -> symbol_id, populated on connect)
WATCH_SYMBOLS = os.getenv("WATCH_SYMBOLS", "EURUSD,XAUUSD,GBPUSD").split(",")
WATCH_SYMBOLS = [s.strip() for s in WATCH_SYMBOLS]

# Periods available for cycling
PERIODS = ["M1", "M5", "M15", "H1"]
PERIOD_MAP = {"M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5,
              "M10": 6, "M15": 7, "M30": 8, "H1": 9, "H4": 10}
PERIOD_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}

HISTORY_CANDLES = 60  # how many historical candles to send

TOKEN_FILE = "tokens.json"
if not os.path.exists(TOKEN_FILE):
    print("[ERROR] No tokens.json found. Run get_token.py first.")
    sys.exit(1)

with open(TOKEN_FILE) as f:
    tokens = json.load(f)
ACCESS_TOKEN = tokens["accessToken"]

HOST = EndPoints.PROTOBUF_LIVE_HOST if ACCOUNT_TYPE == "live" else EndPoints.PROTOBUF_DEMO_HOST
PORT = EndPoints.PROTOBUF_PORT

# ─── Shared State (thread-safe via GIL for simple reads/writes) ─────
account_id = None
symbol_cache = {}        # symbol_name -> symbol_id
symbol_names = {}        # symbol_id -> symbol_name
symbol_digits = {}       # symbol_id -> digits (for price precision)
_client = None

# Candle storage: candles[symbol_name][period] = [[ts,o,h,l,c], ...]
candles = defaultdict(lambda: defaultdict(list))

# Latest tick: ticks[symbol_name] = {"bid": float, "ask": float, "ts": int}
ticks = {}

# Open positions: positions = [{"symbol","side","price","lots","sl","tp","pnl"}, ...]
positions = []

# Active subscriptions per symbol
spot_subscribed = set()       # symbol_ids with spot subscription
trendbar_subscribed = set()   # (symbol_id, period_enum) pairs

# WebSocket clients
ws_clients = set()

# Subscription state per WS client
client_subscriptions = {}  # ws -> {"symbol": str, "period": str}


# ─── Logging ─────────────────────────────────────────────────
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ─── Price helpers ───────────────────────────────────────────
def transform_price(raw, digits=5):
    return round(raw / (10 ** digits), digits)


def transform_trendbar(tb, digits=5):
    """Convert a ProtoOATrendbar to [timestamp_ms, open, high, low, close]."""
    divisor = 10 ** digits
    low = tb.low / divisor
    open_p = (tb.low + tb.deltaOpen) / divisor
    high_p = (tb.low + tb.deltaHigh) / divisor
    close_p = (tb.low + tb.deltaClose) / divisor
    ts_ms = tb.utcTimestampInMinutes * 60 * 1000
    return [ts_ms, round(open_p, digits), round(high_p, digits),
            round(low_p, digits), round(close_p, digits)]


# ─── WebSocket broadcast ────────────────────────────────────
def broadcast(msg_dict, filter_fn=None):
    """Queue a broadcast to all (or filtered) WS clients."""
    data = json.dumps(msg_dict)
    asyncio.run_coroutine_threadsafe(_broadcast(data, filter_fn), ws_loop)


async def _broadcast(data, filter_fn=None):
    targets = ws_clients if filter_fn is None else {c for c in ws_clients if filter_fn(c)}
    dead = set()
    for ws in targets:
        try:
            await ws.send(data)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)
    for d in dead:
        client_subscriptions.pop(d, None)


def broadcast_to_subscribers(symbol, period, msg_dict):
    """Send only to clients subscribed to this symbol+period."""
    data = json.dumps(msg_dict)
    asyncio.run_coroutine_threadsafe(
        _broadcast(data, lambda c: client_subscriptions.get(c, {}).get("symbol") == symbol
                   and client_subscriptions.get(c, {}).get("period") == period),
        ws_loop
    )


# ─── cTrader Error Handling ──────────────────────────────────
def extract_or_error(result):
    msg = Protobuf.extract(result)
    if msg.__class__.__name__ == "ProtoOAErrorRes":
        code = msg.errorCode if msg.HasField("errorCode") else "?"
        desc = msg.description if msg.HasField("description") else ""
        log(f"[API ERROR] {code}: {desc}")
        return None
    return msg


def on_error(failure):
    log(f"[ERROR] {failure}")


def format_side(trade_side):
    return "BUY" if trade_side == 1 else "SELL"


# ─── cTrader Auth Chain ─────────────────────────────────────
def connected(client):
    global _client
    _client = client
    log(f"Connected to {ACCOUNT_TYPE.upper()} ({HOST}:{PORT})")

    request = ProtoOAApplicationAuthReq()
    request.clientId = CLIENT_ID
    request.clientSecret = CLIENT_SECRET
    d = _client.send(request)
    d.addCallbacks(on_app_auth, on_error)


def on_app_auth(result):
    msg = extract_or_error(result)
    if msg is None: return
    log("App authenticated")

    request = ProtoOAGetAccountListByAccessTokenReq()
    request.accessToken = ACCESS_TOKEN
    d = _client.send(request)
    d.addCallbacks(on_account_list, on_error)


def on_account_list(result):
    msg = extract_or_error(result)
    if msg is None: return

    accounts = msg.ctidTraderAccount
    global account_id
    env_account = os.getenv("ACCOUNT_ID")

    if env_account:
        account_id = int(env_account)
    else:
        is_live = ACCOUNT_TYPE == "live"
        matching = [a for a in accounts if a.isLive == is_live]
        account_id = matching[0].ctidTraderAccountId if matching else accounts[0].ctidTraderAccountId

    log(f"Using account: {account_id}")

    request = ProtoOAAccountAuthReq()
    request.ctidTraderAccountId = account_id
    request.accessToken = ACCESS_TOKEN
    d = _client.send(request)
    d.addCallbacks(on_account_auth, on_error)


def on_account_auth(result):
    msg = extract_or_error(result)
    if msg is None: return
    log("Account authenticated")

    request = ProtoOASymbolsListReq()
    request.ctidTraderAccountId = account_id
    d = _client.send(request)
    d.addCallbacks(on_symbols, on_error)


def on_symbols(result):
    msg = extract_or_error(result)
    if msg is None: return

    for sym in msg.symbol:
        symbol_names[sym.symbolId] = sym.symbolName
        if sym.symbolName in WATCH_SYMBOLS:
            symbol_cache[sym.symbolName] = sym.symbolId

    log(f"Loaded {len(symbol_names)} symbols, watching: {list(symbol_cache.keys())}")

    # Get full symbol details for digits
    if symbol_cache:
        request = ProtoOASymbolByIdReq()
        request.ctidTraderAccountId = account_id
        for sid in symbol_cache.values():
            request.symbolId.append(sid)
        d = _client.send(request)
        d.addCallbacks(on_symbol_details, on_error)
    else:
        log("[WARN] None of the WATCH_SYMBOLS found!")
        subscribe_spots()


def on_symbol_details(result):
    msg = extract_or_error(result)
    if msg is None:
        subscribe_spots()
        return

    for sym in msg.symbol:
        symbol_digits[sym.symbolId] = sym.digits
        name = symbol_names.get(sym.symbolId, str(sym.symbolId))
        log(f"  {name}: {sym.digits} digits")

    subscribe_spots()


def subscribe_spots():
    """Subscribe to spot prices for all watched symbols."""
    for name, sid in symbol_cache.items():
        if sid in spot_subscribed:
            continue
        request = ProtoOASubscribeSpotsReq()
        request.ctidTraderAccountId = account_id
        request.symbolId.append(sid)
        request.subscribeToSpotTimestamp = True
        d = _client.send(request)
        d.addErrback(on_error)
        spot_subscribed.add(sid)
        log(f"Subscribed to spots: {name}")

    # Subscribe to default trendbars (M5 for all symbols)
    for name, sid in symbol_cache.items():
        subscribe_trendbar(sid, "M5")

    # Fetch open positions
    fetch_positions()


def subscribe_trendbar(symbol_id, period_str):
    """Subscribe to live trendbar updates for a symbol+period."""
    period_enum = PERIOD_MAP.get(period_str)
    if period_enum is None:
        return
    key = (symbol_id, period_enum)
    if key in trendbar_subscribed:
        return

    request = ProtoOASubscribeLiveTrendbarReq()
    request.ctidTraderAccountId = account_id
    request.symbolId = symbol_id
    request.period = period_enum
    d = _client.send(request)
    d.addErrback(on_error)
    trendbar_subscribed.add(key)

    name = symbol_names.get(symbol_id, str(symbol_id))
    log(f"Subscribed to trendbar: {name} {period_str}")


def fetch_historical_candles(symbol_name, period_str):
    """Fetch historical candles and send to WS clients."""
    sid = symbol_cache.get(symbol_name)
    if sid is None:
        return

    period_enum = PERIOD_MAP.get(period_str)
    if period_enum is None:
        return

    # Also subscribe to live trendbar if not already
    subscribe_trendbar(sid, period_str)

    minutes = PERIOD_MINUTES.get(period_str, 5)
    now_ms = int(time.time() * 1000)
    from_ms = now_ms - (HISTORY_CANDLES * minutes * 60 * 1000)

    request = ProtoOAGetTrendbarsReq()
    request.ctidTraderAccountId = account_id
    request.symbolId = sid
    request.period = period_enum
    request.fromTimestamp = from_ms
    request.toTimestamp = now_ms

    d = _client.send(request, clientMsgId=f"{symbol_name}|{period_str}")
    d.addCallbacks(on_historical_candles, on_error)


def on_historical_candles(result):
    msg = extract_or_error(result)
    if msg is None:
        return

    client_msg_id = result.clientMsgId if hasattr(result, 'clientMsgId') and result.clientMsgId else ""

    # Try to extract symbol+period from clientMsgId
    if "|" in client_msg_id:
        sym_name, period_str = client_msg_id.split("|", 1)
    else:
        # Fallback: try to determine from the message
        sym_name = symbol_names.get(msg.symbolId if hasattr(msg, 'symbolId') else 0, "UNKNOWN")
        period_str = "M5"

    sid = symbol_cache.get(sym_name, 0)
    digits = symbol_digits.get(sid, 5)

    bars = []
    for tb in msg.trendbar:
        bar = transform_trendbar(tb, digits)
        bars.append(bar)

    # Store in memory
    candles[sym_name][period_str] = bars

    log(f"Historical candles: {sym_name} {period_str} → {len(bars)} bars")

    # Broadcast to subscribers
    broadcast_to_subscribers(sym_name, period_str, {
        "type": "candles",
        "symbol": sym_name,
        "period": period_str,
        "data": bars,
    })


def fetch_positions():
    request = ProtoOAReconcileReq()
    request.ctidTraderAccountId = account_id
    d = _client.send(request)
    d.addCallbacks(on_positions, on_error)


def on_positions(result):
    msg = extract_or_error(result)
    if msg is None: return

    global positions
    positions = []
    for pos in msg.position:
        sid = pos.tradeData.symbolId
        name = symbol_names.get(sid, str(sid))
        digits = symbol_digits.get(sid, 5)
        sl = pos.stopLoss if pos.HasField("stopLoss") else None
        tp = pos.takeProfit if pos.HasField("takeProfit") else None

        positions.append({
            "symbol": name,
            "side": format_side(pos.tradeData.tradeSide),
            "price": round(pos.price, digits),
            "lots": round(pos.tradeData.volume / 100 / 100000, 2),
            "sl": round(sl, digits) if sl else None,
            "tp": round(tp, digits) if tp else None,
        })

    log(f"Positions: {len(positions)} open")
    broadcast({"type": "positions", "data": positions})

    # Send config to any connected clients
    broadcast({
        "type": "config",
        "symbols": list(symbol_cache.keys()),
        "periods": PERIODS,
    })

    log("Relay ready — listening for ticks and WS clients...")


# ─── Live Message Handler ────────────────────────────────────
def on_message(client, message):
    msg = Protobuf.extract(message)
    msg_type = msg.__class__.__name__

    if msg_type == "ProtoOASpotEvent":
        handle_spot(msg)
    elif msg_type == "ProtoOAExecutionEvent":
        # Position changed — refresh
        twisted_reactor.callLater(0.5, fetch_positions)
    elif msg_type == "ProtoHeartbeatEvent":
        pass


def handle_spot(msg):
    sid = msg.symbolId
    name = symbol_names.get(sid)
    if not name:
        return

    digits = symbol_digits.get(sid, 5)
    tick = ticks.get(name, {"bid": 0, "ask": 0, "ts": 0})

    if msg.HasField("bid"):
        tick["bid"] = round(msg.bid / (10 ** digits), digits)
    if msg.HasField("ask"):
        tick["ask"] = round(msg.ask / (10 ** digits), digits)
    if msg.HasField("timestamp"):
        tick["ts"] = msg.timestamp

    ticks[name] = tick

    # Check for trendbar data in the spot event
    if msg.trendbar:
        for tb in msg.trendbar:
            period_names = {v: k for k, v in PERIOD_MAP.items()}
            period_str = period_names.get(tb.period, None)
            if period_str is None:
                continue

            bar = transform_trendbar(tb, digits)

            # Update or append in candle storage
            stored = candles[name][period_str]
            if stored and stored[-1][0] == bar[0]:
                stored[-1] = bar  # update current candle
            else:
                stored.append(bar)
                # Trim to max history
                if len(stored) > HISTORY_CANDLES + 10:
                    candles[name][period_str] = stored[-HISTORY_CANDLES:]

            broadcast_to_subscribers(name, period_str, {
                "type": "candle_update",
                "symbol": name,
                "period": period_str,
                "candle": bar,
            })

    # Broadcast tick to all clients subscribed to this symbol
    broadcast(
        {"type": "tick", "symbol": name, "bid": tick["bid"], "ask": tick["ask"], "ts": tick["ts"]},
        filter_fn=lambda c: client_subscriptions.get(c, {}).get("symbol") == name,
    )


# ─── Disconnection ──────────────────────────────────────────
def disconnected(client, reason):
    log(f"Disconnected: {reason}")
    spot_subscribed.clear()
    trendbar_subscribed.clear()
    log("Reconnecting in 5s...")
    twisted_reactor.callLater(5, start_ctrader)


# ─── WebSocket Server ────────────────────────────────────────
async def ws_handler(websocket):
    ws_clients.add(websocket)
    client_subscriptions[websocket] = {}
    log(f"ESP32 connected ({len(ws_clients)} clients)")

    # Send config
    await websocket.send(json.dumps({
        "type": "config",
        "symbols": list(symbol_cache.keys()),
        "periods": PERIODS,
    }))

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
                cmd = msg.get("cmd")

                if cmd == "subscribe":
                    sym = msg.get("symbol", "EURUSD")
                    period = msg.get("period", "M5")
                    client_subscriptions[websocket] = {"symbol": sym, "period": period}
                    log(f"Client subscribed: {sym} {period}")

                    # Ensure trendbar subscription exists (from Twisted thread)
                    sid = symbol_cache.get(sym)
                    if sid:
                        twisted_reactor.callFromThread(subscribe_trendbar, sid, period)
                        twisted_reactor.callFromThread(fetch_historical_candles, sym, period)

                    # Send current positions
                    await websocket.send(json.dumps({"type": "positions", "data": positions}))

                    # Send cached candles if available
                    cached = candles.get(sym, {}).get(period, [])
                    if cached:
                        await websocket.send(json.dumps({
                            "type": "candles", "symbol": sym,
                            "period": period, "data": cached,
                        }))

                    # Send latest tick
                    tick = ticks.get(sym)
                    if tick:
                        await websocket.send(json.dumps({
                            "type": "tick", "symbol": sym,
                            "bid": tick["bid"], "ask": tick["ask"], "ts": tick["ts"],
                        }))

                elif cmd == "get_positions":
                    await websocket.send(json.dumps({"type": "positions", "data": positions}))
                    twisted_reactor.callFromThread(fetch_positions)

            except json.JSONDecodeError:
                pass
    except Exception:
        pass
    finally:
        ws_clients.discard(websocket)
        client_subscriptions.pop(websocket, None)
        log(f"ESP32 disconnected ({len(ws_clients)} clients)")


# ─── Startup ─────────────────────────────────────────────────
ws_loop = None


def start_ctrader():
    client = Client(HOST, PORT, TcpProtocol)
    client.setConnectedCallback(connected)
    client.setDisconnectedCallback(disconnected)
    client.setMessageReceivedCallback(on_message)
    client.startService()


def run_twisted():
    start_ctrader()
    twisted_reactor.run(installSignalHandlers=False)


async def run_ws_server():
    global ws_loop
    ws_loop = asyncio.get_event_loop()

    async with serve(ws_handler, "0.0.0.0", WS_PORT):
        log(f"WebSocket server on ws://0.0.0.0:{WS_PORT}")
        await asyncio.Future()  # run forever


def main():
    print()
    print("  ╔═══════════════════════════════════════════╗")
    print("  ║  cTrader → ESP32 Chart Relay Server       ║")
    print("  ║  Protobuf/TCP → WebSocket/JSON            ║")
    print("  ╚═══════════════════════════════════════════╝")
    print()
    log(f"cTrader:  {HOST}:{PORT} ({ACCOUNT_TYPE.upper()})")
    log(f"Symbols:  {WATCH_SYMBOLS}")
    log(f"WS Port:  {WS_PORT}")
    print()

    # Run Twisted in a background thread
    twisted_thread = threading.Thread(target=run_twisted, daemon=True)
    twisted_thread.start()

    # Run WebSocket server in main thread (asyncio)
    asyncio.run(run_ws_server())


if __name__ == "__main__":
    main()
