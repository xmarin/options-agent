import math
import os
import sys
import time
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv
from scipy.stats import norm

# Suppress noisy yfinance warnings
warnings.filterwarnings("ignore", category=FutureWarning)

load_dotenv()

from config.settings import (
    DEFAULT_CC_STRATEGY_MODE,
    DEFAULT_SKIP_DOWNTREND,
    DTE_MAX,
    DTE_MIN,
    FMP_API_KEY,
    MAX_DELTA,
    MAX_SPREAD_PCT,
    MIN_BID,
    MIN_DELTA,
    MIN_OPEN_INTEREST,
    MIN_OTM_PCT,
    MIN_VOLUME,
    STRATEGY_TARGETS,
)


def get_strategy_mode() -> str:
    mode = os.getenv("CC_STRATEGY_MODE", DEFAULT_CC_STRATEGY_MODE).strip().lower()
    if mode not in STRATEGY_TARGETS:
        valid = ", ".join(sorted(STRATEGY_TARGETS))
        raise RuntimeError(f"Invalid CC_STRATEGY_MODE='{mode}'. Valid values: {valid}")
    return mode


CC_STRATEGY_MODE = get_strategy_mode()
STRATEGY_CONFIG = STRATEGY_TARGETS[CC_STRATEGY_MODE]
SKIP_DOWNTREND = (
    os.getenv("SKIP_DOWNTREND", str(DEFAULT_SKIP_DOWNTREND)).strip().lower() == "true"
)


def load_tickers(path: str = "data/tickers.csv", limit: int = 500) -> list[str]:
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


RISK_FREE_RATE = float(os.getenv("RISK_FREE_RATE", "0.05"))

# Module-level SPY benchmark closes, populated once in main()
_SPY_CLOSES: list[float] = []


def bs_delta(S: float, K: float, T: float, sigma: float) -> float | None:
    """Black-Scholes call delta. T in years, sigma as decimal (e.g. 0.25 = 25%)."""
    if T <= 0 or sigma is None or sigma <= 0 or S <= 0 or K <= 0:
        return None
    try:
        d1 = (math.log(S / K) + (RISK_FREE_RATE + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return float(norm.cdf(d1))
    except Exception:
        return None


def compute_hv_rank(closes: list[float], window: int = 21) -> float | None:
    """
    HV Rank: where is current 21-day realized vol vs its 52-week range?
    Returns 0.0–1.0 (1.0 = historically high vol = best for premium selling).
    """
    if len(closes) < window * 2 + 10:
        return None
    log_rets: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            log_rets.append(math.log(closes[i] / closes[i - 1]))
    if len(log_rets) < window + 20:
        return None
    hvs: list[float] = []
    for i in range(window, len(log_rets) + 1):
        w = log_rets[i - window : i]
        mean = sum(w) / window
        variance = sum((r - mean) ** 2 for r in w) / (window - 1)
        hvs.append((variance ** 0.5) * (252 ** 0.5))
    if not hvs:
        return None
    lo, hi = min(hvs), max(hvs)
    if hi == lo:
        return 0.5
    return clamp((hvs[-1] - lo) / (hi - lo))


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI-14. Returns 0–100 or None."""
    if len(closes) < period + 3:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent = deltas[-(period + 1) :]
    gains = [d if d > 0 else 0.0 for d in recent]
    losses = [-d if d < 0 else 0.0 for d in recent]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    avg_gain = (avg_gain * (period - 1) + gains[-1]) / period
    avg_loss = (avg_loss * (period - 1) + losses[-1]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1 + rs)), 1)


def compute_beta(ticker_closes: list[float], spy_closes: list[float], window: int = 90) -> float | None:
    """Beta vs SPY over last `window` trading days."""
    n = min(len(ticker_closes), len(spy_closes), window + 1)
    if n < 20:
        return None
    tc = ticker_closes[-n:]
    sc = spy_closes[-n:]
    t_rets = [(tc[i] / tc[i - 1]) - 1 for i in range(1, len(tc))]
    s_rets = [(sc[i] / sc[i - 1]) - 1 for i in range(1, len(sc))]
    nr = min(len(t_rets), len(s_rets))
    if nr < 15:
        return None
    t_rets, s_rets = t_rets[:nr], s_rets[:nr]
    mean_t = sum(t_rets) / nr
    mean_s = sum(s_rets) / nr
    cov = sum((t_rets[i] - mean_t) * (s_rets[i] - mean_s) for i in range(nr)) / (nr - 1)
    var_s = sum((s_rets[i] - mean_s) ** 2 for i in range(nr)) / (nr - 1)
    if var_s < 1e-10:
        return None
    return round(cov / var_s, 3)


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


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def parse_date(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


def dte(expiration: str) -> int:
    return (parse_date(expiration) - date.today()).days


def build_occ_option_symbol(ticker: str, expiration: str, option_type: str, strike: float) -> str:
    ticker = str(ticker).strip().upper()
    exp_part = pd.to_datetime(expiration).strftime("%y%m%d")
    strike_int = int(round(float(strike) * 1000))
    strike_part = f"{strike_int:08d}"
    opt_type = str(option_type).strip().upper()
    return f"{ticker}{exp_part}{opt_type}{strike_part}"


def get_last_price(ticker: str) -> float | None:
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = safe_float(getattr(info, "last_price", None))
        if price and price > 0:
            return price
        # Fallback: last close from recent history
        hist = t.history(period="2d")
        if not hist.empty:
            return safe_float(hist["Close"].iloc[-1])
        return None
    except Exception:
        return None


def get_expirations(ticker: str) -> list[str]:
    try:
        t = yf.Ticker(ticker)
        exps = t.options  # tuple of "YYYY-MM-DD" strings
        return list(exps) if exps else []
    except Exception:
        return []


def choose_weekly_expiration(expirations: list[str]) -> str | None:
    candidates = [(e, dte(e)) for e in expirations]
    candidates = [(e, d) for (e, d) in candidates if DTE_MIN <= d <= DTE_MAX]

    if not candidates:
        return None

    fridays = [(e, d) for (e, d) in candidates if parse_date(e).weekday() == 4]
    pool = fridays if fridays else candidates
    pool.sort(key=lambda x: x[1])
    return pool[0][0]


def get_option_chain(ticker: str, expiration: str, spot: float) -> list[dict]:
    """
    Returns a list of option dicts in the same shape the rest of the scanner expects:
    option_type, strike, bid, ask, volume, open_interest, greeks.delta, greeks.mid_iv
    Delta is computed via Black-Scholes using the implied volatility yfinance provides.
    """
    try:
        t = yf.Ticker(ticker)
        chain = t.option_chain(expiration)
        calls_df = chain.calls
    except Exception:
        return []

    if calls_df is None or calls_df.empty:
        return []

    exp_date = parse_date(expiration)
    T = (exp_date - date.today()).days / 365.0

    results = []
    for _, row in calls_df.iterrows():
        strike = safe_float(row.get("strike"))
        bid = safe_float(row.get("bid"))
        ask = safe_float(row.get("ask"))
        volume = safe_int(row.get("volume"))
        open_interest = safe_int(row.get("openInterest"))
        iv = safe_float(row.get("impliedVolatility"))

        delta = bs_delta(spot, strike, T, iv) if (strike and iv) else None

        results.append({
            "option_type": "call",
            "strike": strike,
            "bid": bid,
            "ask": ask,
            "volume": volume,
            "open_interest": open_interest,
            "greeks": {
                "delta": delta,
                "mid_iv": iv,
                "smv_vol": iv,
            },
        })

    return results


def get_price_history(ticker: str, lookback_days: int = 365) -> list[float]:
    try:
        end_dt = date.today()
        start_dt = end_dt - timedelta(days=lookback_days)
        hist = yf.Ticker(ticker).history(start=start_dt.isoformat(), end=end_dt.isoformat())
        if hist.empty:
            return []
        return [safe_float(v) for v in hist["Close"].tolist() if safe_float(v) is not None]
    except Exception:
        return []


def get_trend_metrics(ticker: str, spy_closes: list[float] | None = None) -> dict:
    closes = get_price_history(ticker, lookback_days=365)

    base = {
        "SMA20": None, "SMA50": None, "20D Return": None,
        "Trend": "unknown", "Trend Score": 0.5,
        "HV Rank": None, "RSI": None, "Beta": None,
    }

    if len(closes) < 55:
        return base

    sma20 = sum(closes[-20:]) / 20.0
    sma50 = sum(closes[-50:]) / 50.0
    ret20 = (closes[-1] / closes[-21]) - 1 if len(closes) >= 21 else None
    spot = closes[-1]

    if ret20 is not None and spot > sma20 and sma20 > sma50 and ret20 > 0:
        trend, trend_score = "up", 1.0
    elif ret20 is not None and spot < sma20 and sma20 < sma50 and ret20 < 0:
        trend, trend_score = "down", 0.0
    else:
        trend, trend_score = "neutral", 0.5

    hv_rank = compute_hv_rank(closes)
    rsi = compute_rsi(closes)
    beta = compute_beta(closes, spy_closes) if spy_closes else None

    return {
        "SMA20": round(sma20, 2),
        "SMA50": round(sma50, 2),
        "20D Return": round(ret20, 4) if ret20 is not None else None,
        "Trend": trend,
        "Trend Score": trend_score,
        "HV Rank": round(hv_rank, 4) if hv_rank is not None else None,
        "RSI": rsi,
        "Beta": beta,
    }


def get_earnings_map(days_ahead: int = 90) -> dict:
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


def calculate_component_scores(
    premium: float,
    delta: float,
    otm_pct: float,
    spread_pct: float | None,
    earnings_risk: str,
    volume: int,
    open_interest: int,
    return_on_capital: float,
) -> dict:
    target_delta = float(STRATEGY_CONFIG["target_delta"])
    target_otm = float(STRATEGY_CONFIG["target_otm"])

    premium_score = clamp(premium / 1000.0)
    delta_score = clamp(1.0 - (abs(delta - target_delta) / max(target_delta, 0.05)))
    otm_score = clamp(1.0 - (abs(otm_pct - target_otm) / max(target_otm, 0.02)))

    if spread_pct is None:
        spread_score = 0.0
    else:
        spread_score = clamp(1.0 - (spread_pct / max(MAX_SPREAD_PCT, 0.01)))

    volume_score = clamp(volume / max(MIN_VOLUME * 4, 1))
    oi_score = clamp(open_interest / max(MIN_OPEN_INTEREST * 4, 1))
    liquidity_score = (spread_score * 0.40) + (volume_score * 0.30) + (oi_score * 0.30)

    earnings_score = 0.0 if earnings_risk == "YES" else 1.0
    roc_score = clamp(return_on_capital / 0.03)

    core_weights = {
        "premium_weight": float(STRATEGY_CONFIG["premium_weight"]),
        "delta_weight": float(STRATEGY_CONFIG["delta_weight"]),
        "otm_weight": float(STRATEGY_CONFIG["otm_weight"]),
        "liquidity_weight": float(STRATEGY_CONFIG["liquidity_weight"]),
        "earnings_weight": float(STRATEGY_CONFIG["earnings_weight"]),
        "roc_weight": float(STRATEGY_CONFIG["roc_weight"]),
    }

    core_score = (
        premium_score * core_weights["premium_weight"]
        + delta_score * core_weights["delta_weight"]
        + otm_score * core_weights["otm_weight"]
        + liquidity_score * core_weights["liquidity_weight"]
        + earnings_score * core_weights["earnings_weight"]
        + roc_score * core_weights["roc_weight"]
    )
    core_weight_total = sum(core_weights.values()) or 1.0

    return {
        "Premium Score": round(premium_score, 4),
        "Delta Score": round(delta_score, 4),
        "OTM Score": round(otm_score, 4),
        "Liquidity Score": round(liquidity_score, 4),
        "Earnings Score": round(earnings_score, 4),
        "ROC Score": round(roc_score, 4),
        "Target Delta": round(target_delta, 4),
        "Target OTM": round(target_otm, 4),
        "Score": round(core_score / core_weight_total, 4),
    }


def scan_one_ticker(ticker: str, earnings_map: dict, spy_closes: list[float] | None = None) -> list[dict]:
    spot = get_last_price(ticker)
    if not spot or spot <= 0:
        return []

    trend = get_trend_metrics(ticker, spy_closes)
    if SKIP_DOWNTREND and trend["Trend"] == "down":
        return []

    earnings_date = earnings_map.get(ticker)
    days_to_earnings = days_until(earnings_date)

    exp_list = get_expirations(ticker)
    exp = choose_weekly_expiration(exp_list)
    if not exp:
        return []

    days = dte(exp)
    chain = get_option_chain(ticker, exp, spot)

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
        if spread_pct is None or spread_pct > MAX_SPREAD_PCT:
            continue

        if volume < MIN_VOLUME:
            continue

        if open_interest < MIN_OPEN_INTEREST:
            continue

        premium = bid * 100.0
        capital_required = spot * 100.0
        return_on_capital = (premium / capital_required) if capital_required > 0 else 0.0
        annual_yield = (bid / spot) * (365.0 / days) if days > 0 else None
        assignment_proxy = delta
        weekly_income_estimate = premium

        earnings_risk = "NO"
        if earnings_date:
            try:
                earnings_risk = "YES" if parse_date(earnings_date) <= parse_date(exp) else "NO"
            except Exception:
                earnings_risk = "NO"

        component_scores = calculate_component_scores(
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

        row = {
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
            "Return on Capital": round(return_on_capital, 4),
            "Assignment Proxy": round(assignment_proxy, 3),
            "Weekly Income Estimate": round(weekly_income_estimate, 2),
            "Annual Yield": round(annual_yield, 4) if annual_yield is not None else None,
            "Spread%": round(spread_pct, 4),
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
            "HV Rank": trend["HV Rank"],
            "RSI": trend["RSI"],
            "Beta": trend["Beta"],
            "Strategy Mode": CC_STRATEGY_MODE,
        }
        row.update(component_scores)
        results.append(row)

    return results


def load_owned_tickers() -> set[str]:
    """Load tickers from data/owned_tickers.txt (written by import_positions.py)."""
    path = Path("data/owned_tickers.txt")
    if not path.exists():
        return set()
    return {t.strip().upper() for t in path.read_text(encoding="utf-8").splitlines() if t.strip()}


def apply_final_scoring(df: pd.DataFrame) -> pd.DataFrame:
    # Cross-scan IV percentile (how this option ranks vs others in today's scan)
    if "Current IV" in df.columns:
        df["IV Percentile (Scan)"] = (df["Current IV"].rank(pct=True) * 100).round(1)
    else:
        df["IV Percentile (Scan)"] = None

    iv_component = pd.to_numeric(df["IV Percentile (Scan)"], errors="coerce").fillna(0) / 100.0

    # HV Rank: 52-week realized-vol rank (proxy for IV rank vs own history)
    hv_rank_component = pd.to_numeric(df.get("HV Rank", pd.Series(dtype=float)), errors="coerce").fillna(0.5)

    # RSI signal: sweet spot 45–65 for covered calls (not oversold, not overbought)
    def _rsi_score(rsi_val) -> float:
        if pd.isna(rsi_val):
            return 0.5
        rsi_val = float(rsi_val)
        if 45 <= rsi_val <= 65:
            return 1.0
        elif rsi_val < 45:
            return max(0.0, rsi_val / 45.0)
        else:
            return max(0.0, 1.0 - (rsi_val - 65.0) / 35.0)

    rsi_col = df["RSI"] if "RSI" in df.columns else pd.Series([None] * len(df))
    rsi_component = rsi_col.apply(_rsi_score)

    # Beta signal: prefer moderate beta (~1.0), penalise very high or very low
    def _beta_score(beta_val) -> float:
        if pd.isna(beta_val):
            return 0.5
        return max(0.0, min(1.0, 1.0 - abs(float(beta_val) - 1.0) * 0.5))

    beta_col = df["Beta"] if "Beta" in df.columns else pd.Series([None] * len(df))
    beta_component = beta_col.apply(_beta_score)

    trend_component = df["Trend"].map({"up": 1.0, "neutral": 0.5, "down": 0.0}).fillna(0.5)
    score_component = df["Score"].fillna(0)

    # Owned-position bonus: stocks you already own need no capital to buy —
    # selling a call against them is immediately actionable.
    owned_tickers = load_owned_tickers()
    df["Owned Position"] = df["Ticker"].str.upper().isin(owned_tickers)
    owned_component = df["Owned Position"].astype(float)
    owned_count = int(owned_component.sum())
    if owned_count:
        print(f"  📌 Owned-position bonus applied to {owned_count} ticker(s): "
              f"{', '.join(df.loc[df['Owned Position'], 'Ticker'].tolist())}")

    core_weight_total = (
        STRATEGY_CONFIG["premium_weight"]
        + STRATEGY_CONFIG["delta_weight"]
        + STRATEGY_CONFIG["otm_weight"]
        + STRATEGY_CONFIG["liquidity_weight"]
        + STRATEGY_CONFIG["earnings_weight"]
        + STRATEGY_CONFIG["roc_weight"]
    )

    final_score = (
        score_component * core_weight_total
        + iv_component      * STRATEGY_CONFIG.get("iv_weight", 0.0)
        + hv_rank_component * STRATEGY_CONFIG.get("iv_rank_weight", 0.0)
        + trend_component   * STRATEGY_CONFIG.get("trend_weight", 0.0)
        + rsi_component     * STRATEGY_CONFIG.get("rsi_weight", 0.0)
        + beta_component    * STRATEGY_CONFIG.get("beta_weight", 0.0)
        + owned_component   * STRATEGY_CONFIG.get("owned_weight", 0.15)
    )

    df["Final Score"] = final_score.round(4)
    return df


def main():
    global _SPY_CLOSES
    tickers = load_tickers(limit=500)
    if len(sys.argv) > 1:
        tickers = [t.strip().upper() for t in sys.argv[1].split(",") if t.strip()]

    print(f"Scanner strategy mode: {CC_STRATEGY_MODE}")

    print("Fetching SPY benchmark history for beta calculation...")
    _SPY_CLOSES = get_price_history("SPY", lookback_days=365)
    print(f"  SPY history: {len(_SPY_CLOSES)} days loaded")
    if len(_SPY_CLOSES) == 0:
        print("  ⚠️  WARNING: SPY history is empty — markets may not be open yet or yfinance is unreachable.")
        print("  Beta scoring will be skipped this run (all beta scores = 0.5).")
        print("  If this repeats, check that the cron runs after 13:30 UTC (9:30 AM ET).")

    print("Loading earnings calendar...")
    earnings_map = get_earnings_map(days_ahead=90)
    print(f"Earnings entries loaded: {len(earnings_map)}")

    all_rows = []
    for i, ticker in enumerate(tickers, start=1):
        try:
            print(f"[{i}/{len(tickers)}] {ticker}")
            all_rows.extend(scan_one_ticker(ticker, earnings_map, _SPY_CLOSES))
            time.sleep(0.15)
        except Exception as e:
            print(f"  ! {ticker} error: {e}")

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("\nNo matches found. Loosen filters or scan more tickers.")
        print("Suggested loosening examples:")
        print("  export MIN_BID=1.50")
        print("  export MAX_DELTA=0.40")
        print("  export MIN_OTM_PCT=0.01")
        print("  export MIN_VOLUME=100")
        print("  export MIN_OPEN_INTEREST=200")
        raise RuntimeError("Scanner produced no matches.")

    df = apply_final_scoring(df)
    df = df.sort_values(
        ["Final Score", "Score", "Premium", "Annual Yield", "Open Interest", "Volume"],
        ascending=[False, False, False, False, False, False],
    )
    df = df.drop_duplicates(subset=["Ticker"], keep="first")

    os.makedirs("reports", exist_ok=True)
    os.makedirs("published", exist_ok=True)

    dated_out = os.path.join("reports", f"covered_call_report_{date.today().isoformat()}.csv")
    latest_out = os.path.join("reports", "covered_call_report_latest.csv")
    published_dated_out = os.path.join("published", f"covered_call_report_{date.today().isoformat()}.csv")
    published_latest_out = os.path.join("published", "covered_call_report_latest.csv")

    df.to_csv(dated_out, index=False)
    df.to_csv(latest_out, index=False)
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
