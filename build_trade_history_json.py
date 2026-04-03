import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
CSV_PATH = ROOT / "data" / "trade_history.csv"
PUBLISHED_DIR = ROOT / "published"
OUTPUT_PATH = PUBLISHED_DIR / "trade_history.json"


def safe_float(value):
    if pd.isna(value):
        return None
    try:
        text = str(value).replace("$", "").replace("%", "").replace(",", "").strip()
        if text == "":
            return None
        return float(text)
    except Exception:
        return None


def safe_int(value):
    f = safe_float(value)
    if f is None:
        return None
    return int(round(f))


def safe_str(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text if text else None


def safe_bool(value):
    if pd.isna(value):
        return None
    text = str(value).strip().lower()
    if text in {"yes", "y", "true", "1"}:
        return True
    if text in {"no", "n", "false", "0"}:
        return False
    return None


def safe_date(value):
    if pd.isna(value):
        return None
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return safe_str(value)
        return parsed.date().isoformat()
    except Exception:
        return safe_str(value)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().replace("\n", " ") for c in df.columns]
    return df


def get_col(row, *possible_names):
    for name in possible_names:
        if name in row.index:
            return row[name]
    return None


def build_record(row):
    stock = safe_str(get_col(row, "Stock", "Ticker", "Recommended Ticker"))
    transaction_date = safe_date(get_col(row, "Transaction Date"))
    call_expiration = safe_date(get_col(row, "Call Expiration Date"))
    close_date = safe_date(get_col(row, "Close Date"))
    report_date = safe_date(get_col(row, "Report Date"))

    stock_price = safe_float(get_col(row, "Stock Price"))
    sold_price = safe_float(get_col(row, "Sold Price"))
    premium_dollars = safe_float(get_col(row, "Premium ($)"))
    strike_price = safe_float(get_col(row, "Call Strike Price"))
    dte = safe_int(get_col(row, "DTE (days)", "DTE"))
    total_market_value = safe_float(get_col(row, "Total Market Value"))
    pct_otm = safe_float(get_col(row, "% OTM (Out of the Money)", "% OTM"))
    return_on_stock_period = safe_float(get_col(row, "Return on Stock (period)"))

    recommended_ticker = safe_str(get_col(row, "Recommended Ticker"))
    recommended_strike = safe_float(get_col(row, "Recommended Strike"))
    recommended_expiration = safe_date(get_col(row, "Recommended Expiration"))
    recommended_premium = safe_float(get_col(row, "Recommended Premium"))
    recommended_final_score = safe_float(get_col(row, "Recommended Final Score"))
    best_trade_of_week = safe_bool(get_col(row, "Best Trade of Week?"))

    actual_premium_collected = safe_float(get_col(row, "Actual Premium Collected"))
    contracts_sold = safe_int(get_col(row, "Contracts Sold"))
    assigned = safe_bool(get_col(row, "Assigned?"))
    assignment_price = safe_float(get_col(row, "Assignment Price"))
    expired_worthless = safe_bool(get_col(row, "Expired Worthless?"))
    rolled = safe_bool(get_col(row, "Rolled?"))
    buyback_price = safe_float(get_col(row, "Buyback Price"))
    buyback_cost = safe_float(get_col(row, "Buyback Cost ($)"))
    underlying_exit_price = safe_float(get_col(row, "Underlying Exit Price"))
    realized_total_pnl = safe_float(get_col(row, "Realized Total P&L ($)"))
    net_option_pnl = safe_float(get_col(row, "Net Option P&L ($)"))
    underlying_pnl = safe_float(get_col(row, "Underlying P&L ($)"))
    days_held = safe_int(get_col(row, "Days Held"))
    annualized_actual_return = safe_float(get_col(row, "Annualized Actual Return"))
    notes = safe_str(get_col(row, "Notes"))
    contract = safe_str(get_col(row, "Contract"))

    ticker_match = None
    if stock and recommended_ticker:
        ticker_match = (stock.upper() == recommended_ticker.upper())

    strike_match = None
    if strike_price is not None and recommended_strike is not None:
        strike_match = abs(strike_price - recommended_strike) < 0.0001

    expiration_match = None
    if call_expiration and recommended_expiration:
        expiration_match = (call_expiration == recommended_expiration)

    premium_variance = None
    if actual_premium_collected is not None and recommended_premium is not None:
        premium_variance = actual_premium_collected - recommended_premium

    is_closed = any([
        assigned is True,
        expired_worthless is True,
        rolled is True,
        close_date is not None,
        realized_total_pnl is not None,
    ])

    status = "open"
    if assigned is True:
        status = "assigned"
    elif expired_worthless is True:
        status = "expired_worthless"
    elif rolled is True:
        status = "rolled"
    elif is_closed:
        status = "closed"

    return {
        "stock": stock,
        "transaction_date": transaction_date,
        "stock_price": stock_price,
        "contract": contract,
        "total_market_value": total_market_value,
        "call_expiration_date": call_expiration,
        "dte_days": dte,
        "call_strike_price": strike_price,
        "sold_price": sold_price,
        "premium_dollars": premium_dollars,
        "pct_otm": pct_otm,
        "return_on_stock_period": return_on_stock_period,

        "report_date": report_date,
        "recommended_ticker": recommended_ticker,
        "recommended_strike": recommended_strike,
        "recommended_expiration": recommended_expiration,
        "recommended_premium": recommended_premium,
        "recommended_final_score": recommended_final_score,
        "best_trade_of_week": best_trade_of_week,

        "actual_premium_collected": actual_premium_collected,
        "contracts_sold": contracts_sold,
        "assigned": assigned,
        "assignment_price": assignment_price,
        "expired_worthless": expired_worthless,
        "rolled": rolled,
        "buyback_price": buyback_price,
        "buyback_cost": buyback_cost,
        "underlying_exit_price": underlying_exit_price,
        "net_option_pnl": net_option_pnl,
        "underlying_pnl": underlying_pnl,
        "realized_total_pnl": realized_total_pnl,
        "close_date": close_date,
        "days_held": days_held,
        "annualized_actual_return": annualized_actual_return,
        "notes": notes,

        "ticker_match": ticker_match,
        "strike_match": strike_match,
        "expiration_match": expiration_match,
        "premium_variance": premium_variance,
        "status": status,
        "is_closed": is_closed,
    }


def build_summary(records):
    valid_records = [r for r in records if r.get("stock")]
    closed_records = [r for r in valid_records if r.get("is_closed")]
    open_records = [r for r in valid_records if not r.get("is_closed")]
    assigned_records = [r for r in valid_records if r.get("assigned") is True]
    expired_records = [r for r in valid_records if r.get("expired_worthless") is True]
    rolled_records = [r for r in valid_records if r.get("rolled") is True]

    total_premium_collected = sum(
        r["actual_premium_collected"] for r in valid_records
        if r.get("actual_premium_collected") is not None
    )

    total_realized_pnl = sum(
        r["realized_total_pnl"] for r in valid_records
        if r.get("realized_total_pnl") is not None
    )

    matched_recommendations = [
        r for r in valid_records
        if r.get("ticker_match") is True
    ]

    return {
        "total_trades": len(valid_records),
        "open_trades": len(open_records),
        "closed_trades": len(closed_records),
        "assigned_count": len(assigned_records),
        "expired_worthless_count": len(expired_records),
        "rolled_count": len(rolled_records),
        "total_premium_collected": round(total_premium_collected, 2),
        "total_realized_pnl": round(total_realized_pnl, 2),
        "recommendation_ticker_match_count": len(matched_recommendations),
    }


def main():
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Missing trade history CSV: {CSV_PATH}")

    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(CSV_PATH)
    df = normalize_columns(df)

    records = [build_record(row) for _, row in df.iterrows()]
    summary = build_summary(records)

    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "source_file": str(CSV_PATH.name),
        "summary": summary,
        "records": records,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved trade history JSON: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()