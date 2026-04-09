# Options Agent

Batch pipeline for scanning covered call opportunities, refreshing quote overlays, generating reports, and powering the static dashboard.

## System Flow

[Scanner] → [Overlay] → [Reports] → [Published JSON] → [Dashboard]

## Local Run Order

Run the full pipeline locally with:

```bash
source venv/bin/activate
python3 -m scanner.covered_call_scanner_tradier && \
python3 generate_weekly_summary.py && \
python3 build_trade_history_json.py && \
python3 build_live_overlay.py && \
python3 publish_reports_to_github.py
deactivate


Project Files

1. scanner/covered_call_scanner_tradier.py

Main scanner and ranking engine.

Responsibilities
	•	Pulls option chains from Tradier
	•	Calculates option metrics such as:
	•	premium
	•	delta
	•	OTM %
	•	liquidity
	•	IV percentile
	•	Applies the covered call scoring model
	•	Selects the best call candidate per stock
	•	Writes base report output used by the rest of the pipeline

Outputs
	•	CSV reports
	•	base candidate dataset for downstream steps

⸻

2. build_live_overlay.py

Refreshes quote-related fields for the already selected candidates.

Responsibilities
	•	Reads scanner output
	•	Fetches updated:
	•	stock price
	•	option bid/ask
	•	Computes:
	•	premium change
	•	suggested live limit price
	•	Writes the dashboard overlay data

Output
	•	published/live_overlay.json

Important
	•	Does not re-rank or re-score contracts
	•	Only refreshes quote-related fields

⸻

3. generate_weekly_summary.py

Builds summary data for the dashboard and reports.

Responsibilities
	•	Aggregates scanner results
	•	Produces summary views such as:
	•	top picks
	•	summary metrics
	•	weekly snapshots

Outputs
	•	published/weekly_summary_latest.json
	•	dated weekly summary JSON files

⸻

4. build_trade_history_json.py

Builds the dashboard trade history dataset.

Responsibilities
	•	Reads:
	•	historical reports
	•	data/trade_history.csv
	•	Converts that information into dashboard-ready JSON

Output
	•	published/trade_history.json

⸻

5. publish_reports_to_github.py

Publishes generated output so the static dashboard can read it after deployment.

Responsibilities
	•	Takes generated reports and JSON artifacts
	•	Pushes them to GitHub
	•	Makes updated files available to the Render-hosted static site

⸻

Data Directories

6. data/

Source input files.

Files
	•	tickers.csv — list of stocks to scan
	•	trade_history.csv — tracked or manually maintained trade history

⸻

7. published/

Dashboard-facing output files.

This is the main folder the dashboard reads from.

Typical files
	•	live_overlay.json — current recommendations with refreshed quotes
	•	trade_history.json
	•	weekly_summary_latest.json
	•	reports_manifest.json

⸻

8. reports/

Historical report archive.

Contains
	•	scanner-generated CSV reports
	•	dated historical snapshots

⸻

Configuration

9. config/settings.py

Central configuration file.

May include
	•	API keys
	•	thresholds
	•	scoring settings
	•	environment-specific config

⸻

Frontend

10. dashboard.html

Static dashboard UI.

Responsibilities
	•	Loads JSON from /published/
	•	Renders:
	•	top candidates
	•	metrics
	•	rankings
	•	reports

Important
	•	Does not calculate or rank trades
	•	Only displays the most recently generated data