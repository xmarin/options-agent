import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
PUBLISHED_DIR = Path("published")
LATEST_CSV = PUBLISHED_DIR / "covered_call_report_latest.csv"


def update_manifest():
    report_files = sorted(
        [
            p for p in PUBLISHED_DIR.glob("covered_call_report_*.csv")
            if p.name != "covered_call_report_latest.csv"
        ],
        reverse=True,
    )

    reports = []
    for report_file in report_files:
        date_part = report_file.stem.replace("covered_call_report_", "")
        summary_file = PUBLISHED_DIR / f"weekly_summary_{date_part}.json"

        reports.append(
            {
                "date": date_part,
                "report": report_file.name,
                "summary": summary_file.name if summary_file.exists() else None,
            }
        )

    manifest = {
        "latest_report": "covered_call_report_latest.csv",
        "latest_summary": "weekly_summary_latest.json"
        if (PUBLISHED_DIR / "weekly_summary_latest.json").exists()
        else None,
        "reports": reports,
    }

    manifest_path = PUBLISHED_DIR / "reports_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Saved manifest: {manifest_path}")


def fetch_last_week_outcomes() -> list[dict]:
    """
    Pull trades from Supabase that closed in the last 14 days.
    Returns a list of outcome dicts ready to inject into the GPT prompt.
    Returns [] if Supabase is not configured or query fails.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        import urllib.request
        cutoff = (date.today() - timedelta(days=14)).isoformat()
        # Query trades closed (not open) within the last 14 days
        url = (
            f"{SUPABASE_URL}/rest/v1/trades"
            f"?select=ticker,transaction_date,strike,expiration,premium,total_premium,"
            f"contracts,status,close_date,total_pnl,assignment_price,buyback_price,notes"
            f"&status=neq.open"
            f"&close_date=gte.{cutoff}"
            f"&order=close_date.desc"
        )
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"fetch_last_week_outcomes: could not reach Supabase — {e}")
        return []


def build_outcomes_context(outcomes: list[dict]) -> str:
    """Convert Supabase trade records into a plain-English summary for the GPT prompt."""
    if not outcomes:
        return ""

    lines = ["\n\nLAST WEEK'S TRADE OUTCOMES (from brokerage records):"]
    total_pnl = 0.0
    total_premium = 0.0

    for t in outcomes:
        ticker    = t.get("ticker", "?")
        strike    = t.get("strike")
        exp       = t.get("expiration", "?")
        status    = t.get("status", "?")
        premium   = t.get("total_premium") or 0
        pnl       = t.get("total_pnl") or 0
        contracts = t.get("contracts", 1)
        assign_px = t.get("assignment_price")

        total_premium += premium
        total_pnl     += pnl

        line = f"  • {ticker} ${strike} call exp {exp} ({contracts} contract{'s' if contracts > 1 else ''}): "

        if status == "expired":
            line += f"EXPIRED WORTHLESS ✅ — full premium kept: ${premium:.2f}"
        elif status == "assigned":
            opp_cost = None
            if assign_px and strike:
                opp_cost = (assign_px - strike) * contracts * 100
            line += f"ASSIGNED at ${strike} — premium collected: ${premium:.2f}"
            if opp_cost and opp_cost > 0:
                net = premium - opp_cost
                line += (
                    f". Stock was at ${assign_px:.2f} at expiry — "
                    f"upside missed: ${opp_cost:.2f}, net opportunity cost: ${abs(net):.2f}"
                )
        elif status == "closed":
            line += f"CLOSED early — P&L: ${pnl:.2f}"
        else:
            line += f"status: {status}, P&L: ${pnl:.2f}"

        lines.append(line)

    lines.append(f"\n  WEEK TOTAL — Premium collected: ${total_premium:.2f} | Net P&L: ${total_pnl:.2f}")
    lines.append(
        "Use these outcomes to comment on what went well, what didn't, and whether the "
        "agent's recommendations were accurate. Factor this into your analysis of this week's picks."
    )
    return "\n".join(lines)


def load_owned_tickers() -> list[str]:
    """Load owned tickers from data/owned_tickers.txt (written by import_positions.py)."""
    path = Path("data/owned_tickers.txt")
    if not path.exists():
        return []
    return [t.strip().upper() for t in path.read_text(encoding="utf-8").splitlines() if t.strip()]


def main():
    if not LATEST_CSV.exists():
        print(f"No latest report found at {LATEST_CSV} — scanner may not have run yet or found no matches. Skipping summary generation.")
        sys.exit(0)

    # If the scanner ran today but produced no new dated CSV, skip to avoid generating
    # a stale summary from last week's data.
    today_csv = PUBLISHED_DIR / f"covered_call_report_{date.today().isoformat()}.csv"
    if not today_csv.exists():
        print(f"No report for today ({date.today().isoformat()}) found. Scanner likely produced no matches this week.")
        print("Skipping summary generation — last week's summary remains current.")
        sys.exit(0)

    df = pd.read_csv(LATEST_CSV)
    top10 = df.head(10).fillna("").to_dict(orient="records")
    today_str = date.today().isoformat()

    # Build owned-positions context so the AI can personalise its recommendation
    owned_tickers = load_owned_tickers()
    top10_tickers = [str(r.get("Ticker", "")).upper() for r in top10]
    owned_in_top10     = [t for t in owned_tickers if t in top10_tickers]
    owned_not_in_top10 = [t for t in owned_tickers if t not in top10_tickers]

    owned_context = ""
    if owned_tickers:
        owned_context = f"\n\nINVESTOR PORTFOLIO CONTEXT:\nThe investor currently owns: {', '.join(owned_tickers)}. For these stocks they can sell covered calls immediately — no additional capital needed to buy shares."
        if owned_in_top10:
            owned_context += f"\nOf this week's top 10, the investor already owns: {', '.join(owned_in_top10)}. These are immediately actionable and should be highlighted."
        if owned_not_in_top10:
            owned_context += f"\nThe investor also owns {', '.join(owned_not_in_top10)} which did not rank in the top 10 this week — mention this briefly."
        owned_context += "\nWhen choosing the best trade, strongly prefer owned stocks unless a non-owned stock is dramatically better on all metrics."

    # Fetch last week's trade outcomes from Supabase
    outcomes = fetch_last_week_outcomes()
    outcomes_context = build_outcomes_context(outcomes)
    if outcomes_context:
        print(f"Loaded {len(outcomes)} closed trade(s) from last week for outcome analysis.")
    else:
        print("No last-week outcomes found — summary will cover this week only.")

    client = OpenAI()

    prompt = f"""
You are an options income analyst helping choose ONE weekly covered-call trade.

I am providing the top 10 unique-stock candidates from a scanner.
Each row is already the best call contract for that stock.
{owned_context}
{outcomes_context}

Your tasks:
1. If last week's outcomes are provided, start with a plain-English recap: what happened to each trade, how much was earned, and whether any upside was missed due to assignment. Be specific with dollar amounts.
2. Explain why this week's names ranked highly.
3. Point out any names that look dangerous or less desirable.
4. Choose ONE best trade of the week — prefer stocks the investor already owns.
5. Give a concise reason for the choice, mentioning if it is an owned stock.
6. Mention the biggest risk of that choice.

Return ONLY valid JSON.
Do not include markdown fences.
Do not include commentary before or after the JSON.
Use this exact schema:
{{
  "report_date": "YYYY-MM-DD",
  "last_week_recap": "string (empty string if no outcome data provided)",
  "best_trade": {{
    "ticker": "string",
    "reason": "string",
    "biggest_risk": "string"
  }},
  "overall_summary": "string",
  "top_10_notes": [
    {{
      "ticker": "string",
      "comment": "string"
    }}
  ]
}}

Use the data below and do not invent fields.
Top 10 candidates:
{json.dumps(top10, indent=2)}
"""

    response = client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
    )

    text = (response.output_text or "").strip()

    if not text:
        raise RuntimeError("OpenAI returned empty output.")

    try:
        summary = json.loads(text)
    except json.JSONDecodeError:
        raw_path = PUBLISHED_DIR / f"weekly_summary_raw_{today_str}.txt"
        raw_path.write_text(text, encoding="utf-8")
        raise RuntimeError(
            f"OpenAI did not return valid JSON. Raw output saved to {raw_path}"
        )

    dated_summary_path = PUBLISHED_DIR / f"weekly_summary_{today_str}.json"
    latest_summary_path = PUBLISHED_DIR / "weekly_summary_latest.json"

    dated_summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    latest_summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Saved summary: {dated_summary_path}")
    print(f"Saved latest summary: {latest_summary_path}")

    update_manifest()


if __name__ == "__main__":
    main()