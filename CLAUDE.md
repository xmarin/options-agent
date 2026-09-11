# Options Agent — Claude Code Project Guide

## What This Project Is

A weekly covered-call income tool for Xavier's Charles Schwab brokerage account. It:
1. Scans ~284 S&P 500 stocks every Monday for the best covered-call opportunities
2. Generates an AI-written weekly summary via GPT-4o-mini
3. Shows a live dashboard with positions, P&L, trade history, and picks from a partner agent (Danilo)
4. Logs all trades to Supabase so outcomes can feed back into next week's analysis

**Live dashboard**: deployed as a Render static site (rebuilds on every `git push origin main`).

---

## Infrastructure

| Service | Role |
|---|---|
| **Render** (static) | Hosts `dashboard.html` as a public web page |
| **Render** (cron, Mondays 10am ET / `0 14 * * 1`) | Runs the scanner + all build scripts |
| **Render** (web, starter) | Webhook server (`webhook_server.py`) for partner integrations |
| **Supabase** | PostgreSQL DB + auth. Tables: `trades`, `positions` |
| **GitHub** (`xmarin/options-agent`, branch `main`) | Source of truth; Render auto-deploys on push |
| **Tradier API** | Options chain data for the scanner |
| **OpenAI API** (`gpt-4o-mini`) | Weekly AI summary generation |

### Render cron job sequence (every Monday)
```
python3 -m scanner.covered_call_scanner_tradier
python3 generate_weekly_summary.py
python3 build_trade_history_json.py
python3 build_live_overlay.py
python3 build_picks_json.py
python3 fetch_danilo_picks.py
python3 publish_reports_to_github.py
```

### Required env vars (set in Render dashboard, NOT committed)
- `SUPABASE_URL` — base URL only, e.g. `https://tmlpqjlnnsjbwyjvlmeo.supabase.co` (no trailing `/rest/v1/`)
- `SUPABASE_ANON_KEY` — new format starts with `sb_publishable_...`; use only `Authorization: Bearer` header (no `apikey` header)
- `TRADIER_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_MODEL` (default: `gpt-4o-mini`)

Local dev: copy to `.env` (already gitignored).

---

## Key Files

### `dashboard.html`
Single-file dashboard — all HTML, CSS, JS inline. Four tabs:

- **Weekly Picks** — loads `published/picks_latest.json` and `published/weekly_summary_latest.json`
- **My Positions** — reads live from Supabase `positions` table; shows unrealized P&L with live prices
- **P&L & Trades** — reads from Supabase `trades` table + `published/trade_history.json`
- **Danilo's Agent** — loads Danilo's picks with a **date dropdown** populated from `published/reports_manifest.json`

**Import Schwab JSON button** (`📤 Import Schwab`):
- Appears next to "+ Log Trade" only after the user signs in
- Parses Schwab `BrokerageTransactions` JSON (downloaded from Schwab → Accounts → Transaction History → Export)
- Handles covered-call trades: `Sell to Open`, `Buy to Close`, `Expired`, `Assigned`
- Handles stock position changes: `Buy` / `Sell` actions, **90-day cutoff only** (prevents old history from corrupting positions)
- Stock import uses **replace mode** (not additive) — the 90-day net becomes the absolute position for that ticker

**Critical design note on stock import**: The import replaces existing Supabase shares for any ticker that appears in the 90-day transaction window. This avoids double-counting when `reset_positions.py` was used to set a baseline. Tickers with no recent activity (e.g. AMD bought long ago) are untouched.

### `scanner/covered_call_scanner_tradier.py`
Scans S&P 500 stocks for weekly covered-call candidates. Outputs:
- `published/covered_call_report_YYYY-MM-DD.csv`
- `published/covered_call_report_latest.csv`

Filtering criteria: delta range, min bid, min OTM%, min open interest, DTE window.

### `generate_weekly_summary.py`
Reads the latest scanner CSV, fetches last 2 weeks of closed trades from Supabase, builds a GPT prompt, and saves:
- `published/weekly_summary_YYYY-MM-DD.json`
- `published/weekly_summary_latest.json`
- Updates `published/reports_manifest.json`

**Supabase query fix** (important): The URL must NOT include `/rest/v1/` — the code appends that. If you see 404s from `fetch_last_week_outcomes`, check `SUPABASE_URL` in Render env vars.

### `fetch_danilo_picks.py`
Fetches Danilo's picks from his public GitHub repo (`dmarinb/danilo-picks`), sanitizes the schema, saves:
- `published/danilo_picks_latest.json`
- `published/danilo_picks_MM-DD-YYYY.json` (dated copy)
- Updates `danilo_reports` array in `published/reports_manifest.json`

### `published/reports_manifest.json`
Central index file loaded by the dashboard. Structure:
```json
{
  "latest_report": "covered_call_report_latest.csv",
  "latest_summary": "weekly_summary_latest.json",
  "reports": [{"date": "YYYY-MM-DD", "report": "...", "summary": "..."}],
  "danilo_reports": [{"date": "YYYY-MM-DD", "file": "danilo_picks_MM-DD-YYYY.json"}]
}
```

### `scripts/reset_positions.py`
One-time script to hard-reset the Supabase `positions` table to match Schwab. Run locally when positions get corrupted. Requires Supabase email/password login (not just the anon key) to get a JWT for DELETE permissions.

**Current correct positions** (as of Sept 2026):
```python
CORRECT_POSITIONS = [
    {"ticker": "AMD",  "shares": 20,  "avg_cost": 475.91, "total_cost": 9518.20},
    {"ticker": "INTC", "shares": 100, "avg_cost": 98.48,  "total_cost": 9848.00},
    {"ticker": "NFLX", "shares": 100, "avg_cost": 94.86,  "total_cost": 9486.00},
    {"ticker": "HIMS", "shares": 200, "avg_cost": 39.46,  "total_cost": 7892.00},
]
```
Update this whenever Schwab positions change significantly, then run: `python3 scripts/reset_positions.py`

### `scripts/import_positions.py`
Alternative to `reset_positions.py` — takes a Schwab positions CSV (not JSON) and uploads to Supabase. More precise since it reads exact share counts and cost basis directly.

Usage: `python3 scripts/import_positions.py path/to/CASH-Positions-YYYY-MM-DD.csv`

### `data/owned_tickers.txt`
Plain text list (one ticker per line) of stocks Xavier currently owns. Read by the scanner to highlight owned stocks in the weekly report. Must be kept in sync with actual positions.

Current: `AMD`, `INTC`, `NFLX`, `HIMS`

---

## Supabase Schema

### `trades` table
| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `ticker` | text | e.g. `INTC` |
| `transaction_date` | date | When the call was sold |
| `strike` | numeric | Call strike price |
| `expiration` | date | Option expiration |
| `premium` | numeric | Per-share premium collected |
| `total_premium` | numeric | `premium * contracts * 100` |
| `contracts` | int | Number of contracts |
| `status` | text | `open`, `expired`, `assigned`, `closed` |
| `close_date` | date | When closed/expired/assigned |
| `total_pnl` | numeric | Net P&L |
| `assignment_price` | numeric | Stock price at assignment |
| `buyback_price` | numeric | Price paid to buy back (if closed early) |
| `notes` | text | Free text |

### `positions` table
| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `ticker` | text | e.g. `INTC` |
| `shares` | int | Current share count |
| `avg_cost` | numeric | Weighted average cost per share |
| `total_cost` | numeric | `shares * avg_cost` |

---

## Common Workflows

### After selling a covered call
Log it via "+ Log Trade" button on the dashboard (requires sign-in). The trade is inserted into Supabase `trades` with `status: open`.

### After a call expires worthless
In the P&L & Trades tab, find the trade and click Edit → set status to `expired`, add close_date. Or re-import the latest Schwab JSON — it will detect the `Expired` action and update automatically.

### After a call is assigned (shares sold)
The Schwab JSON import will detect the `Assigned` action. After import, run `reset_positions.py` to update share counts to reflect the new reality, OR import a fresh Schwab positions CSV via `import_positions.py`.

### Updating positions after buying more shares
Download the Schwab transaction history JSON and use the **📤 Import Schwab** button. Transactions within the last 90 days will update positions automatically. For older purchases or corrections, use `reset_positions.py`.

### Pushing changes to production
```bash
git add -A
git commit -m "your message"
git push origin main
```
Render auto-deploys within ~1 minute.

### If git push fails (lock file)
```bash
rm -f .git/HEAD.lock .git/index.lock
git stash
git pull --rebase origin main
git push origin main
git stash pop
```

---

## Dashboard Auth

Supabase auth gates the following:
- **My Positions** tab (reads from `positions` table)
- **P&L & Trades** tab (reads from `trades` table)
- **📤 Import Schwab** button
- **+ Log Trade** button

Weekly Picks and Danilo's Agent tabs are public (read from static JSON files, no auth needed).

The `updateAuthUI()` function in `dashboard.html` controls all show/hide logic after sign-in. If adding new auth-gated UI elements, add them there alongside `importSchwabBtn`.

---

## Partner: Danilo's Agent

Danilo (Xavier's brother) runs his own autonomous eToro stock-picking agent at GitHub repo `dmarinb/danilo-picks`. His picks are fetched every Monday by `fetch_danilo_picks.py`.

The Danilo tab has a **date dropdown** that lets Xavier browse historical picks. The dropdown is populated from `reports_manifest.json` → `danilo_reports` array. Each Monday run adds a new entry.

If Danilo's picks appear stale, check:
1. Did `fetch_danilo_picks.py` run successfully in the Monday cron log?
2. Does `https://raw.githubusercontent.com/dmarinb/danilo-picks/main/published/picks_ibkr_latest.json` have a recent `report_date`?
3. Is `published/reports_manifest.json` updated with the new entry?

---

## Known Issues / Design Decisions

- **90-day cutoff for stock imports**: Full Schwab transaction history goes back to 2022. Importing everything corrupts positions because shares acquired via spinoffs/transfers don't have Buy transactions. The 90-day window captures only genuine recent purchases.
- **Replace vs. additive import**: Stock position updates from the JSON import are now replace-mode (not additive). The 90-day net transaction count becomes the new absolute position for that ticker.
- **Supabase new key format**: Keys starting with `sb_publishable_` use only the `Authorization: Bearer` header. The old `apikey` header is for legacy `eyJ...` keys. `generate_weekly_summary.py` handles both formats.
- **Danilo tab initialized on sign-in**: `_daniloInitialized` is reset to `false` in `updateAuthUI()` when a session starts, allowing a fresh load with auth context.
