import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # reads .env when running locally; env vars on Render take precedence

ROOT = Path(__file__).parent
SITE = ROOT / "site"
PUBLISHED = ROOT / "published"

if SITE.exists():
    shutil.rmtree(SITE)

SITE.mkdir(parents=True, exist_ok=True)

# Inject Supabase credentials from environment variables into the dashboard.
# Set SUPABASE_URL and SUPABASE_ANON_KEY on your Render service to enable trade logging.
dashboard_src = (ROOT / "dashboard.html").read_text(encoding="utf-8")
dashboard_src = dashboard_src.replace("%%SUPABASE_URL%%",      os.getenv("SUPABASE_URL", ""))
dashboard_src = dashboard_src.replace("%%SUPABASE_ANON_KEY%%", os.getenv("SUPABASE_ANON_KEY", ""))
(SITE / "index.html").write_text(dashboard_src, encoding="utf-8")

# Also write to repo root so Render's staticPublishPath: . serves the updated dashboard
(ROOT / "index.html").write_text(dashboard_src, encoding="utf-8")

target_published = SITE / "published"
target_published.mkdir(parents=True, exist_ok=True)

for item in PUBLISHED.iterdir():
    src = item
    dst = target_published / item.name
    if src.is_file():
        shutil.copy2(src, dst)
    elif src.is_dir():
        shutil.copytree(src, dst)