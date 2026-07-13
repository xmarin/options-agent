#!/usr/bin/env python3
"""
import_schwab.py — Parse a Charles Schwab transaction export (CSV or JSON) and
import into Supabase:
  1. Covered call trades  →  `trades` table
  2. Current stock positions  →  `positions` table

Usage:
    python scripts/import_schwab.py path/to/schwab_transactions.csv
    python scripts/import_schwab.py path/to/schwab_transactions.json

Positions are seeded from data/positions_baseline.json (holdings as of the
start of the export window) so partial-history exports still reconstruct
current positions correctly. Positions are UPSERTED by ticker (existing rows
for tickers not in the export are left untouched).

Requirements:
    pip install python-dotenv  (already in requirements.txt)

Before running, make sure you have created the `positions` table in Supabase:
--------------------------------------------------------------------
create table public.positions (
  id          uuid        default gen_random_uuid() primary key,
  updated_at  timestamptz default now(),
  ticker      text        not null unique,
  shares      integer     not null default 0,
  avg_cost    numeric(10,4),
  total_cost  numeric(10,2)
);
alter table public.positions enable row level security;
create policy "Owner read positions"   on public.positions for select to authenticated using (true);
create policy "Owner insert positions" on public.positions for insert to authenticated with check (true);
create policy "Owner update positions" on public.positions for update to authenticated using (true);
create policy "Owner delete positions" on public.positions for delete to authenticated using (true);
--------------------------------------------------------------------
"""

import csv
import json
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, date
from getpass import getpass
from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL     = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env")
    sys.exit(1)


# ─── Parsing helpers ──────────────────────────────────────────────────────────

def parse_dates(date_str: str) -> tuple[date | None, date | None]:
    """Return (recorded_date, event_date).
    For 'MM/DD/YYYY as of MM/DD/YYYY' the event_date is the 2nd (actual trade date)."""
    matches = re.findall(r'\d{2}/\d{2}/\d{4}', date_str)
    if len(matches) == 2:
        return (datetime.strptime(matches[0], "%m/%d/%Y").date(),
                datetime.strptime(matches[1], "%m/%d/%Y").date())
    elif len(matches) == 1:
        d = datetime.strptime(matches[0], "%m/%d/%Y").date()
        return d, d
    return None, None


def is_option_symbol(symbol: str) -> bool:
    return bool(re.match(r'^\w+\s+\d{2}/\d{2}/\d{4}\s+[\d.]+\s+[CP]$', symbol.strip()))


def parse_option_symbol(symbol: str) -> dict | None:
    """Parse 'AMD 04/24/2026 292.50 C' → dict."""
    m = re.match(r'^(\w+)\s+(\d{2}/\d{2}/\d{4})\s+([\d.]+)\s+([CP])$', symbol.strip())
    if m:
        ticker, exp_str, strike_str, opt_type = m.groups()
        return {
            'ticker':      ticker.upper(),
            'expiration':  datetime.strptime(exp_str, "%m/%d/%Y").date(),
            'strike':      float(strike_str),
            'option_type': opt_type,
        }
    return None


def parse_money(s: str) -> float | None:
    s = str(s).replace('$', '').replace(',', '').strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def parse_qty(s: str) -> float:
    try:
        return float(str(s).replace(',', ''))
    except ValueError:
        return 0.0


def load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k.strip().strip('"'): str(v).strip().strip('"') for k, v in row.items()})
    return [r for r in rows if r.get('Date', '').strip()]


def load_json_export(path: str) -> list[dict]:
    """Load a Schwab JSON transactions export (BrokerageTransactions array)."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    txns = data.get('BrokerageTransactions', data if isinstance(data, list) else [])
    return [
        {k: str(v).strip() for k, v in row.items()}
        for row in txns
        if str(row.get('Date', '')).strip()
    ]


def load_transactions(path: str) -> list[dict]:
    if path.lower().endswith('.json'):
        return load_json_export(path)
    return load_csv(path)


def load_baseline() -> list[dict]:
    """Holdings as of the start of the export window (data/positions_baseline.json)."""
    baseline_path = Path(__file__).parent.parent / 'data' / 'positions_baseline.json'
    if not baseline_path.exists():
        return []
    try:
        data = json.loads(baseline_path.read_text(encoding='utf-8'))
        return data.get('positions', [])
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠️  Could not read positions baseline: {e}")
        return []


# ─── Covered call extraction ──────────────────────────────────────────────────

def extract_covered_calls(transactions: list[dict]) -> list[dict]:
    """
    Extract all covered call cycles (Sell to Open → Expired/Assigned).
    Returns a flat list of trade dicts ready for Supabase `trades` table.
    """

    def sort_key(r):
        _, ev = parse_dates(r.get('Date', ''))
        return ev or date(2000, 1, 1)

    sorted_txns = sorted(transactions, key=sort_key)

    # Track most recent buy price per ticker as a stock-price proxy
    recent_buy_price: dict[str, float] = {}

    open_calls: dict[str, dict] = {}   # key → open trade
    closed: list[dict] = []

    for row in sorted_txns:
        action  = row.get('Action', '').strip()
        symbol  = row.get('Symbol', '').strip()
        date_str = row.get('Date', '')
        txn_date, event_date = parse_dates(date_str)
        price   = parse_money(row.get('Price',  ''))
        qty_raw = row.get('Quantity', '').replace(',', '')
        qty     = int(float(qty_raw)) if qty_raw else 1
        amount  = parse_money(row.get('Amount', ''))

        # Track buy prices for stock-price context
        if action == 'Buy' and symbol and not is_option_symbol(symbol):
            if price:
                recent_buy_price[symbol.upper()] = price

        # ── Open: Sell to Open ────────────────────────────────────────────
        if action == 'Sell to Open':
            opt = parse_option_symbol(symbol)
            if not opt or opt['option_type'] != 'C':
                continue
            ticker    = opt['ticker']
            dte       = (opt['expiration'] - (event_date or txn_date)).days
            stock_px  = recent_buy_price.get(ticker)
            pct_otm   = round((opt['strike'] - stock_px) / stock_px, 4) if stock_px else None
            key       = f"{ticker}_{opt['expiration']}_{opt['strike']}"

            open_calls[key] = {
                'ticker':            ticker,
                'transaction_date':  str(event_date or txn_date),
                'stock_price':       stock_px,
                'contracts':         qty,
                'strike':            opt['strike'],
                'expiration':        str(opt['expiration']),
                'dte':               dte,
                'premium':           price,
                'total_premium':     abs(amount) if amount else None,
                'pct_otm':           pct_otm,
                'status':            'open',
                'notes':             'Imported from Schwab CSV',
                '_key':              key,
            }

        # ── Close: Expired worthless ──────────────────────────────────────
        elif action == 'Expired':
            opt = parse_option_symbol(symbol)
            if not opt:
                continue
            key = f"{opt['ticker']}_{opt['expiration']}_{opt['strike']}"
            if key in open_calls:
                t = open_calls.pop(key)
                t.update({
                    'status':         'expired_worthless',
                    'close_date':     str(event_date or txn_date),
                    'net_option_pnl': t.get('total_premium'),
                    'total_pnl':      t.get('total_premium'),
                })
                closed.append(t)

        # ── Close: Assigned (stock called away at strike) ─────────────────
        elif action == 'Assigned':
            opt = parse_option_symbol(symbol)
            if not opt:
                continue
            key = f"{opt['ticker']}_{opt['expiration']}_{opt['strike']}"
            if key in open_calls:
                t = open_calls.pop(key)
                ticker   = opt['ticker']
                prem     = t.get('total_premium') or 0
                avg_cost = recent_buy_price.get(ticker)
                underly  = round((opt['strike'] - avg_cost) * 100 * t['contracts'], 2) if avg_cost else None

                t.update({
                    'status':           'assigned',
                    'close_date':       str(event_date or txn_date),
                    'assignment_price': opt['strike'],
                    'net_option_pnl':   round(prem, 2),
                    'underlying_pnl':   underly,
                    'total_pnl':        round(prem + (underly or 0), 2),
                })
                closed.append(t)

    # Any still-open (shouldn't be any if closed properly)
    for t in open_calls.values():
        closed.append(t)

    return closed


# ─── Current positions ────────────────────────────────────────────────────────

def compute_positions(transactions: list[dict]) -> list[dict]:
    """
    Reconstruct current net stock positions from all Buy / Sell transactions.
    Skips option symbols, cash movements, and non-equity rows.
    """

    def sort_key(r):
        _, ev = parse_dates(r.get('Date', ''))
        return ev or date(2000, 1, 1)

    sorted_txns = sorted(transactions, key=sort_key)

    shares:     dict[str, float] = defaultdict(float)
    total_cost: dict[str, float] = defaultdict(float)

    # Seed pre-window holdings so partial exports reconstruct correctly
    for b in load_baseline():
        t = str(b.get('ticker', '')).upper()
        if not t:
            continue
        shares[t]     = float(b.get('shares', 0))
        total_cost[t] = float(b.get('shares', 0)) * float(b.get('avg_cost', 0))

    for row in sorted_txns:
        action = row.get('Action', '').strip()
        symbol = row.get('Symbol', '').strip().upper()
        qty    = parse_qty(row.get('Quantity', '0'))
        price  = parse_money(row.get('Price', '')) or 0.0

        # Skip blanks, option symbols, and non-buy/sell rows
        if not symbol or is_option_symbol(symbol):
            continue
        if action not in ('Buy', 'Sell'):
            continue

        if action == 'Buy':
            total_cost[symbol] += qty * price
            shares[symbol]     += qty

        elif action == 'Sell':
            if shares[symbol] > 0:
                ratio = min(qty / shares[symbol], 1.0)
                total_cost[symbol] = max(0.0, total_cost[symbol] * (1 - ratio))
                shares[symbol]     = max(0.0, shares[symbol] - qty)

    result = []
    for ticker, qty in shares.items():
        q = round(qty)
        if q > 0:
            avg = round(total_cost[ticker] / qty, 4) if qty > 0 else 0.0
            result.append({
                'ticker':     ticker,
                'shares':     q,
                'avg_cost':   avg,
                'total_cost': round(total_cost[ticker], 2),
            })

    return sorted(result, key=lambda x: x['ticker'])


# ─── Supabase helpers ─────────────────────────────────────────────────────────

def supabase_login(email: str, password: str) -> str:
    """Authenticate and return JWT access token."""
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


def supabase_clear(table: str, token: str) -> None:
    """Delete all rows from a table (authenticated)."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?id=neq.00000000-0000-0000-0000-000000000000"
    req = urllib.request.Request(url, method='DELETE', headers={
        'apikey':        SUPABASE_ANON_KEY,
        'Authorization': f'Bearer {token}',
        'Content-Type':  'application/json',
    })
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"  🗑️  Cleared {table} (status {resp.status})")
    except urllib.error.HTTPError as e:
        print(f"  ⚠️  Could not clear {table}: {e.code} {e.read().decode()}")


def supabase_insert(table: str, records: list[dict], token: str,
                    on_conflict: str | None = None) -> None:
    """Insert records into Supabase table. If on_conflict is given, upsert."""
    clean = [{k: v for k, v in r.items() if not k.startswith('_')} for r in records]
    url   = f"{SUPABASE_URL}/rest/v1/{table}"
    prefer = 'return=minimal'
    if on_conflict:
        url    += f"?on_conflict={on_conflict}"
        prefer += ',resolution=merge-duplicates'
    payload = json.dumps(clean).encode()
    req = urllib.request.Request(url, data=payload, method='POST', headers={
        'apikey':        SUPABASE_ANON_KEY,
        'Authorization': f'Bearer {token}',
        'Content-Type':  'application/json',
        'Prefer':        prefer,
    })
    try:
        with urllib.request.urlopen(req) as resp:
            verb = 'upserted' if on_conflict else 'inserted'
            print(f"  ✅ {table}: {verb} {len(clean)} records (status {resp.status})")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ❌ {table}: {e.code} — {body[:300]}")


def supabase_get_existing_trade_keys(token: str) -> set[str]:
    """Fetch (ticker, expiration, strike) keys of trades already in Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/trades?select=ticker,expiration,strike"
    req = urllib.request.Request(url, headers={
        'apikey':        SUPABASE_ANON_KEY,
        'Authorization': f'Bearer {token}',
    })
    try:
        with urllib.request.urlopen(req) as resp:
            rows = json.loads(resp.read())
        return {f"{r['ticker']}_{r['expiration']}_{float(r['strike'])}" for r in rows}
    except Exception as e:
        print(f"  ⚠️  Could not fetch existing trades ({e}); duplicates possible")
        return set()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/import_schwab.py path/to/schwab_transactions.{csv,json}")
        sys.exit(1)

    in_path = sys.argv[1]
    if not Path(in_path).exists():
        print(f"File not found: {in_path}")
        sys.exit(1)

    print(f"\n📂 Loading: {in_path}")
    txns = load_transactions(in_path)
    print(f"   {len(txns)} transactions loaded\n")

    # ── Covered calls ──────────────────────────────────────────────────
    calls = extract_covered_calls(txns)
    total_prem = sum(t.get('total_premium') or 0 for t in calls)
    total_pnl  = sum(t.get('total_pnl')     or 0 for t in calls)

    print("=" * 65)
    print("📋  COVERED CALL TRADES")
    print("=" * 65)
    for t in sorted(calls, key=lambda x: x['transaction_date']):
        status = t.get('status', '?')
        prem   = t.get('total_premium') or 0
        pnl    = t.get('total_pnl')    or prem
        icon   = {'expired_worthless': '✅', 'assigned': '📦', 'open': '🔵'}.get(status, '❓')
        print(f"  {icon} {t['transaction_date']}  {t['ticker']:6s} "
              f"${t['strike']:7.2f}C  exp {t['expiration']}  "
              f"Prem: ${prem:7.2f}  P&L: ${pnl:8.2f}  [{status}]")
    print(f"\n  Total premium collected : ${total_prem:.2f}")
    print(f"  Total P&L (incl. stock) : ${total_pnl:.2f}")
    print(f"  Trades                  : {len(calls)}")

    # ── Current positions ──────────────────────────────────────────────
    positions = compute_positions(txns)
    print(f"\n{'=' * 65}")
    print("📊  CURRENT STOCK POSITIONS")
    print("=" * 65)
    if not positions:
        print("  No open stock positions found.")
    for p in positions:
        lots     = p['shares'] // 100
        lot_note = (f"→ {lots} covered call contract{'s' if lots != 1 else ''} possible"
                    if lots >= 1 else "→ < 100 shares, cannot sell covered calls yet")
        print(f"  {p['ticker']:6s}  {p['shares']:5d} shares  "
              f"avg cost ${p['avg_cost']:.2f}  "
              f"total ${p['total_cost']:,.0f}   {lot_note}")

    # ── Upload ─────────────────────────────────────────────────────────
    print(f"\n{'=' * 65}")
    confirm = input("Upload to Supabase? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Skipped upload.")
        return

    print("\n🔐 Sign in with your dashboard credentials:")
    email    = input("   Email: ").strip()
    password = getpass("   Password: ")

    try:
        token = supabase_login(email, password)
        print("   ✅ Authenticated\n")
    except Exception as e:
        print(f"   ❌ Login failed: {e}")
        sys.exit(1)

    clear = input("Clear existing trades/positions before importing? (y/n): ").strip().lower()
    if clear == 'y':
        supabase_clear('trades', token)
        supabase_clear('positions', token)

    print("\n⬆️  Uploading...")

    # Skip trades already in Supabase (partial exports overlap previous imports)
    existing = supabase_get_existing_trade_keys(token) if clear != 'y' else set()
    new_calls = [t for t in calls if t.get('_key') not in existing]
    skipped = len(calls) - len(new_calls)
    if skipped:
        print(f"  ↩️  Skipping {skipped} trades already in Supabase")
    if new_calls:
        supabase_insert('trades', new_calls, token)

    # Upsert positions by ticker; rows for tickers not in this export are kept
    supabase_insert('positions', positions, token, on_conflict='ticker')
    print("\n🎉 Done! Refresh your dashboard to see the data.")


if __name__ == '__main__':
    main()
