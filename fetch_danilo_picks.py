"""Fetch the newest danilo_picks_YYYY-MM-DD.json from Danilo's shared Drive
folder and publish it as published/danilo_picks_latest.json for the dashboard.

Requires GOOGLE_API_KEY in the environment (folder is shared publicly, so an
API key is enough - no OAuth).

Never fails the pipeline: on any error it logs a warning and keeps the
previously published file.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

FOLDER_ID = "1oKjX11loeqBfiQLrZiRLMP1c96rd4cnU"
API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()

ROOT = Path(__file__).parent
OUT_PATH = ROOT / "published" / "danilo_picks_latest.json"

# Only these fields are copied through - anything else in the file is dropped.
PICK_FIELDS = {
    "ticker", "bias", "status", "stock_price", "trend", "rsi", "beta",
    "entry_zone", "our_score", "rationale",
}
DISCARD_FIELDS = {"ticker", "motivo"}


def api_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def newest_file_id() -> tuple[str, str] | None:
    query = urllib.parse.quote(f"'{FOLDER_ID}' in parents and trashed=false")
    url = (
        "https://www.googleapis.com/drive/v3/files"
        f"?q={query}&orderBy=createdTime+desc&pageSize=5"
        f"&fields=files(id,name,createdTime)&key={API_KEY}"
    )
    files = json.loads(api_get(url)).get("files", [])
    for f in files:
        if f.get("name", "").startswith("danilo_picks_") and f["name"].endswith(".json"):
            return f["id"], f["name"]
    return None


def sanitize(raw: dict) -> dict:
    """Keep only the agreed schema fields; coerce types defensively."""
    picks = []
    for p in raw.get("picks", []):
        if not isinstance(p, dict) or not p.get("ticker"):
            continue
        clean = {k: p.get(k) for k in PICK_FIELDS if k in p}
        clean["ticker"] = str(clean["ticker"]).upper()[:8]
        picks.append(clean)

    discarded = []
    for d in raw.get("descartados", []):
        if isinstance(d, dict) and d.get("ticker"):
            discarded.append({k: d.get(k) for k in DISCARD_FIELDS if k in d})

    return {
        "schema_version": str(raw.get("schema_version", "1.0")),
        "report_date": str(raw.get("report_date", "")),
        "source": "danilo_etoro_agent",
        "picks": picks,
        "descartados": discarded,
    }


def main() -> int:
    if not API_KEY:
        print("fetch_danilo_picks: GOOGLE_API_KEY not set; skipping (non-fatal)")
        return 0
    try:
        found = newest_file_id()
        if not found:
            print("fetch_danilo_picks: no danilo_picks_*.json found in folder; skipping")
            return 0
        file_id, name = found
        raw = json.loads(api_get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&key={API_KEY}"
        ))
        payload = sanitize(raw)
        if not payload["picks"] and not payload["descartados"]:
            print(f"fetch_danilo_picks: {name} has no usable picks; keeping previous file")
            return 0
        payload["source_file"] = name
        OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved Danilo picks: {OUT_PATH} ({len(payload['picks'])} picks, "
              f"{len(payload['descartados'])} discarded) from {name}")
    except Exception as e:
        print(f"fetch_danilo_picks: WARNING - {e}; keeping previous file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
