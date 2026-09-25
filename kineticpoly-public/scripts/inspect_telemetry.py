#!/usr/bin/env python3
"""
Enhanced Telemetry Inspector – deep analysis of telemetry.db
Usage:
    python scripts/inspect_telemetry.py                   # Full report
    python scripts/inspect_telemetry.py --top 20         # Top 20 tokens
    python scripts/inspect_telemetry.py --show-trades    # Show recent trades
    python scripts/inspect_telemetry.py --export-csv report.csv
    python scripts/inspect_telemetry.py --verbose        # More details
"""
import sqlite3
import sys
import argparse
import csv
from collections import defaultdict
from datetime import datetime

DB_PATH = "telemetry.db"


def split_winner_loser_tokens(rows):
    """Return disjoint positive and negative token P&L buckets."""
    winners = []
    losers = []
    for token_id, total_pnl in rows:
        token_id = safe_str(token_id)
        total_pnl = safe_float(total_pnl)
        if total_pnl > 0:
            winners.append((token_id, total_pnl))
        elif total_pnl < 0:
            losers.append((token_id, total_pnl))
    return winners, losers


def safe_float(val, default=0.0):
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, bytes):
        try:
            return float(val.decode('utf-8', errors='ignore').strip())
        except (ValueError, UnicodeDecodeError):
            return default
    if isinstance(val, str):
        try:
            return float(val.strip())
        except ValueError:
            return default
    return default

def safe_str(val):
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode('utf-8', errors='ignore')
    return str(val)

def print_section(title):
    print("\n" + "=" * 80)
    print(f" {title}")
    print("=" * 80)

def main():
    parser = argparse.ArgumentParser(description="Enhanced telemetry inspector")
    parser.add_argument("--top", type=int, default=10, help="Number of top/bottom tokens to show")
    parser.add_argument("--show-trades", action="store_true", help="Show recent trades")
    parser.add_argument("--export-csv", type=str, help="Export detailed trade data to CSV")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ----------------------------------------------------------------------
    # 1. Summary stats
    # ----------------------------------------------------------------------
    print_section("📊 OVERALL PERFORMANCE")

    c.execute("""
        SELECT
            COUNT(*) AS total_trades,
            SUM(pnl) AS total_pnl,
            AVG(pnl) AS avg_pnl,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 1.0 / SUM(CASE WHEN ABS(pnl) > 1e-6 THEN 1 ELSE 0 END) AS win_rate,
            MAX(pnl) AS max_win,
            MIN(pnl) AS max_loss,
            SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) AS gross_profit,
            SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END) AS gross_loss
        FROM trades
    """)
    row = c.fetchone()
    if row:
        (total_trades, total_pnl, avg_pnl, win_rate, max_win, max_loss,
         gross_profit, gross_loss) = row
        total_trades = int(total_trades) if total_trades else 0
        total_pnl = safe_float(total_pnl)
        avg_pnl = safe_float(avg_pnl)
        win_rate = safe_float(win_rate, 0.0)
        max_win = safe_float(max_win)
        max_loss = safe_float(max_loss)
        gross_profit = safe_float(gross_profit)
        gross_loss = safe_float(gross_loss)

        print(f"Total Trades:         {total_trades}")
        print(f"Total P&L:            ${total_pnl:,.2f}")
        print(f"Avg P&L per trade:    ${avg_pnl:,.2f}")
        print(f"Win Rate (Closed):    {win_rate*100:.1f}%")
        print(f"Max Win:              ${max_win:,.2f}")
        print(f"Max Loss:             ${max_loss:,.2f}")
        print(f"Gross Profit:         ${gross_profit:,.2f}")
        print(f"Gross Loss:           ${gross_loss:,.2f}")
        profit_factor = gross_profit / abs(gross_loss) if gross_loss != 0 else 0.0
        print(f"Profit Factor:        {profit_factor:.2f}")

    # ----------------------------------------------------------------------
    # 2. Best / Worst Tokens
    # ----------------------------------------------------------------------
    print_section(f"🏆 TOP {args.top} TOKENS BY P&L")
    c.execute("""
        SELECT token_id, SUM(pnl) AS total_pnl, COUNT(*) AS trades,
               AVG(pnl) AS avg_pnl
        FROM trades
        GROUP BY token_id
        ORDER BY total_pnl DESC
    """)
    all_token_rows = c.fetchall()
    winners, losers = split_winner_loser_tokens(
        [(row[0], row[1]) for row in all_token_rows]
    )
    if not winners:
        print(f"No positive-P&L tokens found in telemetry.")
    else:
        for i, (token_id, total_pnl) in enumerate(winners[:args.top], 1):
            total_trades = next((int(r[2]) if r[2] else 0 for r in all_token_rows if safe_str(r[0]) == token_id), 0)
            avg_pnl = next((safe_float(r[3]) for r in all_token_rows if safe_str(r[0]) == token_id), 0.0)
            print(f"{i:2d}. {token_id[:12]}... : ${total_pnl:,.2f}  ({total_trades} trades, avg ${avg_pnl:,.2f})")

    print_section(f"📉 BOTTOM {args.top} TOKENS BY P&L")
    if not losers:
        print(f"No negative-P&L tokens found in telemetry.")
    else:
        for i, (token_id, total_pnl) in enumerate(losers[:args.top], 1):
            total_trades = next((int(r[2]) if r[2] else 0 for r in all_token_rows if safe_str(r[0]) == token_id), 0)
            avg_pnl = next((safe_float(r[3]) for r in all_token_rows if safe_str(r[0]) == token_id), 0.0)
            print(f"{i:2d}. {token_id[:12]}... : ${total_pnl:,.2f}  ({total_trades} trades, avg ${avg_pnl:,.2f})")

    # ----------------------------------------------------------------------
    # 3. Strategy Performance
    # ----------------------------------------------------------------------
    print_section("🧠 STRATEGY PERFORMANCE")
    c.execute("""
        SELECT strategy,
               SUM(pnl) AS total_pnl,
               AVG(pnl) AS avg_pnl,
               COUNT(*) AS trades,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 1.0 / SUM(CASE WHEN ABS(pnl) > 1e-6 THEN 1 ELSE 0 END) AS win_rate
        FROM trades
        GROUP BY strategy
        ORDER BY total_pnl DESC
    """)
    rows = c.fetchall()
    if rows:
        print(f"{'Strategy':<20} {'P&L':>12} {'Trades':>8} {'Win Rate':>10} {'Avg P&L':>12}")
        print("(Win rate shows closed trades only)")
        print("-" * 70)
        for row in rows:
            strategy = safe_str(row[0])[:20]
            total_pnl = safe_float(row[1])
            avg_pnl = safe_float(row[2])
            trades = int(row[3]) if row[3] else 0
            win_rate = safe_float(row[4], 0.0)
            print(f"{strategy:<20} ${total_pnl:>10,.2f} {trades:>8} {win_rate*100:>9.1f}% ${avg_pnl:>10,.2f}")

    # ----------------------------------------------------------------------
    # 4. Win / Loss Streaks
    # ----------------------------------------------------------------------
    print_section("📈 WIN/LOSS STREAKS")
    c.execute("SELECT pnl FROM trades ORDER BY timestamp ASC")
    rows = c.fetchall()
    pnls = [safe_float(r[0]) for r in rows]
    max_wins = max_losses = 0
    curr_wins = curr_losses = 0
    for p in pnls:
        if p > 0:
            curr_wins += 1
            curr_losses = 0
            max_wins = max(max_wins, curr_wins)
        elif p < 0:
            curr_losses += 1
            curr_wins = 0
            max_losses = max(max_losses, curr_losses)
        else:
            curr_wins = curr_losses = 0
    print(f"Max Consecutive Wins:  {max_wins}")
    print(f"Max Consecutive Losses: {max_losses}")

    # ----------------------------------------------------------------------
    # 5. Daily P&L
    # ----------------------------------------------------------------------
    print_section("📅 DAILY P&L (LAST 30 DAYS)")
    c.execute("""
        SELECT DATE(timestamp) AS day,
               SUM(pnl) AS daily_pnl,
               COUNT(*) AS trades,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 1.0 / SUM(CASE WHEN ABS(pnl) > 1e-6 THEN 1 ELSE 0 END) AS win_rate
        FROM trades
        GROUP BY day
        ORDER BY day DESC
        LIMIT 30
    """)
    rows = c.fetchall()
    if rows:
        print(f"{'Date':<12} {'P&L':>12} {'Trades':>8} {'Win Rate':>10}")
        print("(Win rate shows closed trades only)")
        print("-" * 50)
        for row in rows:
            day = safe_str(row[0])
            daily_pnl = safe_float(row[1])
            trades = int(row[2]) if row[2] else 0
            win_rate = safe_float(row[3], 0.0)
            print(f"{day:<12} ${daily_pnl:>10,.2f} {trades:>8} {win_rate*100:>9.1f}%")

    # ----------------------------------------------------------------------
    # 6. Trade Size Distribution (optional, if we have size)
    # ----------------------------------------------------------------------
    print_section("📊 TRADE SIZE DISTRIBUTION")
    c.execute("SELECT size FROM trades")
    rows = c.fetchall()
    sizes = [safe_float(r[0]) for r in rows if r[0] is not None]
    if sizes:
        bins = [0, 10, 50, 100, 500, 1000, 5000, 10000]
        counts = [0] * (len(bins) + 1)
        for s in sizes:
            for i, b in enumerate(bins):
                if s <= b:
                    counts[i] += 1
                    break
            else:
                counts[-1] += 1
        print(f"{'Size Range':<15} {'Trades':>10} {'%':>8}")
        print("-" * 40)
        total = len(sizes)
        for i, b in enumerate(bins):
            label = f"≤{b}" if i == 0 else f"{bins[i-1]}+ to {b}"
            pct = counts[i] / total * 100 if total > 0 else 0
            print(f"{label:<15} {counts[i]:>10} {pct:>7.1f}%")
        label = f">{bins[-1]}+"
        pct = counts[-1] / total * 100 if total > 0 else 0
        print(f"{label:<15} {counts[-1]:>10} {pct:>7.1f}%")

    # ----------------------------------------------------------------------
    # 7. Show Recent Trades (if requested)
    # ----------------------------------------------------------------------
    if args.show_trades:
        print_section("🔄 RECENT TRADES (LAST 20)")
        c.execute("""
            SELECT timestamp, token_id, side, size, price, pnl, strategy
            FROM trades
            ORDER BY timestamp DESC
            LIMIT 20
        """)
        rows = c.fetchall()
        print(f"{'Timestamp':<20} {'Token':<15} {'Side':<6} {'Size':>8} {'Price':>8} {'P&L':>10} {'Strategy':<15}")
        print("-" * 90)
        for row in rows:
            ts = safe_str(row[0])[:19]
            token = safe_str(row[1])[:12] + "..."
            side = safe_str(row[2])
            size = safe_float(row[3])
            price = safe_float(row[4])
            pnl = safe_float(row[5])
            strategy = safe_str(row[6])[:15]
            print(f"{ts:<20} {token:<15} {side:<6} {size:>8.2f} {price:>8.4f} {pnl:>10.2f} {strategy:<15}")

    # ----------------------------------------------------------------------
    # 8. Export to CSV (if requested)
    # ----------------------------------------------------------------------
    if args.export_csv:
        print_section(f"💾 Exporting trades to {args.export_csv}")
        c.execute("SELECT * FROM trades")
        rows = c.fetchall()
        # Get column names
        col_names = [desc[0] for desc in c.description]
        with open(args.export_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(col_names)
            for row in rows:
                # Convert bytes to str for CSV
                converted = []
                for val in row:
                    if isinstance(val, bytes):
                        converted.append(val.decode('utf-8', errors='ignore'))
                    else:
                        converted.append(val)
                writer.writerow(converted)
        print(f"✅ Exported {len(rows)} rows to {args.export_csv}")

    # ----------------------------------------------------------------------
    # 9. Database overview (tables and row counts)
    # ----------------------------------------------------------------------
    if args.verbose:
        print_section("📋 DATABASE TABLES")
        c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = c.fetchall()
        for table in tables:
            tbl = safe_str(table[0])
            c.execute(f"SELECT COUNT(*) FROM {tbl}")
            cnt = c.fetchone()[0]
            print(f"  {tbl}: {cnt} records")

    conn.close()

if __name__ == "__main__":
    main()