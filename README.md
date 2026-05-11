# cTrader Open API Client

A Python application to connect to the cTrader trading platform via Open API (TCP/Protobuf).

## Features

- **OAuth 2.0 token flow** — local callback server handles the redirect
- **Account discovery** — lists all trading accounts linked to your cTID
- **Open positions** — symbol, side, volume, entry price, SL/TP, swap
- **Pending orders** — limit, stop, stop-limit orders with details
- **Deal history** — last 7 days of executed trades with P&L
- **Live execution events** — real-time feed of position opens/closes/modifications

## Prerequisites

1. An approved cTrader Open API application ([register here](https://openapi.ctrader.com))
2. Python 3.10+

## Setup

```bash
# Clone / copy files, then:
cd ctrader-app

# Install dependencies
pip install -r requirements.txt

# Configure credentials
cp .env.example .env
# Edit .env with your CLIENT_ID, CLIENT_SECRET, REDIRECT_URI
```

## Usage

### Step 1: Get your access token (one-time)

```bash
python get_token.py
```

This opens your browser → you log in with your cTrader ID → grant access →
tokens are saved to `tokens.json`. Run again to refresh expired tokens.

### Step 2: Run the client

```bash
python ctrader_client.py
```

Output:
```
=== cTrader Open API Client ===

  Server:  demo.ctraderapi.com:5035 (DEMO)
  Token:   mos8Bw3D4EG0fRPd4E...

───── Step 1: App Authentication ──────────────────────
  App authenticated successfully

───── Step 2: Fetching Accounts ───────────────────────
  Found 2 account(s):
    [0] Account ID: 12345678  |  DEMO  |  Broker: 1104926
    [1] Account ID: 87654321  |  LIVE  |  Broker: 1104927

───── Open Positions & Pending Orders ─────────────────
  Open Positions (3):
  Symbol       Side   Volume     Entry Price    SL           TP           ...
  EURUSD       BUY    100000.00  1.08450        1.08200      1.09000      ...

───── Live Events (listening...) ──────────────────────
  [14:32:05] ORDER_FILLED: GBPUSD SELL 50000.00 @ 1.27320
```

## File Structure

```
ctrader-app/
├── .env.example        # Config template
├── .env                # Your config (git-ignored)
├── tokens.json         # OAuth tokens (git-ignored)
├── requirements.txt    # Python dependencies
├── get_token.py        # OAuth token retrieval script
├── ctrader_client.py   # Main client application
└── README.md
```

## Notes

- **Heartbeat**: The SDK handles heartbeat internally (every 10s)
- **Rate limits**: Max 50 requests/sec for general data, 5 req/sec for historical
- **Token expiry**: Access tokens last ~30 days; run `python get_token.py` to refresh
- **Live vs Demo**: Set `ACCOUNT_TYPE=live` or `ACCOUNT_TYPE=demo` in `.env`
