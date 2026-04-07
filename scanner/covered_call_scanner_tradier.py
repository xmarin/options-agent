import os
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests
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
CC_STRATEGY_MODE = os.getenv("CC_STRATEGY_MODE", "balanced").strip().lower()

# Supported modes:
#   income       -> favor richer premium / slightly closer strikes
#   balanced     -> default, trader-friendly middle ground
#   conservative -> favor lower assignment risk / farther OTM
STRATEGY_TARGETS = {
    "income": {
        "target_delta": 0.38,
        "target_otm": 0.025,
        "premium_weight": 0.30,
        "delta_weight": 0.24,
        "otm_weight": 0.10,
        "liquidity_weight": 0.16,
        "earnings_weight": 0.12,
        "roc_weight": 0.20,
        "iv_weight": 0.08,
        "trend_weight": 0.10,
    },
    "balanced": {
        "target_delta": 0.30,
        "target_otm": 0.045,
        "premium_weight": 0.26,
        "delta_weight": 0.26,
        "otm_weight": 0.12,
        "liquidity_weight": 0.16,
        "earnings_weight": 0.12,
        "roc_weight": 0.18,
        "iv_weight": 0.08,
        "trend_weight": 0.10,
    },
    "conservative": {
        "target_delta": 0.22,
        "target_otm": 0.065,
        "premium_weight": 0.20,
        "delta_weight": 0.28,
        "otm_weight": 0.16,
        "liquidity_weight": 0.16,
        "earnings_weight": 0.12,
        "roc_weight": 0.16,
        "iv_weight": 0.08,
        "trend_weight": 0.12,
    },
}


def get_strategy_config() -> dict:
    return STRATEGY_TARGETS.get(CC_STRATEGY_MODE, STRATEGY_TARGETS["balanced"])


def load_tickers(path="data/tickers.csv", limit=500):
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
        if x is None:
            return None
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


def build_occ_option_symbol(ticker: str, expiration: str, option_type: str, strike: float) -> str:
    """
    Example:
    AMD 2026-04-10 C 230 -> AMD260410C00230000
    """
    ticker = str(ticker).strip().upper()
    exp_part = pd.to_datetime(expiration).strftime("%y%m%d")
    strike_int = int(round(float(strike) * 1000))
    strike_part = f"{strike_int:08d}"
    opt_type = str(option_type).strip().upper()
    return f"{ticker}{exp_part}{opt_type}{strike_part}"


def get_last_price(ticker: str) -> float | None:
    data = tradier_get("/markets/quotes", {"symbols": ticker, "greeks": "false"})
    q = data.get("quotes", {}).get("quote")

    if not q:
        return None

    if isinstance(q, list):
        q = q[0]

    for field in ["last", "close", "prevclose"]:
        val = safe_float(q.get(field))
        if val is not None and val > 0:
            return val

    return None


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


def clamp(value: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    return max(min_value, min(value, max_value))


def closeness_score(value: float, target: float, tolerance: float) -> float:
    if tolerance <= 0:
        return 1.0 if value == target else 0.0
    return clamp(1.0 - (abs(value - target) / tolerance))


def calculate_score(
    premium: float,
    delta: float,
    otm_pct: float,
    spread_pct: float | None,
    earnings_risk: str,
    volume: int,
    open_interest: int,
    return_on_capital: float | None,
):
    cfg = get_strategy_config()

    # Better premium scaling for weeklies.
    # Most realistic bids for this scanner are usually in the $1-$15 range.
    premium_score = clamp((premium - 100.0) / 900.0)

    # Reward contracts near the target delta instead of blindly preferring lower delta.
    delta_score = closeness_score(delta, cfg["target_delta"], tolerance=0.14)

    # Reward contracts near a target OTM band instead of always pushing farther OTM.
    otm_score = closeness_score(otm_pct, cfg["target_otm"], tolerance=0.05)

    if spread_pct is None:
        spread_score = 0.0
    else:
        # Spreads under ~8% are excellent, 15%+ fade quickly.
        spread_score = clamp(1.0 - (spread_pct / 0.15))

    volume_score = clamp(volume / 1500.0)
    oi_score = clamp(open_interest / 6000.0)
    liquidity_score = (spread_score * 0.50) + (volume_score * 0.25) + (oi_score * 0.25)

    earnings_score = 0.0 if earnings_risk == "YES" else 1.0

    roc_value = return_on_capital if return_on_capital is not None else 0.0
    roc_score = clamp(roc_value / 0.03)

    score = (
        premium_score * cfg["premium_weight"]
        + delta_score * cfg["delta_weight"]
        + otm_score * cfg["otm_weight"]
        + liquidity_score * cfg["liquidity_weight"]
        + earnings_score * cfg["earnings_weight"]
        + roc_score * cfg["roc_weight"]
    )

    component_scores = {
        "Premium Score": round(premium_score, 4),
        "Delta Score": round(delta_score, 4),
        "OTM Score": round(otm_score, 4),
        "Liquidity Score": round(liquidity_score, 4),
        "Earnings Score": round(earnings_score, 4),
        "ROC Score": round(roc_score, 4),
    }

    return round(score, 4), component_scores


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

        score, component_scores = calculate_score(
            premium=premium,
            delta=delta,
            otm_pct=otm_pct,
            spread_pct=spread_pct,
            earnings_risk=earnings_risk,
            volume=volume,
            open_interest=open_interest,
            return_on_capital=return_on_capital,
        )

        option_symbol = build_occ_option_symbol(
            ticker=ticker,
            expiration=exp,
            option_type="C",
            strike=strike,
        )

        results.append(
            {
                "Ticker": ticker,
                "Current Stock Price": round(spot, 2),
                "Expiration": exp,
                "DTE": days,
                "Strike": round(strike, 2),
                "Option Type": "CALL",
                "Option Symbol": option_symbol,
                "Bid": round(bid, 2),
                "Ask": round(ask, 2),
                "Delta": round(delta, 3),
                "OTM": round(otm_pct, 4),
                "Premium": round(premium, 2),
                "Capital Required": round(capital_required, 2),
                "Return on Capital": round(return_on_capital, 4) if return_on_capital is not None else None,
                "Assignment Proxy": round(assignment_proxy, 3),
                "Weekly Income Estimate": round(weekly_income_estimate, 2),
                "Annual Yield": round(annual_yield, 4) if annual_yield is not None else None,
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
                "Strategy Mode": CC_STRATEGY_MODE,
                "Target Delta": round(get_strategy_config()["target_delta"], 3),
                "Target OTM": round(get_strategy_config()["target_otm"], 4),
                "Premium Score": component_scores["Premium Score"],
                "Delta Score": component_scores["Delta Score"],
                "OTM Score": component_scores["OTM Score"],
                "Liquidity Score": component_scores["Liquidity Score"],
                "Earnings Score": component_scores["Earnings Score"],
                "ROC Score": component_scores["ROC Score"],
                "Score": score,
            }
        )

    return results


def main():
    tickers = load_tickers(limit=500)

    if len(sys.argv) > 1:
        tickers = [t.strip().upper() for t in sys.argv[1].split(",") if t.strip()]

    print(f"Strategy mode: {CC_STRATEGY_MODE}")
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
        print("  export MAX_DELTA=0.40")
        print("  export MIN_OTM_PCT=0.01")
        raise RuntimeError("Scanner produced no matches.")

    if "Current IV" in df.columns:
        df["IV Percentile (Scan)"] = (df["Current IV"].rank(pct=True) * 100).round(1)
    else:
        df["IV Percentile (Scan)"] = None

    cfg = get_strategy_config()
    iv_component = df["IV Percentile (Scan)"].fillna(0) / 100.0
    base_component = df["Score"].fillna(0)
    trend_component = df["Trend"].map({"up": 1.0, "neutral": 0.5, "down": 0.0}).fillna(0.5)

    df["Final Score"] = (
        base_component * (1.0 - cfg["iv_weight"] - cfg["trend_weight"])
        + iv_component * cfg["iv_weight"]
        + trend_component * cfg["trend_weight"]
    ).round(4)

    df = df.sort_values(
        ["Final Score", "Premium Score", "Delta Score", "Liquidity Score", "Premium", "Open Interest", "Volume"],
        ascending=[False, False, False, False, False, False, False],
    )

    df = df.drop_duplicates(subset=["Ticker"], keep="first")

    os.makedirs("reports", exist_ok=True)
    dated_out = os.path.join("reports", f"covered_call_report_{date.today().isoformat()}.csv")
    latest_out = os.path.join("reports", "covered_call_report_latest.csv")
    df.to_csv(dated_out, index=False)
    df.to_csv(latest_out, index=False)

    os.makedirs("published", exist_ok=True)
    published_dated_out = os.path.join("published", f"covered_call_report_{date.today().isoformat()}.csv")
    published_latest_out = os.path.join("published", "covered_call_report_latest.csv")
    df.to_csv(published_dated_out, index=False)
    df.to_csv(published_latest_out, index=False)

    print(f"\nSaved CSV: {dated_out}")
    print(f"Saved latest CSV: {latest_out}")
    print(f"Saved published CSV: {published_dated_out}")
    print(f"Saved published latest CSV: {published_latest_out}")
    print("\nTop 10:")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
