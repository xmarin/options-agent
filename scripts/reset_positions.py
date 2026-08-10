#!/usr/bin/env python3
"""
One-time script to reset the positions table to match what Schwab shows right now.
Edit CORRECT_POSITIONS below if anything has changed, then run:
    python3 scripts/reset_positions.py
"""
import json, os, sys, urllib.request, urllib.error
from dotenv import load_dotenv
from getpass import getpass

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# ── Edit this to match your current Schwab positions ──────────────────────────
CORRECT_POSITIONS = [
    {"ticker": "INTC", "shares": 50,  "avg_cost": 90.55,  "total_cost": 4527.50},
    {"ticker": "HIMS", "shares": 200, "avg_cost": 39.46,  "total_cost": 7892.00},
    {"ticker": "NFLX", "shares": 100, "avg_cost": 94.865, "total_cost": 9486.50},
]
# ─────────────────────────────────────────────────────────────────────────────

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_ANON_KEY must be in .env")
    sys.exit(1)

print("Current positions to set:")
for p in CORRECT_POSITIONS:
    lots = p["shares"] // 100
    print(f"  {p['ticker']:6s}  {p['shares']:4d} shares  avg ${p['avg_cost']:.2f}  "
          f"({lots} contract{'s' if lots != 1 else ''} available)")

print()
confirm = input("Continue? (y/n): ").strip().lower()
if confirm != "y":
    sys.exit(0)

print("\n🔐 Sign in with your Supabase credentials:")
email    = input("   Email: ").strip()
password = getpass("   Password: ")

# Auth
auth_url = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
payload  = json.dumps({"email": email, "password": password}).encode()
req = urllib.request.Request(auth_url, data=payload, headers={
    "apikey": SUPABASE_KEY, "Content-Type": "application/json"
})
try:
    with urllib.request.urlopen(req) as resp:
        token = json.loads(resp.read())["access_token"]
    print("   ✅ Authenticated\n")
except Exception as e:
    print(f"   ❌ Login failed: {e}")
    sys.exit(1)

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}

# Delete all existing positions
del_url = f"{SUPABASE_URL}/rest/v1/positions?id=neq.00000000-0000-0000-0000-000000000000"
req = urllib.request.Request(del_url, method="DELETE", headers=headers)
try:
    with urllib.request.urlopen(req) as resp:
        print(f"🗑️  Cleared existing positions (status {resp.status})")
except urllib.error.HTTPError as e:
    print(f"⚠️  Could not clear positions: {e.code} {e.read().decode()}")
    sys.exit(1)

# Insert correct positions
ins_url = f"{SUPABASE_URL}/rest/v1/positions"
req = urllib.request.Request(
    ins_url,
    data=json.dumps(CORRECT_POSITIONS).encode(),
    method="POST",
    headers={**headers, "Prefer": "return=minimal"},
)
try:
    with urllib.request.urlopen(req) as resp:
        print(f"✅ Inserted {len(CORRECT_POSITIONS)} positions (status {resp.status})")
except urllib.error.HTTPError as e:
    print(f"❌ Insert failed: {e.code} {e.read().decode()}")
    sys.exit(1)

# Update owned_tickers.txt for the scanner
from pathlib import Path
tickers = [p["ticker"] for p in CORRECT_POSITIONS]
Path("data/owned_tickers.txt").write_text("\n".join(tickers) + "\n")
print(f"📝 Updated data/owned_tickers.txt: {', '.join(tickers)}")
print("\n🎉 Done! Refresh your dashboard to see corrected positions.")
