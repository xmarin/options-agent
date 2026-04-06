import json
import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
PUBLISHED_DIR = ROOT / "published"
LATEST_REPORT = PUBLISHED_DIR / "covered_call_report_latest.csv"
OUTPUT_PATH = PUBLISHED_DIR / "live_overlay.json"

TRADIER_TOKEN = os.getenv("TRADIER_TOKEN")
TRADIER_BASE_URL = os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1").rstrip("/")


def safe_float(value):
    try:
        if pd.isna(value):
            return None
        text = str(value).replace("$", "").replace("%", "").replace(",", "").strip()
        if not text:
            return None
        return float(text)
    except Exception:
        return None


def option_symbol_from_row(row):
    """
    Builds OCC option symbol from row fields.

    Example:
    AMD 2026-04-10 230 C -> AMD260410C00230000
    """
    ticker = str(row["Ticker"]).strip().upper()
    expiration = pd.to_datetime(row["Expiration"]).strftime("%y%m%d")
    strike = safe_float(row["Strike"])
    if strike is None:
        return None

    strike_int = int(round(strike * 1000))
    strike_formatted = f"{strike_int:08d}"

    return f"{ticker}{expiration}C{strike_formatted}"


def fetch_tradier_quote(symbol):
    headers = {
        "Authorization": f"Bearer {TRADIER_TOKEN}",
        "Accept": "application/json",
    }
    url = f"{TRADIER_BASE_URL}/markets/quotes"
    resp = requests.get(url, headers=headers, params={"symbols": symbol, "greeks": "false"}, timeout=20)
    resp.raise_for_status()
    payload = resp.json()

    quote = payload.get("quotes", {}).get("quote")
    if not quote:
        return None

    if isinstance(quote, list):
        quote = quote[0] if quote else None

    return quote


def build_record(row):
    scanned_bid = safe_float(row.get("Bid"))
    scanned_ask = safe_float(row.get("Ask"))
    scanned_mid = None
    if scanned_bid is not None and scanned_ask is not None:
        scanned_mid = round((scanned_bid + scanned_ask) / 2, 4)

    option_symbol = option_symbol_from_row(row)
    result = {
        "ticker": str(row["Ticker"]).strip().upper(),
        "expiration": str(row["Expiration"]).strip(),
        "strike": safe_float(row.get("Strike")),
        "option_symbol": option_symbol,
        "scanned_bid": scanned_bid,
        "scanned_ask": scanned_ask,
        "scanned_mid": scanned_mid,
        "live_bid": None,
        "live_ask": None,
        "live_mid": None,
        "premium_change_pct_from_bid": None,
        "stale": None,
        "error": None,
    }

    if not TRADIER_TOKEN:
        result["error"] = "Missing TRADIER_TOKEN"
        return result

    if not option_symbol:
        result["error"] = "Could not build option symbol"
        return result

    try:
        quote = fetch_tradier_quote(option_symbol)

        if not quote:
            result["error"] = "No quote returned"
            return result

        live_bid = safe_float(quote.get("bid"))
        live_ask = safe_float(quote.get("ask"))
        live_mid = None
        if live_bid is not None and live_ask is not None:
            live_mid = round((live_bid + live_ask) / 2, 4)

        result["live_bid"] = live_bid
        result["live_ask"] = live_ask
        result["live_mid"] = live_mid

        if scanned_bid is not None and live_bid is not None and scanned_bid != 0:
            pct_change = (live_bid - scanned_bid) / scanned_bid
            result["premium_change_pct_from_bid"] = round(pct_change, 4)
            result["stale"] = abs(pct_change) > 0.10

    except Exception as e:
        result["error"] = str(e)

    return result


def main():
    if not LATEST_REPORT.exists():
        raise FileNotFoundError(f"Missing latest report: {LATEST_REPORT}")

    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(LATEST_REPORT)
    df.columns = [str(c).strip().replace("\n", " ") for c in df.columns]

    top10 = df.head(10).copy()
    records = [build_record(row) for _, row in top10.iterrows()]

    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "source_report": LATEST_REPORT.name,
        "records": records,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved live overlay JSON: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()