# Journal Daemon — Service Documentation

## Overview

`journal_daemon.py` is a long-running background process that connects to the cTrader trading platform via TCP/Protobuf and automatically records every trade into a PostgreSQL database. It is read-only — it never places, modifies, or closes trades.

The daemon handles three core responsibilities: backfilling historical trades on startup, recording live trades in real time, and auto-reconnecting on disconnection.

---

## Architecture

```
cTrader Backend (demo.ctraderapi.com:5035)
        │
        │  TCP + SSL + Protobuf
        ▼
┌──────────────────────────┐
│    journal_daemon.py     │
│                          │
│  ┌────────────────────┐  │
│  │  Twisted reactor   │  │  Async event loop
│  │  (TCP client)      │  │
│  └────────┬───────────┘  │
│           │              │
│  ┌────────▼───────────┐  │
│  │  recorder.py       │  │  Trade recording logic
│  │  sessions.py       │  │  Session auto-tagging
│  └────────┬───────────┘  │
│           │              │
│  ┌────────▼───────────┐  │
│  │  SQLAlchemy ORM    │  │
│  └────────┬───────────┘  │
└───────────┼──────────────┘
            │
            ▼
     PostgreSQL
     (trades, trade_notes, screenshots)
```

---

## Startup Sequence

The daemon follows a strict chain of callbacks. Each step must succeed before the next begins.

```
1. TCP Connect
   └─▶ SSL handshake with cTrader proxy server

2. App Authentication (ProtoOAApplicationAuthReq)
   └─▶ Sends CLIENT_ID + CLIENT_SECRET
   └─▶ Server validates the registered API application

3. Account Discovery (ProtoOAGetAccountListByAccessTokenReq)
   └─▶ Retrieves all cTID-linked trading accounts (live + demo)
   └─▶ Filters by ACCOUNT_TYPE or uses ACCOUNT_ID from .env

4. Account Authentication (ProtoOAAccountAuthReq)
   └─▶ Authenticates the specific trading account with ACCESS_TOKEN
   └─▶ CRITICAL: Account type must match server
       (demo account → demo server, live account → live server)

5. Symbol Loading (ProtoOASymbolsListReq)
   └─▶ Loads all available symbols into an in-memory cache
   └─▶ Maps symbol_id → symbol_name (e.g., 1 → "EURUSD")

6. Reconcile (ProtoOAReconcileReq)
   └─▶ Fetches all currently open positions and pending orders
   └─▶ Records any open positions not already in the database

7. Backfill (ProtoOADealListReq)
   └─▶ Fetches deals from the last BACKFILL_DAYS (default 30)
   └─▶ Groups deals by position_id to reconstruct full trade lifecycles
   └─▶ Skips trades already in the database (idempotent)

8. Live Listening
   └─▶ Daemon enters steady state
   └─▶ Execution events arrive automatically (no explicit subscribe needed)
   └─▶ Each event is processed and written to PostgreSQL
```

---

## Live Event Handling

After startup, the daemon listens for `ProtoOAExecutionEvent` messages. These fire automatically for any authenticated account whenever a position or order changes.

### Event Types Handled

| executionType | Name               | Action                                                   |
|---------------|--------------------|----------------------------------------------------------|
| 2             | ORDER_FILLED       | Position opened or closed — check `positionStatus` field |
| 3             | ORDER_REPLACED     | SL/TP modified — update existing trade record            |
| 4             | ORDER_CANCELLED    | Pending order cancelled — log only                       |
| 5             | ORDER_EXPIRED      | Pending order expired — log only                         |
| 6             | ORDER_REJECTED     | Order rejected by server — log only                      |

### Position Open vs Close Detection

When `executionType == 2` (ORDER_FILLED), the daemon checks `position.positionStatus`:

- `positionStatus != 2` → position opened → `record_position_open()`
- `positionStatus == 2` → position closed → `record_position_close()`

On close, the daemon extracts P&L from `deal.closePositionDetail`:

```
net_profit = gross_profit - |commission| - |swap|
```

### Events Ignored

- `ProtoHeartbeatEvent` — keepalive, no action
- `ProtoOASpotEvent` — price ticks (only received if explicitly subscribed)

---

## Session Auto-Tagging

Every trade is tagged with the market session active at entry time (UTC):

| Session    | UTC Hours   | Notes                              |
|------------|-------------|------------------------------------|
| asian      | 00:00–08:00 | Tokyo, Sydney, Singapore           |
| london     | 08:00–15:00 | Includes London/NY overlap 13:00–15:00 |
| new_york   | 15:00–21:00 | After London close                 |
| off_hours  | 21:00–00:00 | Low liquidity period               |

The overlap period (13:00–15:00 UTC) is tagged as `london` because London opened first.

---

## Database Schema

### trades

The primary table. One row per position lifecycle.

| Column         | Type          | Description                                      |
|----------------|---------------|--------------------------------------------------|
| id             | serial PK     | Auto-increment                                   |
| position_id    | bigint UNIQUE | cTrader position identifier                      |
| deal_id_open   | bigint        | Opening deal ID                                  |
| deal_id_close  | bigint        | Closing deal ID (null while open)                |
| symbol_id      | bigint        | cTrader internal symbol ID                       |
| symbol         | varchar(30)   | Human-readable symbol name (EURUSD, XAUUSD)      |
| side           | varchar(4)    | BUY or SELL                                      |
| volume         | float         | Volume in cTrader centiunits                     |
| lots           | float         | Human-readable lots (volume / 100000)            |
| entry_price    | float         | Position entry price                             |
| entry_time     | timestamptz   | Entry time (UTC)                                 |
| exit_price     | float         | Position exit price (null while open)            |
| exit_time      | timestamptz   | Exit time (null while open)                      |
| stop_loss      | float         | Current SL (updated on modification)             |
| take_profit    | float         | Current TP (updated on modification)             |
| gross_profit   | float         | Raw P&L before costs (filled on close)           |
| commission     | float         | Trading commission (filled on close)             |
| swap           | float         | Overnight swap charge (filled on close)          |
| net_profit     | float         | gross_profit - |commission| - |swap|             |
| session        | varchar(20)   | Auto-detected: asian/london/new_york/off_hours   |
| status         | varchar(10)   | "open" or "closed"                               |
| created_at     | timestamptz   | Record creation time                             |
| updated_at     | timestamptz   | Last modification time                           |

Indexes: `position_id` (unique), `(symbol, entry_time)`, `session`, `status`.

### trade_notes

Manual notes attached to trades via the CLI.

| Column    | Type        | Description                                    |
|-----------|-------------|------------------------------------------------|
| id        | serial PK   | Auto-increment                                 |
| trade_id  | int FK      | References trades.id (CASCADE delete)          |
| content   | text        | Note content                                   |
| note_type | varchar(20) | general, entry_reason, exit_reason, lesson     |
| created_at| timestamptz | When the note was added                        |

### screenshots

Chart screenshots linked to trades (Phase 3 — web dashboard).

| Column          | Type         | Description                          |
|-----------------|--------------|--------------------------------------|
| id              | serial PK    | Auto-increment                       |
| trade_id        | int FK       | References trades.id (CASCADE delete)|
| file_path       | varchar(500) | Path to screenshot file              |
| caption         | varchar(200) | Optional description                 |
| chart_timeframe | varchar(10)  | M1, M5, H1, H4, D1, etc.           |
| created_at      | timestamptz  | When uploaded                        |

---

## Configuration

All configuration is via environment variables (`.env` file).

| Variable       | Required | Default | Description                          |
|----------------|----------|---------|--------------------------------------|
| CLIENT_ID      | yes      | —       | cTrader Open API application ID      |
| CLIENT_SECRET  | yes      | —       | cTrader Open API application secret  |
| ACCOUNT_TYPE   | no       | demo    | "demo" or "live" (selects server)    |
| ACCOUNT_ID     | no       | —       | Specific account ID (auto-selects if blank) |
| DATABASE_URL   | yes      | —       | PostgreSQL connection string         |
| BACKFILL_DAYS  | no       | 30      | Days of history to backfill on start |

The daemon also requires a `tokens.json` file in the working directory containing `accessToken` and `refreshToken` from the OAuth flow (`get_token.py`).

---

## Running the Daemon

### Direct

```bash
cd ctrader-journal
python journal_daemon.py
```

### As a systemd Service

Create `/etc/systemd/system/ctrader-journal.service`:

```ini
[Unit]
Description=cTrader Trading Journal Daemon
After=network.target postgresql.service

[Service]
Type=simple
User=zee
WorkingDirectory=/home/zee/ctrader-journal
ExecStart=/home/zee/ctrader-journal/venv/bin/python journal_daemon.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ctrader-journal
sudo systemctl start ctrader-journal
```

View logs:

```bash
journalctl -u ctrader-journal -f
```

---

## Reconnection Behaviour

On disconnection (network drop, server maintenance), the daemon:

1. Logs the disconnect reason
2. Waits 5 seconds
3. Creates a new TCP client and reconnects
4. Runs the full auth chain again (app auth → account auth → symbol load → reconcile → backfill)

The backfill step is idempotent — trades already in the database are skipped by checking `position_id` uniqueness. This means a reconnect never creates duplicate records.

---

## Error Handling

### API Errors

Every callback uses `extract_or_error()` which checks if the response is a `ProtoOAErrorRes`. Common errors:

| Error Code           | Cause                                              | Fix                                    |
|----------------------|----------------------------------------------------|-----------------------------------------|
| CANT_ROUTE_REQUEST   | Account type doesn't match server                  | Set correct ACCOUNT_TYPE or ACCOUNT_ID |
| INVALID_ACCESS_TOKEN | Token expired (~30 days)                           | Run `python get_token.py` to refresh   |
| CH_ACCOUNT_NOT_FOUND | Account ID doesn't exist                           | Check ACCOUNT_ID in .env               |
| TOO_MANY_REQUESTS    | Rate limit exceeded (50 req/sec general, 5 historical) | Reduce request frequency          |

### Twisted Deferred Errors

Network-level failures (connection refused, SSL errors, timeouts) are caught by the `on_error` callback and logged. The disconnection handler then triggers reconnection.

### Database Errors

SQLAlchemy sessions are opened per-event and closed in `finally` blocks to prevent connection leaks. The `pool_pre_ping=True` engine option handles stale database connections after PostgreSQL restarts.

---

## Rate Limits

cTrader enforces per-connection rate limits:

- General requests: 50 per second
- Historical data requests: 5 per second

The daemon stays well within these limits — it only makes requests during the startup chain and backfill. During steady-state listening, it receives events passively with no outbound requests.

---

## Token Management

Access tokens expire after approximately 30 days. When the daemon receives an `INVALID_ACCESS_TOKEN` error, it will log the error but cannot self-heal — you need to manually refresh the token:

```bash
python get_token.py
# Select "y" to refresh existing tokens
```

Then restart the daemon. For unattended operation, consider adding a cron job that refreshes the token weekly:

```bash
# crontab -e
0 3 * * 0 cd /home/zee/ctrader-journal && /home/zee/ctrader-journal/venv/bin/python -c "
from get_token import refresh_access_token, save_tokens
import json
with open('tokens.json') as f:
    tokens = json.load(f)
new_tokens = refresh_access_token(tokens['refreshToken'])
save_tokens(new_tokens)
"
```

---

## Log Output

The daemon logs to stdout with UTC timestamps. Sample output:

```
  ╔══════════════════════════════════════════╗
  ║   cTrader Trading Journal Daemon         ║
  ║   Read-only • Auto-logging • PostgreSQL  ║
  ╚══════════════════════════════════════════╝

[2026-05-11 14:30:01] Server:   demo.ctraderapi.com:5035 (DEMO)
[2026-05-11 14:30:01] Database: localhost:5432/trading_journal
[2026-05-11 14:30:01] Backfill: 30 days

[2026-05-11 14:30:02] Connected to DEMO (demo.ctraderapi.com:5035)
[2026-05-11 14:30:02] App authenticated
[2026-05-11 14:30:02] Using account: 12345678
[2026-05-11 14:30:02] Account 12345678 authenticated
[2026-05-11 14:30:03] Loaded 342 symbols
[2026-05-11 14:30:03] Reconciled 2 open position(s)
[2026-05-11 14:30:03] Backfilling last 30 days...
[2026-05-11 14:30:04] Backfill: 47 new, 0 skipped
[2026-05-11 14:30:04] Daemon ready — listening for live trades...

[2026-05-11 15:12:33] OPENED: EURUSD BUY 0.10L @ 1.08450 [london]
[2026-05-11 15:45:17] MODIFIED: EURUSD SL=1.084 TP=1.087
[2026-05-11 16:20:44] CLOSED: EURUSD BUY 0.10L @ 1.08720 | P&L: +24.30
```

---

## CLI Companion

The journal CLI (`journal_cli.py`) reads from the same PostgreSQL database the daemon writes to. It can be used in a separate terminal while the daemon runs.

```bash
python journal_cli.py today
python journal_cli.py stats week
python journal_cli.py note 12 "London session breakout, H1 FVG fill"
python journal_cli.py export may_trades.csv
```

See `python journal_cli.py` (no arguments) for the full command reference.

---

## File Structure

```
ctrader-journal/
├── .env                    # Configuration (git-ignored)
├── .env.example            # Template
├── tokens.json             # OAuth tokens (git-ignored)
├── requirements.txt        # Python dependencies
├── get_token.py            # OAuth token setup (from Phase 0)
├── journal_daemon.py       # The daemon entry point
├── journal_cli.py          # CLI review tool
└── journal/
    ├── __init__.py
    ├── models.py           # SQLAlchemy models (Trade, TradeNote, Screenshot)
    ├── sessions.py         # Session detection logic
    └── recorder.py         # Database write operations
```
