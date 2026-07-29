"""Fetch the latest Danilo IBKR picks from his public GitHub repo
and save as published/danilo_picks_latest.json for the dashboard.

No API key required — his repo is public.

Never fails the pipeline: on any error it logs a warning and keeps the
previously published file.
"""

import json
import sys
import urllib.request
from pathlib import Path
from datetime import date

ROOT = Path(__file__).parent
OUT_PATH = ROOT / "published" / "danilo_picks_latest.json"

BASE_URL = "https://raw.githubusercontent.com/dmarinb/danilo-picks/main/published"
LATEST_URL = f"{BASE_URL}/picks_ibkr_latest.json"


def fetch(week: str = None) -> dict:
    """
    Fetch Danilo's picks.
    week: "MM-DD-YYYY" for a specific week, or None for latest.
    """
    url = f"{BASE_URL}/picks_ibkr_{week}.json" if week else LATEST_URL
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read())


def sanitize(raw: dict) -> dict:
    """Normalize to a consistent schema for the dashboard."""

    TREND_MAP = {
        "alcista": "up", "bullish": "up", "up": "up", "positivo": "up",
        "bajista": "down", "bearish": "down", "down": "down", "negativo": "down",
    }
    BIAS_MAP = {
        "bullish": "long", "long": "long", "compra": "long",
        "bearish": "short", "short": "short", "venta": "short",
    }

    picks = []
    for p in raw.get("picks", []):
        if not isinstance(p, dict) or not p.get("ticker"):
            continue

        # entry_zone: prefer [entry_low, entry_high] array; fall back to string parse
        el = p.get("entry_low")
        eh = p.get("entry_high")
        if el is not None and eh is not None:
            entry_zone = [el, eh]
        else:
            entry_zone = p.get("entry_zone")  # leave as-is if already array or absent

        picks.append({
            "ticker":           str(p.get("ticker", "")).upper()[:8],
            "stock_price":      p.get("stock_price"),
            "signal":           p.get("signal", "BULLISH_CALL"),
            "bias":             BIAS_MAP.get(str(p.get("bias", "")).lower(), p.get("bias")),
            "status":           p.get("status"),
            "trend":            TREND_MAP.get(str(p.get("trend", "")).lower(), p.get("trend")),
            "rsi":              p.get("rsi"),
            "beta":             p.get("beta"),
            "entry_zone":       entry_zone,
            "our_score":        p.get("score") if p.get("score") is not None else p.get("final_score"),
            "rationale":        p.get("rationale"),
            "momentum_20d_pct": p.get("momentum_20d_pct"),
            "call_strike":      p.get("call_strike"),
            "call_expiration":  p.get("call_expiration"),
            "call_dte":         p.get("call_dte"),
            "call_cost_usd":    p.get("call_cost_usd"),
            "delta":            p.get("delta"),
        })

    return {
        "schema_version": "2.0",
        "report_date":    str(raw.get("report_date", "")),
        "source":         "danilo_ibkr_agent",
        "premium_unit":   raw.get("premium_unit", "usd_per_contract"),
        "note":           raw.get("note", ""),
        "picks":          picks,
    }


def main() -> int:
    try:
        raw     = fetch()
        payload = sanitize(raw)

        if not payload["picks"]:
            print("fetch_danilo_picks: no usable picks found; keeping previous file")
            return 0

        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(
            f"Saved Danilo picks: {OUT_PATH} "
            f"({len(payload['picks'])} picks, report_date={payload['report_date']})"
        )

        # Also save a dated copy
        report_date = payload["report_date"]
        try:
            dt = date.fromisoformat(report_date)
            dated_name = f"danilo_picks_{dt.strftime('%m-%d-%Y')}.json"
        except ValueError:
            dated_name = f"danilo_picks_{report_date}.json"

        dated_path = OUT_PATH.parent / dated_name
        dated_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Saved dated copy: {dated_path}")

    except Exception as e:
        print(f"fetch_danilo_picks: WARNING - {e}; keeping previous file")

    return 0


if __name__ == "__main__":
    sys.exit(main())
