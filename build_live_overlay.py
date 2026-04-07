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


# 🔥 IMPORTANT: Uses scanner-provided Option Symbol FIRST
def option_symbol_from_row(row):
    existing = str(row.get("Option Symbol", "")).strip().upper()
    if existing:
        return existing

    ticker = str(row["Ticker"]).strip().upper()
    expiration = pd.to_datetime(row["Expiration"]).strftime("%y%m%d")
    strike = safe_float(row["Strike"])
    if strike is None:
        return None

    strike_int = int(round(float(strike) * 1000))
    strike_formatted = f"{strike_int:08d}"

    return f"{ticker}{expiration}C{strike_formatted}"


def fetch_tradier_quotes(symbols):
    if not symbols:
        return {}

    headers = {
        "Authorization": f"Bearer {TRADIER_TOKEN}",
        "Accept": "application/json",
    }

    url = f"{TRADIER_BASE_URL}/markets/quotes"
    symbol_str = ",".join(symbols)

    resp = requests.get(
        url,
        headers=headers,
        params={"symbols": symbol_str, "greeks": "false"},
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()

    quotes = payload.get("quotes", {}).get("quote")
    if not quotes:
        return {}

    if isinstance(quotes, dict):
        quotes = [quotes]

    result = {}
    for quote in quotes:
        symbol = str(quote.get("symbol", "")).strip().upper()
        if symbol:
            result[symbol] = quote

    return result


def build_option_symbols(rows):
    symbols = []
    for _, row in rows.iterrows():
        sym = option_symbol_from_row(row)
        if sym:
            symbols.append(sym)
    return sorted(set(symbols))


def build_stock_symbols(rows):
    symbols = []
    for _, row in rows.iterrows():
        ticker = str(row["Ticker"]).strip().upper()
        if ticker:
            symbols.append(ticker)
    return sorted(set(symbols))


def get_stock_price_from_quote(q):
    if not q:
        return None

    bid = safe_float(q.get("bid"))
    ask = safe_float(q.get("ask"))

    if bid and ask and bid > 0 and ask > 0:
        return round((bid + ask) / 2, 4)

    for field in ["last", "close", "prevclose"]:
        val = safe_float(q.get(field))
        if val:
            return val

    return None


def build_record(row, option_quotes, stock_quotes, generated_at):
    ticker = str(row["Ticker"]).strip().upper()
    expiration = str(row["Expiration"]).strip()
    strike = safe_float(row.get("Strike"))

    option_symbol = option_symbol_from_row(row)

    scanned_stock_price = safe_float(row.get("Current Stock Price"))
    scanned_bid = safe_float(row.get("Bid"))
    scanned_ask = safe_float(row.get("Ask"))

    scanned_mid = None
    if scanned_bid and scanned_ask:
        scanned_mid = round((scanned_bid + scanned_ask) / 2, 4)

    result = {
        "ticker": ticker,
        "expiration": expiration,
        "strike": strike,
        "option_symbol": option_symbol,
        "generated_at": generated_at,

        "scanned_stock_price": scanned_stock_price,
        "live_stock_price": None,
        "stock_price_change_pct": None,

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

    option_quote = option_quotes.get(option_symbol)
    stock_quote = stock_quotes.get(ticker)

    # OPTION
    if not option_quote:
        result["error"] = f"No option quote for {option_symbol}"
        return result

    live_bid = safe_float(option_quote.get("bid"))
    live_ask = safe_float(option_quote.get("ask"))

    if live_bid and live_ask:
        result["live_bid"] = live_bid
        result["live_ask"] = live_ask
        result["live_mid"] = round((live_bid + live_ask) / 2, 4)

    if scanned_bid and live_bid:
        pct = (live_bid - scanned_bid) / scanned_bid
        result["premium_change_pct_from_bid"] = round(pct, 4)
        result["stale"] = abs(pct) > 0.10

    # STOCK
    if stock_quote:
        live_stock_price = get_stock_price_from_quote(stock_quote)
        result["live_stock_price"] = live_stock_price

        if scanned_stock_price and live_stock_price:
            pct = (live_stock_price - scanned_stock_price) / scanned_stock_price
            result["stock_price_change_pct"] = round(pct, 4)

    return result


def main():
    if not LATEST_REPORT.exists():
        raise FileNotFoundError(f"Missing {LATEST_REPORT}")

    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(LATEST_REPORT)
    df.columns = [str(c).strip() for c in df.columns]

    top10 = df.head(10).copy()
    generated_at = pd.Timestamp.utcnow().isoformat()

    option_symbols = build_option_symbols(top10)
    stock_symbols = build_stock_symbols(top10)

    option_quotes = fetch_tradier_quotes(option_symbols)
    stock_quotes = fetch_tradier_quotes(stock_symbols)

    records = [
        build_record(row, option_quotes, stock_quotes, generated_at)
        for _, row in top10.iterrows()
    ]

    payload = {
        "generated_at": generated_at,
        "source_report": LATEST_REPORT.name,
        "records": records,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"Saved live overlay JSON: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()