#!/usr/bin/env python3
"""
import_positions.py — Import current positions from a Schwab positions CSV into Supabase.

The positions CSV is downloaded from Schwab → Accounts → Positions → Export.
It is the ground truth for what you currently own.

Usage:
    python3 scripts/import_positions.py path/to/CASH-Positions-YYYY-MM-DD.csv

What it does:
  1. Parses the Schwab positions CSV (skips cash rows and totals rows)
  2. Shows you the positions it found
  3. Asks confirmation before uploading
  4. Clears the existing `positions` table and replaces with fresh data
"""

import csv
import json
import re
import sys
import urllib.error
import urllib.request
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

def parse_money(s: str) -> float | None:
    s = str(s).replace('$', '').replace(',', '').replace('%', '').strip()
    try:
        return float(s) if s and s != '--' else None
    except ValueError:
        return None


def load_positions_csv(path: str) -> list[dict]:
    """
    Parse a Schwab positions CSV.
    Format:
      Row 0:  Account header (skip)
      Row 1:  blank (skip)
      Row 2:  Column headers
      Row 3+: Data rows (equity positions + cash row + totals row)
    """
    with open(path, newline='', encoding='utf-8-sig') as f:
        raw = f.read()

    lines = raw.splitlines()

    # Find the header row (contains "Symbol")
    header_idx = None
    for i, line in enumerate(lines):
        if 'Symbol' in line and 'Description' in line:
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Could not find header row with 'Symbol' column")

    reader = csv.DictReader(lines[header_idx:])
    positions = []

    for row in reader:
        symbol = row.get('Symbol', '').strip().strip('"')
        qty_raw = row.get('Qty (Quantity)', row.get('Qty', '')).strip().strip('"')
        cost_share_raw = row.get('Cost/Share', '').strip().strip('"')
        cost_basis_raw = row.get('Cost Basis', '').strip().strip('"')
        asset_type = row.get('Asset Type', '').strip().strip('"')

        # Skip non-equity rows
        if not symbol or symbol in ('--', '') :
            continue
        if 'cash' in symbol.lower() or 'cash' in asset_type.lower():
            continue
        if symbol.lower() in ('positions total',):
            continue
        if not re.match(r'^[A-Z]{1,5}$', symbol.upper()):
            continue  # skip anything that doesn't look like a ticker

        qty = parse_money(qty_raw)
        avg_cost = parse_money(cost_share_raw)
        total_cost = parse_money(cost_basis_raw)

        if qty is None or qty <= 0:
            continue

        positions.append({
            'ticker':     symbol.upper(),
            'shares':     int(qty),
            'avg_cost':   round(avg_cost, 4) if avg_cost else None,
            'total_cost': round(total_cost, 2) if total_cost else None,
        })

    return positions


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


def supabase_clear(table: str, token: str) -> None:
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


def supabase_insert(table: str, records: list[dict], token: str) -> None:
    url     = f"{SUPABASE_URL}/rest/v1/{table}"
    payload = json.dumps(records).encode()
    req     = urllib.request.Request(url, data=payload, method='POST', headers={
        'apikey':        SUPABASE_ANON_KEY,
        'Authorization': f'Bearer {token}',
        'Content-Type':  'application/json',
        'Prefer':        'return=minimal',
    })
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"  ✅ {table}: inserted {len(records)} records (status {resp.status})")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ❌ {table}: {e.code} — {body[:300]}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/import_positions.py path/to/CASH-Positions.csv")
        sys.exit(1)

    csv_path = sys.argv[1]
    if not Path(csv_path).exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)

    print(f"\n📂 Loading: {csv_path}")
    positions = load_positions_csv(csv_path)

    print(f"\n{'=' * 55}")
    print("📊  CURRENT POSITIONS FOUND")
    print('=' * 55)

    if not positions:
        print("  No equity positions found in this file.")
        sys.exit(0)

    for p in positions:
        lots = p['shares'] // 100
        lot_note = (f"→ {lots} covered call contract{'s' if lots != 1 else ''} possible"
                    if lots >= 1 else "→ < 100 shares")
        cost_str = f"  total ${p['total_cost']:,.2f}" if p['total_cost'] else ''
        print(f"  {p['ticker']:6s}  {p['shares']:5d} shares  "
              f"avg cost ${p['avg_cost']:.2f}{cost_str}   {lot_note}")

    print(f"\n  Total positions: {len(positions)}")

    # ── Write owned tickers file for scanner ──────────────────────────────
    owned_path = Path("data/owned_tickers.txt")
    owned_path.parent.mkdir(parents=True, exist_ok=True)
    owned_path.write_text("\n".join(p['ticker'] for p in positions) + "\n", encoding="utf-8")
    print(f"\n📝 Wrote {owned_path} ({len(positions)} tickers) — commit this file so the scanner uses it.")

    # ── Upload ─────────────────────────────────────────────────────────────
    print(f"\n{'=' * 55}")
    confirm = input("Upload to Supabase? (y/n): ").strip().lower()
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

    print("⬆️  Replacing positions in Supabase…")
    supabase_clear('positions', token)
    supabase_insert('positions', positions, token)
    print("\n🎉 Done! Refresh your dashboard to see updated positions.")


if __name__ == '__main__':
    main()
