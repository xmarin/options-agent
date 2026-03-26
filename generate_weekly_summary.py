import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
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
        "latest_summary": "weekly_summary_latest.json" if (PUBLISHED_DIR / "weekly_summary_latest.json").exists() else None,
        "reports": reports,
    }

    manifest_path = PUBLISHED_DIR / "reports_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Saved manifest: {manifest_path}")


def main():
    if not LATEST_CSV.exists():
        raise FileNotFoundError(f"Missing latest report: {LATEST_CSV}")

    df = pd.read_csv(LATEST_CSV)
    top10 = df.head(10).fillna("").to_dict(orient="records")
    today_str = date.today().isoformat()

    client = OpenAI()

    prompt = f"""
You are an options income analyst helping choose ONE weekly covered-call trade.

I am providing the top 10 unique-stock candidates from a scanner.
Each row is already the best call contract for that stock.

Your tasks:
1. Explain why these names ranked highly this week.
2. Point out any names that look dangerous or less desirable.
3. Choose ONE best trade of the week.
4. Give a concise reason for the choice.
5. Mention the biggest risk of that choice.

Return STRICT JSON with this exact schema:
{{
  "report_date": "YYYY-MM-DD",
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

    text = response.output_text.strip()
    summary = json.loads(text)

    dated_summary_path = PUBLISHED_DIR / f"weekly_summary_{today_str}.json"
    latest_summary_path = PUBLISHED_DIR / "weekly_summary_latest.json"

    dated_summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    latest_summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Saved summary: {dated_summary_path}")
    print(f"Saved latest summary: {latest_summary_path}")

    update_manifest()


if __name__ == "__main__":
    main()