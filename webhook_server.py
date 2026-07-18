#!/usr/bin/env python3
"""
webhook_server.py — Receives GitHub push webhooks from the partner agent's repo
and immediately fetches their latest picks.

Deploy this on Render as a web service. Register its URL as a GitHub webhook
on the partner's repo (Settings → Webhooks).

Environment variables required:
    WEBHOOK_SECRET          Shared secret set in GitHub webhook config (any string you choose)
    PARTNER_RAW_BASE_URL    Base URL for partner's raw GitHub files, e.g.
                            https://raw.githubusercontent.com/danilo/etoro-agent/main/published
    PARTNER_OUTPUT_FILE     Local path to save the fetched picks, e.g.
                            published/danilo_picks_latest.json

Optional:
    PORT                    Port to listen on (default: 10000, Render's default)
"""

import hashlib
import hmac
import json
import os
import urllib.request
from datetime import date
from pathlib import Path

from flask import Flask, Response, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

WEBHOOK_SECRET       = os.getenv("WEBHOOK_SECRET", "").encode()
PARTNER_RAW_BASE_URL = os.getenv("PARTNER_RAW_BASE_URL", "").rstrip("/")
PARTNER_OUTPUT_FILE  = Path(os.getenv("PARTNER_OUTPUT_FILE", "published/danilo_picks_latest.json"))
PORT                 = int(os.getenv("PORT", "10000"))


# ── Signature verification ─────────────────────────────────────────────────────

def verify_signature(payload: bytes, sig_header: str) -> bool:
    """Verify GitHub's HMAC-SHA256 signature on the request body."""
    if not WEBHOOK_SECRET:
        print("WARNING: WEBHOOK_SECRET not set — skipping signature check (unsafe in production)")
        return True
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


# ── Pick fetching ──────────────────────────────────────────────────────────────

def fetch_partner_picks(week: str = None) -> dict | None:
    """
    Fetch partner's picks from their public GitHub raw URL.
    week: "MM-DD-YYYY" for a specific week, or None for latest.
    """
    if not PARTNER_RAW_BASE_URL:
        print("ERROR: PARTNER_RAW_BASE_URL not set")
        return None

    if week:
        url = f"{PARTNER_RAW_BASE_URL}/picks_{week}.json"
    else:
        url = f"{PARTNER_RAW_BASE_URL}/picks_latest.json"

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        print(f"  ✅ Fetched partner picks: {data.get('report_date')} — {len(data.get('picks', []))} picks")
        return data
    except Exception as e:
        print(f"  ❌ Failed to fetch partner picks from {url}: {e}")
        return None


def save_picks(data: dict) -> None:
    """Save the fetched picks to the configured output path."""
    PARTNER_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Save as latest
    PARTNER_OUTPUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  💾 Saved to {PARTNER_OUTPUT_FILE}")

    # Also save a dated copy alongside it
    report_date = data.get("report_date", date.today().isoformat())
    try:
        dt = date.fromisoformat(report_date)
        dated_name = f"picks_{dt.strftime('%m-%d-%Y')}.json"
    except ValueError:
        dated_name = f"picks_{report_date}.json"

    dated_path = PARTNER_OUTPUT_FILE.parent / dated_name
    dated_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  💾 Saved dated copy to {dated_path}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def push_touches_picks(payload: dict) -> bool:
    """Return True if any commit in the push modified published/picks_latest.json."""
    for commit in payload.get("commits", []):
        changed = commit.get("added", []) + commit.get("modified", [])
        if any("published/picks_latest.json" in f for f in changed):
            return True
    return False


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return Response("ok", status=200, mimetype="text/plain")


@app.route("/webhook/picks", methods=["POST"])
def webhook_picks():
    """
    Receives a GitHub push webhook from the partner's repo.
    Fires immediately when they push a new picks file.
    """
    raw_body = request.get_data()
    sig      = request.headers.get("X-Hub-Signature-256", "")

    if not verify_signature(raw_body, sig):
        print("⚠️  Webhook signature mismatch — rejected")
        return Response("Forbidden", status=403)

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return Response("Bad Request", status=400)

    event = request.headers.get("X-GitHub-Event", "")

    if event == "ping":
        print("🏓 Webhook ping received — connection verified")
        return Response("pong", status=200)

    if event != "push":
        # Ignore non-push events silently
        return Response("ignored", status=200)

    branch = payload.get("ref", "")
    if not branch.endswith("/main"):
        print(f"  Push to non-main branch ({branch}) — ignoring")
        return Response("ignored", status=200)

    if not push_touches_picks(payload):
        print("  Push did not touch published/picks_latest.json — ignoring")
        return Response("ignored", status=200)

    print(f"\n🔔 New picks detected from partner repo: {payload.get('repository', {}).get('full_name')}")
    data = fetch_partner_picks()
    if data:
        save_picks(data)
        return Response("ok", status=200)
    else:
        return Response("fetch failed", status=502)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"🚀 Webhook server starting on port {PORT}")
    print(f"   Partner raw base: {PARTNER_RAW_BASE_URL or '(not set)'}")
    print(f"   Output file:      {PARTNER_OUTPUT_FILE}")
    print(f"   Secret set:       {'yes' if WEBHOOK_SECRET else 'NO — set WEBHOOK_SECRET'}")
    app.run(host="0.0.0.0", port=PORT)
