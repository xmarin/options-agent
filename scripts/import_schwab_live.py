#!/usr/bin/env python3
"""
import_schwab_live.py — Pull live positions from the Schwab API into Supabase.

Replaces the manual CSV export workflow. Uses the official Schwab Trader API
via the `schwab-py` library.

FIRST-TIME SETUP
────────────────
1. Go to https://developer.schwab.com and sign in with your Schwab account.
2. Create an app → set Callback URL to exactly: https://127.0.0.1:8182
3. Wait for status to change from "Approved - Pending" to "Ready for Use" (a few days).
4. Copy your App Key and App Secret into .env:
       SCHWAB_APP_KEY=xxxx
       SCHWAB_APP_SECRET=xxxx
5. Run this script: python3 scripts/import_schwab_live.py
   → A browser window opens → log in with your Schwab credentials → done.
6. The token is saved to data/schwab_token.json. Subsequent runs reuse it.

TOKEN EXPIRY
────────────
Schwab tokens expire after 7 days. Re-run the script to get a fresh browser
login when you see an "invalid_client: refresh token invalid" error.

Usage:
    python3 scripts/import_schwab_live.py [--dry-run]

    --dry-run   Print positions without uploading to Supabase.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from getpass import getpass
from pathlib import Path

import httpx
from dotenv import load_dotenv
import schwab
from schwab.auth import easy_client, client_from_token_file
from schwab.client import Client

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

SCHWAB_APP_KEY    = os.getenv("SCHWAB_APP_KEY", "").strip()
SCHWAB_APP_SECRET = os.getenv("SCHWAB_APP_SECRET", "").strip()
SCHWAB_CALLBACK   = os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
TOKEN_PATH        = Path("data/schwab_token.json")

SUPABASE_URL      = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

DRY_RUN = "--dry-run" in sys.argv


# ── Schwab auth ───────────────────────────────────────────────────────────────

def get_client() -> Client:
    """Return an authenticated schwab-py client, launching browser login if needed."""
    if not SCHWAB_APP_KEY or not SCHWAB_APP_SECRET:
        print("ERROR: SCHWAB_APP_KEY and SCHWAB_APP_SECRET must be set in .env")
        print("  See: https://developer.schwab.com → create an app → copy the keys")
        sys.exit(1)

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("🔑 Authenticating with Schwab API…")
    if TOKEN_PATH.exists():
        print(f"  Found existing token at {TOKEN_PATH} — reusing (refreshes automatically).")
    else:
        print("  No token found — a browser window will open for you to log in.")
        print(f"  Callback URL: {SCHWAB_CALLBACK}")
        print("  ⚠️  Your browser may warn about a self-signed certificate — that is safe to ignore.\n")

    try:
        c = easy_client(
            api_key=SCHWAB_APP_KEY,
            app_secret=SCHWAB_APP_SECRET,
            callback_url=SCHWAB_CALLBACK,
            token_path=str(TOKEN_PATH),
        )
        print("  ✅ Authenticated\n")
        return c
    except Exception as e:
        print(f"\n  ❌ Authentication failed: {e}")
        print("\n  If you see 'refresh token invalid', delete the token and re-run:")
        print(f"     rm {TOKEN_PATH}")
        sys.exit(1)


# ── Position parsing ──────────────────────────────────────────────────────────

def fetch_positions(c: Client) -> list[dict]:
    """Fetch all equity positions across all Schwab accounts."""
    resp = c.get_accounts(fields=[Client.Account.Fields.POSITIONS])

    if resp.status_code != httpx.codes.OK:
        print(f"❌ Schwab API error {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)

    accounts = resp.json()
    positions: list[dict] = []

    for account in accounts:
        acct = account.get("securitiesAccount", {})
        acct_number = acct.get("accountNumber", "unknown")
        raw_positions = acct.get("positions", [])

        for pos in raw_positions:
            instrument = pos.get("instrument", {})
            asset_type = instrument.get("assetType", "")

            # Only equity (stock) positions — skip options, mutual funds, cash
            if asset_type != "EQUITY":
                continue

            symbol = instrument.get("symbol", "").strip().upper()

            # Skip anything that doesn't look like a clean ticker
            if not symbol or not re.match(r"^[A-Z]{1,5}$", symbol):
                continue

            long_qty = float(pos.get("longQuantity", 0))
            if long_qty <= 0:
                continue

            avg_price  = pos.get("averagePrice")
            mkt_value  = pos.get("marketValue")
            total_cost = round(avg_price * long_qty, 2) if avg_price else None

            positions.append({
                "ticker":      symbol,
                "shares":      int(long_qty),
                "avg_cost":    round(avg_price, 4) if avg_price else None,
                "total_cost":  total_cost,
                "account":     acct_number[-4:],   # last 4 digits only
            })

    # Sort alphabetically for consistent display
    positions.sort(key=lambda p: p["ticker"])
    return positions


# ── Supabase helpers ──────────────────────────────────────────────────────────

def supabase_login(email: str, password: str) -> str:
    url     = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
    payload = json.dumps({"email": email, "password": password}).encode()
    req     = urllib.request.Request(url, data=payload, headers={
        "apikey":       SUPABASE_ANON_KEY,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    if "access_token" not in data:
        raise ValueError(f"Login failed: {data.get('error_description', data)}")
    return data["access_token"]


def supabase_clear(table: str, token: str) -> None:
    url = f"{SUPABASE_URL}/rest/v1/{table}?id=neq.00000000-0000-0000-0000-000000000000"
    req = urllib.request.Request(url, method="DELETE", headers={
        "apikey":        SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"  🗑️  Cleared {table} (status {resp.status})")
    except urllib.error.HTTPError as e:
        print(f"  ⚠️  Could not clear {table}: {e.code} {e.read().decode()}")


def supabase_insert(table: str, records: list[dict], token: str) -> None:
    # Strip keys not in the Supabase schema
    schema_records = [
        {k: v for k, v in r.items() if k in ("ticker", "shares", "avg_cost", "total_cost")}
        for r in records
    ]
    url     = f"{SUPABASE_URL}/rest/v1/{table}"
    payload = json.dumps(schema_records).encode()
    req     = urllib.request.Request(url, data=payload, method="POST", headers={
        "apikey":        SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"  ✅ {table}: inserted {len(schema_records)} records (status {resp.status})")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ❌ {table}: {e.code} — {body[:300]}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n🚀 Schwab Live Position Importer")
    print("=" * 50)

    c = get_client()

    print("📡 Fetching positions from Schwab API…")
    positions = fetch_positions(c)

    print(f"\n{'=' * 55}")
    print("📊  CURRENT EQUITY POSITIONS")
    print("=" * 55)

    if not positions:
        print("  No equity positions found in your Schwab account(s).")
        sys.exit(0)

    for p in positions:
        lots      = p["shares"] // 100
        lot_note  = (f"→ {lots} covered call contract{'s' if lots != 1 else ''} possible"
                     if lots >= 1 else "→ < 100 shares, no covered calls yet")
        cost_str  = f"  total ${p['total_cost']:,.2f}" if p["total_cost"] else ""
        acct_str  = f"  [acct …{p['account']}]" if p.get("account") else ""
        avg_str   = f"  avg ${p['avg_cost']:.2f}" if p["avg_cost"] else ""
        print(f"  {p['ticker']:6s}  {p['shares']:5d} shares{avg_str}{cost_str}   {lot_note}{acct_str}")

    print(f"\n  Total positions: {len(positions)}")

    # ── Write owned tickers file for scanner ──────────────────────────────────
    owned_path = Path("data/owned_tickers.txt")
    owned_path.parent.mkdir(parents=True, exist_ok=True)
    owned_path.write_text("\n".join(p["ticker"] for p in positions) + "\n", encoding="utf-8")
    print(f"\n📝 Wrote {owned_path} ({len(positions)} tickers)")
    print("   Commit this file so the scanner uses it on the next run.")

    if DRY_RUN:
        print("\n[dry-run] Skipping Supabase upload.")
        return

    # ── Upload to Supabase ────────────────────────────────────────────────────
    print(f"\n{'=' * 55}")
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        print("⚠️  SUPABASE_URL / SUPABASE_ANON_KEY not set — skipping upload.")
        return

    confirm = input("Upload to Supabase? (y/n): ").strip().lower()
    if confirm != "y":
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
    supabase_clear("positions", token)
    supabase_insert("positions", positions, token)
    print("\n🎉 Done! Refresh your dashboard to see live positions.")


if __name__ == "__main__":
    main()
