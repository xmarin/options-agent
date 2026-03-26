import os
import sys
import time
import requests
import pandas as pd
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from config.settings import (
    MIN_BID,
    MIN_DELTA,
    MAX_DELTA,
    MIN_OTM_PCT,
    MAX_SPREAD_PCT,
    DTE_MIN,
    DTE_MAX,
    TRADIER_BASE_URL,
    TRADIER_TOKEN,
    FMP_API_KEY,
)

SKIP_DOWNTREND = os.getenv("SKIP_DOWNTREND", "true").lower() == "true"


def load_tickers(path="data/tickers.csv", limit=500):
    """
    Keep this simple and stable for now:
    use your curated local universe.
    """
    df = pd.read_csv(path)
    return (
        df["ticker"]
        .dropna()
        .astype(str)
        .str.upper()
        .str.strip()
        .drop_duplicates()
        .tolist()[:limit]
    )


# =========================
# Helpers
# =========================
def tradier_get(path: str, params: dict) -> dict:
    if not TRADIER_TOKEN:
        raise RuntimeError(
            "Missing TRADIER_TOKEN. Set it like:\n"
            "  export TRADIER_TOKEN='YOUR_TOKEN'\n"
            "Optional:\n"
            "  export TRADIER_BASE_URL='https://sandbox.tradier.com/v1'"
        )

    headers = {
        "Authorization": f"Bearer {TRADIER_TOKEN}",
        "Accept": "application/json",
    }

    url = f"{TRADIER_BASE_URL}{path}"
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


def safe_int(x):
    try:
        return int(float(x))
    except Exception:
        return 0


def parse_date(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


def dte(expiration: str) -> int:
    return (parse_date(expiration) - date.today()).days


def get_last_price(ticker: str) -> float | None:
    data = tradier_get("/markets/quotes", {"symbols": ticker, "greeks": "false"})
    q = data.get("quotes", {}).get("quote")

    if not q:
        return None

    if isinstance(q, list):
        q = q[0]

    return safe_float(q.get("last"))


def get_expirations(ticker: str) -> list[str]:
    data = tradier_get(
        "/markets/options/expirations",
        {
            "symbol": ticker,
            "includeAllRoots": "true",
        },
    )

    dates = data.get("expirations", {}).get("date", [])
    if isinstance(dates, str):
        return [dates]

    return list(dates)


def choose_weekly_expiration(expirations: list[str]) -> str | None:
    candidates = [(e, dte(e)) for e in expirations]
    candidates = [(e, d) for (e, d) in candidates if DTE_MIN <= d <= DTE_MAX]

    if not candidates:
        return None

    fridays = [(e, d) for (e, d) in candidates if parse_date(e).weekday() == 4]
    pool = fridays if fridays else candidates
    pool.sort(key=lambda x: x[1])

    return pool[0][0]


def get_option_chain(ticker: str, expiration: str) -> list[dict]:
    data = tradier_get(
        "/markets/options/chains",
        {
            "symbol": ticker,
            "expiration": expiration,
            "greeks": "true",
        },
    )

    opt = data.get("options", {}).get("option")
    if not opt:
        return []

    return opt if isinstance(opt, list) else [opt]


def get_price_history(ticker: str, lookback_days: int = 90) -> list[float]:
    """
    Uses Tradier daily history to derive simple trend metrics.
    """
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=lookback_days)

    data = tradier_get(
        "/markets/history",
        {
            "symbol": ticker,
            "interval": "daily",
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        },
    )

    days = data.get("history", {}).get("day")
    if not days:
        return []

    if isinstance(days, dict):
        days = [days]

    closes = []
    for row in days:
        close_val = safe_float(row.get("close"))
        if close_val is not None:
            closes.append(close_val)

    return closes


def get_trend_metrics(ticker: str) -> dict:
    closes = get_price_history(ticker, lookback_days=120)

    if len(closes) < 55:
        return {
            "SMA20": None,
            "SMA50": None,
            "20D Return": None,
            "Trend": "unknown",
            "Trend Score": 0.5,
        }

    sma20 = sum(closes[-20:]) / 20.0
    sma50 = sum(closes[-50:]) / 50.0
    ret20 = (closes[-1] / closes[-21]) - 1 if len(closes) >= 21 else None
    spot = closes[-1]

    if ret20 is not None and spot > sma20 and sma20 > sma50 and ret20 > 0:
        trend = "up"
        trend_score = 1.0
    elif ret20 is not None and spot < sma20 and sma20 < sma50 and ret20 < 0:
        trend = "down"
        trend_score = 0.0
    else:
        trend = "neutral"
        trend_score = 0.5

    return {
        "SMA20": round(sma20, 2),
        "SMA50": round(sma50, 2),
        "20D Return": round(ret20, 4) if ret20 is not None else None,
        "Trend": trend,
        "Trend Score": trend_score,
    }


# =========================
# Earnings helpers
# =========================
def get_earnings_map(days_ahead=90):
    if not FMP_API_KEY:
        return {}

    try:
        url = "https://financialmodelingprep.com/stable/earnings-calendar"
        params = {
            "from": date.today().isoformat(),
            "to": (date.today() + timedelta(days=days_ahead)).isoformat(),
            "apikey": FMP_API_KEY,
        }

        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        earnings_map = {}

        for row in data:
            symbol = str(row.get("symbol", "")).upper().strip()
            earnings_date = row.get("date")

            if not symbol or not earnings_date:
                continue

            if symbol not in earnings_map:
                earnings_map[symbol] = earnings_date
            else:
                try:
                    if parse_date(earnings_date) < parse_date(earnings_map[symbol]):
                        earnings_map[symbol] = earnings_date
                except Exception:
                    pass

        return earnings_map

    except Exception as e:
        print(f"  ! Earnings calendar load failed: {e}")
        return {}


def days_until(date_str: str | None) -> int | None:
    if not date_str:
        return None

    try:
        target = parse_date(date_str)
        return (target - date.today()).days
    except Exception:
        return None


# =========================
# Ranking logic
# =========================
def calculate_score(
    premium: float,
    delta: float,
    otm_pct: float,
    spread_pct: float | None,
    earnings_risk: str,
    volume: int,
    open_interest: int,
):
    premium_score = min(premium / 1000.0, 1.0)
    delta_score = max(0.0, 1.0 - delta)
    otm_score = min(otm_pct / 0.15, 1.0)

    if spread_pct is None:
        spread_score = 0.0
    else:
        spread_score = max(0.0, 1.0 - min(spread_pct / 0.25, 1.0))

    volume_score = min(volume / 1000.0, 1.0)
    oi_score = min(open_interest / 5000.0, 1.0)

    liquidity_score = (spread_score * 0.4) + (volume_score * 0.3) + (oi_score * 0.3)
    earnings_score = 0.0 if earnings_risk == "YES" else 1.0

    score = (
        premium_score * 0.30 +
        otm_score * 0.18 +
        liquidity_score * 0.22 +
        delta_score * 0.15 +
        earnings_score * 0.15
    )

    return round(score, 4)


# =========================
# Core scan logic
# =========================
def scan_one_ticker(ticker: str, earnings_map: dict) -> list[dict]:
    spot = get_last_price(ticker)
    if not spot or spot <= 0:
        return []

    trend = get_trend_metrics(ticker)

    if SKIP_DOWNTREND and trend["Trend"] == "down":
        return []

    earnings_date = earnings_map.get(ticker)
    days_to_earnings = days_until(earnings_date)

    exp_list = get_expirations(ticker)
    exp = choose_weekly_expiration(exp_list)
    if not exp:
        return []

    days = dte(exp)
    chain = get_option_chain(ticker, exp)

    results = []
    for c in chain:
        if c.get("option_type") != "call":
            continue

        strike = safe_float(c.get("strike"))
        bid = safe_float(c.get("bid"))
        ask = safe_float(c.get("ask"))
        volume = safe_int(c.get("volume"))
        open_interest = safe_int(c.get("open_interest"))

        greeks = c.get("greeks") or {}
        delta = safe_float(greeks.get("delta"))
        mid_iv = safe_float(greeks.get("mid_iv"))
        smv_vol = safe_float(greeks.get("smv_vol"))
        current_iv = mid_iv if mid_iv is not None else smv_vol

        if strike is None or bid is None or ask is None or delta is None:
            continue

        otm_pct = (strike - spot) / spot
        if otm_pct < MIN_OTM_PCT:
            continue

        if bid < MIN_BID:
            continue

        if not (MIN_DELTA <= delta <= MAX_DELTA):
            continue

        mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else None
        spread_pct = ((ask - bid) / mid) if (mid and mid > 0 and ask >= bid) else None
        if spread_pct is not None and spread_pct > MAX_SPREAD_PCT:
            continue

        premium = bid * 100.0
        capital_required = spot * 100.0
        return_on_capital = (premium / capital_required) if capital_required > 0 else None
        annual_yield = (bid / spot) * (365.0 / days) if days > 0 else None
        assignment_proxy = delta
        weekly_income_estimate = premium

        earnings_risk = "NO"
        if earnings_date:
            try:
                earnings_risk = "YES" if parse_date(earnings_date) <= parse_date(exp) else "NO"
            except Exception:
                earnings_risk = "NO"

        score = calculate_score(
            premium=premium,
            delta=delta,
            otm_pct=otm_pct,
            spread_pct=spread_pct,
            earnings_risk=earnings_risk,
            volume=volume,
            open_interest=open_interest,
        )

        results.append(
            {
                "Ticker": ticker,
                "Current Stock Price": round(spot, 2),
                "Bid": round(bid, 2),
                "Ask": round(ask, 2),
                "Strike": round(strike, 2),
                "Delta": round(delta, 3),
                "OTM": round(otm_pct, 4),
                "Premium": round(premium, 2),
                "Capital Required": round(capital_required, 2),
                "Return on Capital": round(return_on_capital, 4) if return_on_capital is not None else None,
                "Assignment Proxy": round(assignment_proxy, 3),
                "Weekly Income Estimate": round(weekly_income_estimate, 2),
                "Annual Yield": round(annual_yield, 4) if annual_yield is not None else None,
                "Expiration": exp,
                "DTE": days,
                "Spread%": round(spread_pct, 4) if spread_pct is not None else None,
                "Volume": volume,
                "Open Interest": open_interest,
                "Current IV": round(current_iv, 4) if current_iv is not None else None,
                "Next Earnings Date": earnings_date,
                "Days To Earnings": days_to_earnings,
                "Earnings Risk": earnings_risk,
                "SMA20": trend["SMA20"],
                "SMA50": trend["SMA50"],
                "20D Return": trend["20D Return"],
                "Trend": trend["Trend"],
                "Score": score,
            }
        )

    return results


def main():
    tickers = load_tickers(limit=500)

    if len(sys.argv) > 1:
        tickers = [t.strip().upper() for t in sys.argv[1].split(",") if t.strip()]

    print("Loading earnings calendar...")
    earnings_map = get_earnings_map(days_ahead=90)
    print(f"Earnings entries loaded: {len(earnings_map)}")

    all_rows = []
    for i, t in enumerate(tickers, start=1):
        try:
            print(f"[{i}/{len(tickers)}] {t}")
            all_rows.extend(scan_one_ticker(t, earnings_map))
            time.sleep(0.15)
        except Exception as e:
            print(f"  ! {t} error: {e}")

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("\nNo matches found. Loosen filters or scan more tickers.")
        print("Suggested loosening examples:")
        print("  export MIN_BID=1.50")
        print("  export MAX_DELTA=0.35")
        print("  export MIN_OTM_PCT=0.02")
        return

    if "Current IV" in df.columns:
        df["IV Percentile (Scan)"] = (df["Current IV"].rank(pct=True) * 100).round(1)
    else:
        df["IV Percentile (Scan)"] = None

    iv_component = df["IV Percentile (Scan)"].fillna(0) / 100.0
    base_component = df["Score"].fillna(0)
    roc_component = df["Return on Capital"].fillna(0).clip(upper=0.03) / 0.03
    trend_component = df["Trend"].map({"up": 1.0, "neutral": 0.5, "down": 0.0}).fillna(0.5)

    df["Final Score"] = (
        base_component * 0.60 +
        iv_component * 0.10 +
        roc_component * 0.20 +
        trend_component * 0.10
    ).round(4)

    df = df.sort_values(
        ["Final Score", "Premium", "Annual Yield", "Open Interest", "Volume"],
        ascending=[False, False, False, False, False],
    )

    # Keep only the best contract per stock
    df = df.drop_duplicates(subset=["Ticker"], keep="first")

    os.makedirs("reports", exist_ok=True)

    dated_out = os.path.join("reports", f"covered_call_report_{date.today().isoformat()}.csv")
    df.to_csv(dated_out, index=False)

    latest_out = os.path.join("reports", "covered_call_report_latest.csv")
    df.to_csv(latest_out, index=False)

    os.makedirs("published", exist_ok=True)

    published_dated_out = os.path.join("published", f"covered_call_report_{date.today().isoformat()}.csv")
    df.to_csv(published_dated_out, index=False)

    published_latest_out = os.path.join("published", "covered_call_report_latest.csv")
    df.to_csv(published_latest_out, index=False)

    print(f"\nSaved CSV: {dated_out}")
    print(f"Saved latest CSV: {latest_out}")
    print(f"Saved published CSV: {published_dated_out}")
    print(f"Saved published latest CSV: {published_latest_out}")
    print("\nTop 10:")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()