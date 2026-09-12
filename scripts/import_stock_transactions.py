#!/usr/bin/env python3
"""
import_stock_transactions.py — Backfill/append the stock_transactions ledger
from a Schwab transaction history export (CSV or JSON).

Unlike positions.py's 90-day cutoff (which only tracks the *current* snapshot),
this ledger is meant to hold the FULL history so that Cost/P&L for a covered
call that got exercised can be computed from the actual cost basis at the time
those shares were sold, not today's blended average.

Run the SQL in scripts/create_stock_transactions_table.sql once first (in the
Supabase SQL Editor) to create the table.

Usage:
    python3 scripts/import_stock_transactions.py path/to/Transactions.csv
    python3 scripts/import_stock_transactions.py path/to/Transactions.json

Only plain stock Buy/Sell rows are kept -- options (Sell to Open, Buy to
Close, Expired, Assigned), cash transfers, dividends, interest, and share
transfers/journals are skipped. A transfer/journal means Schwab doesn't show
a purchase price for those shares, so they can't contribute a cost basis
here; if a ticker's history includes those, its earliest-known cost basis
will only be as accurate as the Buy rows Schwab actually has on file.

Re-running this (e.g. with a newer export) is safe -- rows upsert on
(ticker, transaction_date, action, shares, price), so nothing gets
duplicated.
"""

import csv
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from getpass import getpass
from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL      = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env")
    sys.exit(1)


# ─── Parsing ──────────────────────────────────────────────────────────────────

def parse_money(s) -> float | None:
    s = str(s).replace('$', '').replace(',', '').replace('%', '').strip()
    try:
        return float(s) if s and s != '--' else None
    except ValueError:
        return None


def parse_date(raw: str) -> str | None:
    """'09/10/2026' or '08/17/2026 as of 08/14/2026' -> '2026-09-10'.
    The leading date is the settlement/cash-impact date -- what we already
    use elsewhere (e.g. a trade's close_date) for assignment-driven sells."""
    if not raw:
        return None
    first = raw.split(' as of ')[0].strip().strip('"')
    try:
        return datetime.strptime(first, '%m/%d/%Y').date().isoformat()
    except ValueError:
        return None


def extract_stock_rows(txns: list[dict]) -> list[dict]:
    rows = []
    for t in txns:
        action = (t.get('Action') or '').strip()
        if action not in ('Buy', 'Sell'):
            continue
        symbol = (t.get('Symbol') or '').strip().strip('"').upper()
        if not re.match(r'^[A-Z]{1,6}$', symbol):
            continue  # option legs carry the strike/expiration in this field

        date = parse_date(t.get('Date', ''))
        qty  = parse_money(t.get('Quantity'))
        price = parse_money(t.get('Price'))
        amount = parse_money(t.get('Amount'))
        if not date or not qty:
            continue

        rows.append({
            'ticker':           symbol,
            'transaction_date': date,
            'action':           action.lower(),
            'shares':           qty,
            'price':            price,
            'amount':           amount,
        })
    return merge_same_day_fills(rows)


def merge_same_day_fills(rows: list[dict]) -> list[dict]:
    """Schwab sometimes splits one order into several identical line items
    (same ticker/date/action/price, different lot/fill) -- e.g. three
    separate 500-share sells at the same price on the same day. Merge those
    by summing shares/amount: it doesn't change the weighted-average cost
    basis (the price is identical), and it's what lets the unique constraint
    (ticker, date, action, shares, price) upsert safely instead of colliding
    with itself."""
    merged: dict[tuple, dict] = {}
    for r in rows:
        key = (r['ticker'], r['transaction_date'], r['action'], r['price'])
        if key in merged:
            merged[key]['shares'] += r['shares']
            if merged[key]['amount'] is not None and r['amount'] is not None:
                merged[key]['amount'] += r['amount']
        else:
            merged[key] = dict(r)
    return list(merged.values())


def load_transactions(path: str) -> list[dict]:
    p = Path(path)
    if p.suffix.lower() == '.json':
        data = json.loads(p.read_text(encoding='utf-8-sig'))
        txns = data.get('BrokerageTransactions', data if isinstance(data, list) else [])
    else:
        with open(p, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            txns = list(reader)
    return extract_stock_rows(txns)


# ─── Supabase helpers ─────────────────────────────────────────────────────────

def supabase_login(email: str, password: str) -> str:
    url     = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
    payload = json.dumps({'email': email, 'password': password}).encode()
    req     = urllib.request.Request(url, data=payload, headers={
        'apikey':       SUPABASE_ANON_KEY,
        'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    if 'access_token' not in data:
        raise ValueError(f"Login failed: {data.get('error_description', data)}")
    return data['access_token']


def supabase_upsert(table: str, records: list[dict], token: str, on_conflict: str) -> None:
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    payload = json.dumps(records).encode()
    req = urllib.request.Request(url, data=payload, method='POST', headers={
        'apikey':        SUPABASE_ANON_KEY,
        'Authorization': f'Bearer {token}',
        'Content-Type':  'application/json',
        'Prefer':        'resolution=merge-duplicates,return=minimal',
    })
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"  ✅ {table}: upserted {len(records)} rows (status {resp.status})")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ❌ {table}: {e.code} — {body[:500]}")
        sys.exit(1)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/import_stock_transactions.py path/to/Transactions.csv|.json")
        sys.exit(1)

    path = sys.argv[1]
    if not Path(path).exists():
        print(f"File not found: {path}")
        sys.exit(1)

    print(f"\n📂 Loading: {path}")
    rows = load_transactions(path)

    if not rows:
        print("  No stock Buy/Sell rows found in this file.")
        sys.exit(0)

    by_ticker: dict[str, int] = {}
    for r in rows:
        by_ticker[r['ticker']] = by_ticker.get(r['ticker'], 0) + 1

    print(f"\n{'=' * 55}")
    print(f"📊  {len(rows)} STOCK BUY/SELL ROWS FOUND")
    print('=' * 55)
    for ticker, count in sorted(by_ticker.items()):
        print(f"  {ticker:6s}  {count} transaction(s)")

    print(f"\n{'=' * 55}")
    confirm = input("Upload to Supabase stock_transactions? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Skipped upload.")
        return

    print("\n🔐 Sign in with your Supabase dashboard credentials:")
    email    = input("   Email: ").strip()
    password = getpass("   Password: ")

    try:
        token = supabase_login(email, password)
        print("   ✅ Authenticated\n")
    except Exception as e:
        print(f"   ❌ Login failed: {e}")
        sys.exit(1)

    print("⬆️  Upserting into stock_transactions…")
    # Batch to stay well under any request-size limits.
    BATCH = 200
    for i in range(0, len(rows), BATCH):
        supabase_upsert('stock_transactions', rows[i:i+BATCH], token,
                         on_conflict='ticker,transaction_date,action,shares,price')

    print("\n🎉 Done!")


if __name__ == '__main__':
    main()
