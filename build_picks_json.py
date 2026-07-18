"""Build published/picks_latest.json - a stable, public, machine-readable
feed of the week's top covered-call picks for external consumers.

Schema agreed with Danilo's eToro agent. Keep field names stable.
"""

import json
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
CSV_PATH = ROOT / "published" / "covered_call_report_latest.csv"
TOP_N = 10


def main():
    df = pd.read_csv(CSV_PATH)
    df = df.sort_values("Final Score", ascending=False).head(TOP_N)

    picks = []
    for _, row in df.iterrows():
        picks.append(
            {
                "ticker": row["Ticker"],
                "stock_price": round(float(row["Current Stock Price"]), 2),
                "strike": float(row["Strike"]),
                "expiration": str(row["Expiration"]),
                "dte": int(row["DTE"]),
                # Per-contract premium in USD (bid x 100)
                "premium": float(row["Premium"]),
                "hv_rank": round(float(row["HV Rank"]) * 100, 1),
                "rsi": float(row["RSI"]),
                "beta": float(row["Beta"]),
                "return_on_capital": round(float(row["Return on Capital"]), 4),
                "annual_yield": round(float(row["Annual Yield"]), 4),
                "pct_otm": round(float(row["OTM"]), 4),
                "delta": float(row["Delta"]),
                "final_score": round(float(row["Final Score"]), 4),
                "earnings_risk": str(row["Earnings Risk"]),
                "trend": str(row["Trend"]),
            }
        )

    payload = {
        "report_date": date.today().isoformat(),
        "week_of": date.today().isoformat(),
        "premium_unit": "usd_per_contract",
        "picks": picks,
    }

    published_dir = ROOT / "published"

    # Dated file — e.g. picks_07-20-2026.json — lets consumers build their own history
    dated_filename = f"picks_{date.today().strftime('%m-%d-%Y')}.json"
    dated_path = published_dir / dated_filename
    dated_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved dated picks JSON:  {dated_path} ({len(picks)} picks)")

    # Latest alias — always points to the most recent run
    latest_path = published_dir / "picks_latest.json"
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved latest picks JSON: {latest_path}")


if __name__ == "__main__":
    main()
