# Agent-to-Agent Integration Guide

This document describes how Xavier's and Danilo's trading agents share weekly pick
reports with each other using a simple, no-authentication GitHub raw URL feed.

---

## Part 1 — How to Pull Xavier's Covered Call Picks

Xavier's agent scans 288 S&P 500 stocks every Monday at 10 AM ET and publishes
the top 10 covered call picks as a JSON file to GitHub.

### Endpoints

| Method | URL |
|--------|-----|
| **Always latest** | `https://raw.githubusercontent.com/xmarin/options-agent/main/published/picks_latest.json` |
| **Specific week** | `https://raw.githubusercontent.com/xmarin/options-agent/main/published/picks_MM-DD-YYYY.json` |

Example dated URL:
```
https://raw.githubusercontent.com/xmarin/options-agent/main/published/picks_07-21-2026.json
```

No API key or authentication required — both URLs are fully public.

### Response Schema

```json
{
  "report_date": "2026-07-21",
  "week_of": "2026-07-21",
  "premium_unit": "usd_per_contract",
  "picks": [
    {
      "ticker": "META",
      "stock_price": 603.12,
      "strike": 615.0,
      "expiration": "2026-07-25",
      "dte": 4,
      "premium": 995.0,
      "hv_rank": 89.9,
      "rsi": 51.9,
      "beta": 1.58,
      "return_on_capital": 0.0165,
      "annual_yield": 0.7527,
      "pct_otm": 0.0197,
      "delta": 0.39,
      "final_score": 0.8411,
      "earnings_risk": "NO",
      "trend": "neutral"
    }
  ]
}
```

### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `ticker` | string | Stock symbol |
| `stock_price` | float | Current stock price at scan time |
| `strike` | float | Recommended call strike price |
| `expiration` | string | Option expiration date (YYYY-MM-DD) |
| `dte` | int | Days to expiration |
| `premium` | float | Option bid price × 100 (USD per contract) |
| `hv_rank` | float | Historical volatility rank 0–100 (higher = better premium) |
| `rsi` | float | RSI at scan time |
| `beta` | float | Beta vs S&P 500 |
| `return_on_capital` | float | Premium / stock price (weekly ROC) |
| `annual_yield` | float | Annualized ROC |
| `pct_otm` | float | % the strike is out of the money |
| `delta` | float | Option delta (0.15–0.40 range) |
| `final_score` | float | Composite score 0–1 (higher = stronger pick) |
| `earnings_risk` | string | "YES" if earnings fall before expiration |
| `trend` | string | "up", "down", or "neutral" |

### Sample Python Fetch

```python
import json
import urllib.request
from datetime import date

def get_xavier_picks(week: str = None) -> dict:
    """
    Fetch Xavier's covered call picks.
    week: "MM-DD-YYYY" for a specific week, or None for the latest.
    """
    if week:
        url = f"https://raw.githubusercontent.com/xmarin/options-agent/main/published/picks_{week}.json"
    else:
        url = "https://raw.githubusercontent.com/xmarin/options-agent/main/published/picks_latest.json"

    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read())

# Get latest
picks = get_xavier_picks()
print(f"Week of {picks['report_date']}: {len(picks['picks'])} picks")

# Get a specific week
picks = get_xavier_picks("07-21-2026")
```

### Detecting a New Report (Polling)

Since both agents run on Monday, check `report_date` to know if there is a new
report since your last pull rather than setting up a webhook:

```python
import json
import urllib.request
from datetime import date

LAST_SEEN_FILE = "data/xavier_last_seen.txt"

def has_new_xavier_report() -> bool:
    url = "https://raw.githubusercontent.com/xmarin/options-agent/main/published/picks_latest.json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        report_date = data["report_date"]

        # Compare against last seen date
        try:
            last_seen = open(LAST_SEEN_FILE).read().strip()
        except FileNotFoundError:
            last_seen = ""

        if report_date != last_seen:
            # Save the new date and signal there's fresh data
            open(LAST_SEEN_FILE, "w").write(report_date)
            return True
    except Exception as e:
        print(f"Warning: could not check Xavier's feed — {e}")
    return False

if has_new_xavier_report():
    picks = get_xavier_picks()
    # ... process new picks
```

---

## Part 2 — How Danilo Should Publish His Picks (Mirror Setup)

So Xavier's agent can pull Danilo's picks in the same way, Danilo needs to:

1. Push a JSON file to a public GitHub repo after each agent run
2. Follow the same naming convention: `picks_latest.json` + `picks_MM-DD-YYYY.json`

### Step 1 — Add `build_picks_json.py` to your repo

Create a file called `build_picks_json.py` in your project root. This script reads
your agent's output and writes the two JSON files to a `published/` folder.

```python
"""Build published/picks_latest.json and a dated copy for external consumers."""

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
PUBLISHED_DIR = ROOT / "published"
PUBLISHED_DIR.mkdir(exist_ok=True)

def build_picks_payload(picks: list[dict]) -> dict:
    """
    picks: list of your pick dicts. Required fields per pick:
        ticker, stock_price, strike, expiration, dte, premium,
        return_on_capital, annual_yield, final_score, trend
    Add any extra fields your agent produces — Xavier's agent will ignore unknown ones.
    """
    return {
        "report_date": date.today().isoformat(),
        "week_of": date.today().isoformat(),
        "source": "danilo_etoro_agent",
        "picks": picks,
    }

def publish(picks: list[dict]) -> None:
    payload = build_picks_payload(picks)
    payload_str = json.dumps(payload, indent=2, ensure_ascii=False)

    # Dated file — e.g. picks_07-21-2026.json
    dated = PUBLISHED_DIR / f"picks_{date.today().strftime('%m-%d-%Y')}.json"
    dated.write_text(payload_str, encoding="utf-8")
    print(f"Saved: {dated}")

    # Latest alias
    latest = PUBLISHED_DIR / "picks_latest.json"
    latest.write_text(payload_str, encoding="utf-8")
    print(f"Saved: {latest}")

if __name__ == "__main__":
    # Replace this with your real picks data
    example_picks = [
        {
            "ticker": "AAPL",
            "stock_price": 210.50,
            "strike": 215.0,
            "expiration": "2026-07-25",
            "dte": 4,
            "premium": 187.0,
            "return_on_capital": 0.0089,
            "annual_yield": 0.463,
            "final_score": 0.81,
            "trend": "up",
        }
    ]
    publish(example_picks)
```

Call `publish(your_picks_list)` at the end of your agent's weekly run.

### Step 2 — Push the published/ folder to GitHub automatically

Add this to your agent's run script (after `build_picks_json.py` runs):

```bash
git config user.name "danilo-agent"
git config user.email "danilo-agent@users.noreply.github.com"
git add published/
git commit -m "chore: publish picks $(date +%m-%d-%Y)" || echo "Nothing to commit"
git push
```

Or in Python:

```python
import subprocess
from datetime import date

def push_to_github():
    label = date.today().strftime("%m-%d-%Y")
    cmds = [
        ["git", "add", "published/"],
        ["git", "commit", "-m", f"chore: publish picks {label}"],
        ["git", "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            print(f"Warning: {' '.join(cmd)} failed — {result.stderr.strip()}")
```

For this to work on a hosted runner (e.g. Render), set a `GITHUB_TOKEN` environment
variable and use it in the remote URL:

```python
import os
repo = os.getenv("GITHUB_REPO", "danilo/my-agent")
token = os.getenv("GITHUB_TOKEN")
remote = f"https://{os.getenv('GITHUB_USERNAME')}:{token}@github.com/{repo}.git"
subprocess.run(["git", "remote", "set-url", "origin", remote])
```

### Step 3 — Make the published/ folder public

In your GitHub repo settings → make the repo **Public**, or at minimum ensure
the `published/` directory is committed on the `main` branch.

Xavier's agent will then pull your picks from:

```
https://raw.githubusercontent.com/DANILO_USERNAME/DANILO_REPO/main/published/picks_latest.json
https://raw.githubusercontent.com/DANILO_USERNAME/DANILO_REPO/main/published/picks_MM-DD-YYYY.json
```

---

## Part 3 — Webhook: Real-Time Notification When Picks Are Published

Both agents run a small webhook server (Flask) deployed as a web service on Render.
When you push new picks to GitHub, GitHub immediately POSTs to the partner's
webhook server, which fetches the new picks without waiting for Monday's cron.

### How it works

```
Xavier pushes picks → GitHub → POST /webhook/picks → Danilo's server fetches picks_latest.json
Danilo pushes picks → GitHub → POST /webhook/picks → Xavier's server fetches picks_latest.json
```

### Step 1 — Deploy the webhook server on Render

The file `webhook_server.py` is already in the repo. It is also declared in
`render.yaml` as `options-agent-webhook`, so Render will pick it up automatically.

After deploying, your webhook server URL will be:
```
https://options-agent-webhook.onrender.com/webhook/picks
```

Set these environment variables in Render → options-agent-webhook → Environment:

| Variable | Value |
|----------|-------|
| `WEBHOOK_SECRET` | Any strong random string — **must match** what you enter in GitHub |
| `PARTNER_RAW_BASE_URL` | Partner's raw GitHub base, e.g. `https://raw.githubusercontent.com/danilo/etoro-agent/main/published` |
| `PARTNER_OUTPUT_FILE` | `published/danilo_picks_latest.json` |

Verify the server is live:
```
curl https://options-agent-webhook.onrender.com/health
# → ok
```

### Step 2 — Register the webhook on the partner's GitHub repo

1. Go to **partner's GitHub repo** → Settings → Webhooks → **Add webhook**
2. Fill in:
   - **Payload URL:** `https://options-agent-webhook.onrender.com/webhook/picks`
   - **Content type:** `application/json`
   - **Secret:** the same value you set as `WEBHOOK_SECRET` in Render
   - **Which events:** select **Just the push event**
3. Click **Add webhook**
4. GitHub will send a `ping` event — your server logs should show `🏓 Webhook ping received — connection verified`

> Each agent registers the **other person's** GitHub repo, pointing at **their own** Render webhook URL.

### Step 3 — Verify end-to-end

Push a test change to `published/picks_latest.json` in the partner's repo:

```bash
# In partner's repo
echo '{}' >> published/picks_latest.json   # dummy change
git add published/picks_latest.json
git commit -m "test: webhook trigger"
git push
```

Within seconds you should see in your Render webhook service logs:
```
🔔 New picks detected from partner repo: danilo/etoro-agent
  ✅ Fetched partner picks: 2026-07-21 — 10 picks
  💾 Saved to published/danilo_picks_latest.json
  💾 Saved dated copy to published/picks_07-21-2026.json
```

### Webhook server endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness check — returns `ok` |
| `/webhook/picks` | POST | Receives GitHub push events from partner repo |

### Security

The server validates GitHub's `X-Hub-Signature-256` header on every request using
HMAC-SHA256 with your `WEBHOOK_SECRET`. Requests with missing or wrong signatures
are rejected with `403 Forbidden`. Never share or commit your `WEBHOOK_SECRET`.

### Render free-tier note

Render's free-tier web services spin down after 15 minutes of inactivity. Since
webhooks fire infrequently (once a week), use the **Starter plan ($7/mo)** for
the webhook service to keep it always-on. Alternatively, add a simple uptime
ping (e.g. via UptimeRobot — free) to keep it warm:
```
https://options-agent-webhook.onrender.com/health   # ping every 5 min
```

### Fallback: polling

If the webhook is down for any reason, the Monday cron job still works as a
fallback — it checks `report_date` on `picks_latest.json` and processes new
picks if found. The webhook is an enhancement, not a dependency.
