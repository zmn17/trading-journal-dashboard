"""
journal_cli.py - Trading Journal CLI

Usage:
    python journal_cli.py today              # Today's trades
    python journal_cli.py week               # This week
    python journal_cli.py month              # This month
    python journal_cli.py open               # Open positions
    python journal_cli.py last [n]           # Last N trades
    python journal_cli.py stats [period]     # Performance stats
    python journal_cli.py note <id> "text"   # Add note to trade
    python journal_cli.py export [file.csv]  # Export CSV
"""

import os
import sys
import csv
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from sqlalchemy import desc

from journal.models import get_engine, get_session_factory, Trade, TradeNote

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = get_engine(DATABASE_URL)
SessionFactory = get_session_factory(engine)


def print_header(title: str):
    print(f"\n  ─── {title} {'─' * (50 - len(title))}\n")


def print_trade_table(trades: list[Trade]):
    if not trades:
        print("  No trades found.\n")
        return

    print(
        f"  {'ID':<6} {'Symbol':<10} {'Side':<5} {'Lots':<7} {'Entry':<11} {'Exit':<11} {'Net P&L':<10} {'Session':<10} {'Status':<8} {'Time'}"
    )
    print(
        f"  {'─' * 6} {'─' * 10} {'─' * 5} {'─' * 7} {'─' * 11} {'─' * 11} {'─' * 10} {'─' * 10} {'─' * 8} {'─' * 20}"
    )

    for t in trades:
        entry = f"{t.entry_price:.5f}" if t.entry_price else "—"
        exit_p = f"{t.exit_price:.5f}" if t.exit_price else "—"
        pnl = f"{t.net_profit:+.2f}" if t.net_profit is not None else "—"
        lots = f"{t.lots:.2f}" if t.lots else "—"
        session = t.session or "—"
        entry_time = t.entry_time.strftime("%m-%d %H:%M") if t.entry_time else "—"

        if t.net_profit is not None:
            if t.net_profit > 0:
                pnl = f"\033[32m{pnl}\033[0m"
            elif t.net_profit < 0:
                pnl = f"\033[31m{pnl}\033[0m"

        print(
            f"  {t.id:<6} {t.symbol:<10} {t.side:<5} {lots:<7} {entry:<11} {exit_p:<11} {pnl:<21} {session:<10} {t.status:<8} {entry_time}"
        )
    print()


def print_stats(trades: list[Trade], label: str = "Period"):
    closed = [t for t in trades if t.status == "closed" and t.net_profit is not None]

    if not closed:
        print(f"  No closed trades for {label}.\n")
        return

    winners = [t for t in closed if t.net_profit > 0]
    losers = [t for t in closed if t.net_profit < 0]
    breakeven = [t for t in closed if t.net_profit == 0]

    total_pnl = sum(t.net_profit for t in closed)
    avg_win = sum(t.net_profit for t in winners) / len(winners) if winners else 0
    avg_loss = sum(t.net_profit for t in losers) / len(losers) if losers else 0
    win_rate = len(winners) / len(closed) * 100

    best = max(closed, key=lambda t: t.net_profit)
    worst = min(closed, key=lambda t: t.net_profit)

    gross_wins = sum(t.net_profit for t in winners)
    gross_losses = abs(sum(t.net_profit for t in losers))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    by_session = {}
    for t in closed:
        s = t.session or "unknown"
        if s not in by_session:
            by_session[s] = {"count": 0, "pnl": 0}
        by_session[s]["count"] += 1
        by_session[s]["pnl"] += t.net_profit

    by_symbol = {}
    for t in closed:
        if t.symbol not in by_symbol:
            by_symbol[t.symbol] = {"count": 0, "pnl": 0, "wins": 0}
        by_symbol[t.symbol]["count"] += 1
        by_symbol[t.symbol]["pnl"] += t.net_profit
        if t.net_profit > 0:
            by_symbol[t.symbol]["wins"] += 1

    print_header(f"Stats — {label}")

    print(f"  Total trades:    {len(closed)}")
    print(f"  Winners:         {len(winners)}  ({win_rate:.1f}%)")
    print(f"  Losers:          {len(losers)}")
    print(f"  Breakeven:       {len(breakeven)}")
    print()
    pnl_color = "\033[32m" if total_pnl >= 0 else "\033[31m"
    print(f"  Net P&L:         {pnl_color}{total_pnl:+.2f}\033[0m")
    print(f"  Avg winner:      \033[32m{avg_win:+.2f}\033[0m")
    print(f"  Avg loser:       \033[31m{avg_loss:+.2f}\033[0m")
    print(f"  Best trade:      {best.symbol} {best.net_profit:+.2f}")
    print(f"  Worst trade:     {worst.symbol} {worst.net_profit:+.2f}")
    print(f"  Profit factor:   {profit_factor:.2f}")
    print()
    print(f"  By session:")
    for session, data in sorted(by_session.items()):
        c = "\033[32m" if data["pnl"] >= 0 else "\033[31m"
        print(
            f"    {session:<12} {data['count']:>3} trades  {c}{data['pnl']:>+10.2f}\033[0m"
        )
    print()
    print(f"  By symbol:")
    for sym, data in sorted(by_symbol.items(), key=lambda x: x[1]["pnl"], reverse=True):
        c = "\033[32m" if data["pnl"] >= 0 else "\033[31m"
        wr = (data["wins"] / data["count"] * 100) if data["count"] > 0 else 0
        print(
            f"    {sym:<10} {data['count']:>3} trades  {c}{data['pnl']:>+10.2f}\033[0m  ({wr:.0f}% WR)"
        )
    print()


# ─── Commands ────────────────────────────────────────────────


def cmd_today():
    db = SessionFactory()
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    trades = (
        db.query(Trade)
        .filter(Trade.entry_time >= today)
        .order_by(Trade.entry_time)
        .all()
    )
    print_header("Today's trades")
    print_trade_table(trades)
    db.close()


def cmd_week():
    db = SessionFactory()
    since = datetime.now(timezone.utc) - timedelta(days=7)
    trades = (
        db.query(Trade)
        .filter(Trade.entry_time >= since)
        .order_by(Trade.entry_time)
        .all()
    )
    print_header("This week")
    print_trade_table(trades)
    db.close()


def cmd_month():
    db = SessionFactory()
    since = datetime.now(timezone.utc) - timedelta(days=30)
    trades = (
        db.query(Trade)
        .filter(Trade.entry_time >= since)
        .order_by(Trade.entry_time)
        .all()
    )
    print_header("This month")
    print_trade_table(trades)
    db.close()


def cmd_open():
    db = SessionFactory()
    trades = db.query(Trade).filter_by(status="open").order_by(Trade.entry_time).all()
    print_header("Open positions")
    print_trade_table(trades)
    db.close()


def cmd_last(n: int = 10):
    db = SessionFactory()
    trades = db.query(Trade).order_by(desc(Trade.entry_time)).limit(n).all()
    trades.reverse()
    print_header(f"Last {n} trades")
    print_trade_table(trades)
    db.close()


def cmd_stats(period: str = "all"):
    db = SessionFactory()
    periods = {
        "today": (
            datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ),
            "Today",
        ),
        "week": (datetime.now(timezone.utc) - timedelta(days=7), "This Week"),
        "month": (datetime.now(timezone.utc) - timedelta(days=30), "This Month"),
        "all": (datetime(2000, 1, 1, tzinfo=timezone.utc), "All Time"),
    }
    since, label = periods.get(period, periods["all"])
    trades = db.query(Trade).filter(Trade.entry_time >= since).all()
    print_stats(trades, label)
    db.close()


def cmd_note(trade_id: int, content: str):
    db = SessionFactory()
    trade = db.query(Trade).filter_by(id=trade_id).first()
    if not trade:
        print(f"  Trade #{trade_id} not found.\n")
        db.close()
        return
    note = TradeNote(trade_id=trade_id, content=content)
    db.add(note)
    db.commit()
    print(f"  Note added to trade #{trade_id} ({trade.symbol} {trade.side})\n")
    db.close()


def cmd_export(filename: str = "trades_export.csv"):
    db = SessionFactory()
    trades = db.query(Trade).order_by(Trade.entry_time).all()
    if not trades:
        print("  No trades to export.\n")
        db.close()
        return

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "id",
                "symbol",
                "side",
                "lots",
                "entry_price",
                "exit_price",
                "entry_time",
                "exit_time",
                "stop_loss",
                "take_profit",
                "gross_profit",
                "commission",
                "swap",
                "net_profit",
                "session",
                "status",
            ]
        )
        for t in trades:
            writer.writerow(
                [
                    t.id,
                    t.symbol,
                    t.side,
                    t.lots,
                    t.entry_price,
                    t.exit_price,
                    t.entry_time,
                    t.exit_time,
                    t.stop_loss,
                    t.take_profit,
                    t.gross_profit,
                    t.commission,
                    t.swap,
                    t.net_profit,
                    t.session,
                    t.status,
                ]
            )

    print(f"  Exported {len(trades)} trades to {filename}\n")
    db.close()


USAGE = """
  cTrader Trading Journal CLI

  Commands:
    today              Today's trades
    week               This week
    month              This month
    open               Open positions
    last [n]           Last N trades (default 10)
    stats [period]     Stats: today / week / month / all
    note <id> "text"   Add note to a trade
    export [file.csv]  Export to CSV
"""


def main():
    args = sys.argv[1:]
    if not args:
        print(USAGE)
        return

    cmd = args[0].lower()
    commands = {
        "today": cmd_today,
        "week": cmd_week,
        "month": cmd_month,
        "open": cmd_open,
    }

    if cmd in commands:
        commands[cmd]()
    elif cmd == "last":
        cmd_last(int(args[1]) if len(args) > 1 else 10)
    elif cmd == "stats":
        cmd_stats(args[1] if len(args) > 1 else "all")
    elif cmd == "note":
        if len(args) < 3:
            print('  Usage: journal note <trade_id> "your note"')
            return
        cmd_note(int(args[1]), " ".join(args[2:]))
    elif cmd == "export":
        cmd_export(args[1] if len(args) > 1 else "trades_export.csv")
    else:
        print(f"  Unknown command: {cmd}")
        print(USAGE)


if __name__ == "__main__":
    main()
