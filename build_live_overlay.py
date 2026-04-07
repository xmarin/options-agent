import json
import math
import os
from pathlib import Path
from typing import Any

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
OVERLAY_LIMIT = int(os.getenv("LIVE_OVERLAY_LIMIT", "10"))
STALE_BID_THRESHOLD_PCT = float(os.getenv("LIVE_OVERLAY_STALE_BID_THRESHOLD", "0.10"))
STALE_STOCK_THRESHOLD_PCT = float(os.getenv("LIVE_OVERLAY_STALE_STOCK_THRESHOLD", "0.03"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("LIVE_OVERLAY_TIMEOUT_SECONDS", "20"))


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if pd.isna(value):
            return None
        text = str(value).replace("$", "").replace("%", "").replace(",", "").strip()
        if not text:
            return None
        num = float(text)
        if math.isnan(num) or math.isinf(num):
            return None
        return num
    except Exception:
        return None


def clean_str(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def option_symbol_from_row(row: pd.Series) -> str | None:
    existing = clean_str(row.get("Option Symbol", "")).upper()
    if existing:
        return existing

    ticker = clean_str(row.get("Ticker", "")).upper()
    expiration = clean_str(row.get("Expiration", ""))
    strike = safe_float(row.get("Strike"))

    if not ticker or not expiration or strike is None:
        return None

    exp_part = pd.to_datetime(expiration).strftime("%y%m%d")
    strike_int = int(round(float(strike) * 1000))
    strike_formatted = f"{strike_int:08d}"
    return f"{ticker}{exp_part}C{strike_formatted}"


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def fetch_tradier_quotes(symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    if not TRADIER_TOKEN:
        return {}

    headers = {
        "Authorization": f"Bearer {TRADIER_TOKEN}",
        "Accept": "application/json",
    }
    url = f"{TRADIER_BASE_URL}/markets/quotes"

    results: dict[str, dict] = {}
    for batch in chunked(sorted(set(symbols)), 200):
        resp = requests.get(
            url,
            headers=headers,
            params={"symbols": ",".join(batch), "greeks": "false"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        quotes = payload.get("quotes", {}).get("quote")
        if not quotes:
            continue
        if isinstance(quotes, dict):
            quotes = [quotes]
        for quote in quotes:
            symbol = clean_str(quote.get("symbol", "")).upper()
            if symbol:
                results[symbol] = quote

    return results


def build_option_symbols(rows: pd.DataFrame) -> list[str]:
    symbols: list[str] = []
    for _, row in rows.iterrows():
        sym = option_symbol_from_row(row)
        if sym:
            symbols.append(sym)
    return sorted(set(symbols))


def build_stock_symbols(rows: pd.DataFrame) -> list[str]:
    symbols: list[str] = []
    for _, row in rows.iterrows():
        ticker = clean_str(row.get("Ticker", "")).upper()
        if ticker:
            symbols.append(ticker)
    return sorted(set(symbols))


def get_mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0:
        return None
    if ask < bid:
        return None
    return round((bid + ask) / 2.0, 4)


def get_price_from_quote(q: dict | None) -> float | None:
    if not q:
        return None

    bid = safe_float(q.get("bid"))
    ask = safe_float(q.get("ask"))
    mid = get_mid(bid, ask)
    if mid is not None:
        return mid

    for field in ["last", "close", "prevclose"]:
        val = safe_float(q.get(field))
        if val is not None and val > 0:
            return round(val, 4)

    return None


def bool_or_none(value: bool | None) -> bool | None:
    return value if value is not None else None


def build_record(row: pd.Series, option_quotes: dict[str, dict], stock_quotes: dict[str, dict], generated_at: str) -> dict:
    ticker = clean_str(row.get("Ticker", "")).upper()
    expiration = clean_str(row.get("Expiration", ""))
    strike = safe_float(row.get("Strike"))
    option_symbol = option_symbol_from_row(row)

    scanned_stock_price = safe_float(row.get("Current Stock Price"))
    scanned_bid = safe_float(row.get("Bid"))
    scanned_ask = safe_float(row.get("Ask"))
    scanned_mid = get_mid(scanned_bid, scanned_ask)
    scanned_delta = safe_float(row.get("Delta"))
    scanned_premium = safe_float(row.get("Premium"))
    scanned_roc = safe_float(row.get("Return on Capital"))
    final_score = safe_float(row.get("Final Score"))
    rank = safe_float(row.get("Rank"))

    result = {
        "ticker": ticker,
        "expiration": expiration,
        "strike": strike,
        "option_symbol": option_symbol,
        "generated_at": generated_at,
        "rank": int(rank) if rank is not None else None,
        "final_score": round(final_score, 4) if final_score is not None else None,
        "delta": round(scanned_delta, 4) if scanned_delta is not None else None,
        "return_on_capital": round(scanned_roc, 4) if scanned_roc is not None else None,
        "scanned_premium": round(scanned_premium, 2) if scanned_premium is not None else None,
        "scanned_stock_price": scanned_stock_price,
        "live_stock_price": None,
        "stock_price_change_pct": None,
        "stock_stale": None,
        "scanned_bid": scanned_bid,
        "scanned_ask": scanned_ask,
        "scanned_mid": scanned_mid,
        "live_bid": None,
        "live_ask": None,
        "live_mid": None,
        "live_premium": None,
        "premium_change_pct_from_bid": None,
        "premium_change_pct_from_mid": None,
        "stale": None,
        "has_live_option_quote": False,
        "has_live_stock_quote": False,
        "error": None,
    }

    if not TRADIER_TOKEN:
        result["error"] = "Missing TRADIER_TOKEN"
        return result

    if not option_symbol:
        result["error"] = "Missing option symbol"
        return result

    option_quote = option_quotes.get(option_symbol)
    stock_quote = stock_quotes.get(ticker)

    if stock_quote:
        result["has_live_stock_quote"] = True
        live_stock_price = get_price_from_quote(stock_quote)
        result["live_stock_price"] = live_stock_price
        if scanned_stock_price is not None and live_stock_price is not None and scanned_stock_price > 0:
            stock_change = (live_stock_price - scanned_stock_price) / scanned_stock_price
            result["stock_price_change_pct"] = round(stock_change, 4)
            result["stock_stale"] = abs(stock_change) > STALE_STOCK_THRESHOLD_PCT

    if not option_quote:
        result["error"] = f"No option quote for {option_symbol}"
        result["stale"] = bool_or_none(result["stock_stale"])
        return result

    result["has_live_option_quote"] = True

    live_bid = safe_float(option_quote.get("bid"))
    live_ask = safe_float(option_quote.get("ask"))
    live_mid = get_mid(live_bid, live_ask)

    result["live_bid"] = live_bid
    result["live_ask"] = live_ask
    result["live_mid"] = live_mid
    result["live_premium"] = round(live_bid * 100.0, 2) if live_bid is not None else None

    stale_flags: list[bool] = []

    if scanned_bid is not None and live_bid is not None and scanned_bid > 0:
        bid_change = (live_bid - scanned_bid) / scanned_bid
        result["premium_change_pct_from_bid"] = round(bid_change, 4)
        stale_flags.append(abs(bid_change) > STALE_BID_THRESHOLD_PCT)

    if scanned_mid is not None and live_mid is not None and scanned_mid > 0:
        mid_change = (live_mid - scanned_mid) / scanned_mid
        result["premium_change_pct_from_mid"] = round(mid_change, 4)
        stale_flags.append(abs(mid_change) > STALE_BID_THRESHOLD_PCT)

    if result["stock_stale"] is not None:
        stale_flags.append(result["stock_stale"])

    result["stale"] = any(stale_flags) if stale_flags else None
    return result


def choose_rows_for_overlay(df: pd.DataFrame, limit: int) -> pd.DataFrame:
    working = df.copy()

    if "Final Score" in working.columns:
        working["__sort_final_score"] = pd.to_numeric(working["Final Score"], errors="coerce")
    else:
        working["__sort_final_score"] = None

    if "Premium" in working.columns:
        working["__sort_premium"] = pd.to_numeric(working["Premium"], errors="coerce")
    else:
        working["__sort_premium"] = None

    if "Return on Capital" in working.columns:
        working["__sort_roc"] = pd.to_numeric(working["Return on Capital"], errors="coerce")
    else:
        working["__sort_roc"] = None

    if "Ticker" in working.columns:
        working["Ticker"] = working["Ticker"].astype(str).str.upper().str.strip()

    working = working.sort_values(
        by=["__sort_final_score", "__sort_premium", "__sort_roc"],
        ascending=[False, False, False],
        na_position="last",
    )

    return working.head(limit).copy()


def main() -> None:
    if not LATEST_REPORT.exists():
        raise FileNotFoundError(f"Missing {LATEST_REPORT}")

    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(LATEST_REPORT)
    df.columns = [str(c).strip() for c in df.columns]

    selected_rows = choose_rows_for_overlay(df, OVERLAY_LIMIT)
    generated_at = pd.Timestamp.utcnow().isoformat()

    option_symbols = build_option_symbols(selected_rows)
    stock_symbols = build_stock_symbols(selected_rows)

    try:
        option_quotes = fetch_tradier_quotes(option_symbols)
        stock_quotes = fetch_tradier_quotes(stock_symbols)
    except Exception as exc:
        payload = {
            "generated_at": generated_at,
            "source_report": LATEST_REPORT.name,
            "strategy_mode": clean_str(os.getenv("CC_STRATEGY_MODE", "balanced")).lower() or "balanced",
            "error": f"Failed to fetch live quotes: {exc}",
            "records": [],
        }
        OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
        raise

    records = [
        build_record(row, option_quotes, stock_quotes, generated_at)
        for _, row in selected_rows.iterrows()
    ]

    payload = {
        "generated_at": generated_at,
        "source_report": LATEST_REPORT.name,
        "strategy_mode": clean_str(os.getenv("CC_STRATEGY_MODE", "balanced")).lower() or "balanced",
        "overlay_limit": OVERLAY_LIMIT,
        "records": records,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"Saved live overlay JSON: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
