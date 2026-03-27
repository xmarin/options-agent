import shutil
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "site"
PUBLISHED = ROOT / "published"

if SITE.exists():
    shutil.rmtree(SITE)

SITE.mkdir(parents=True, exist_ok=True)

shutil.copy2(ROOT / "dashboard.html", SITE / "index.html")

target_published = SITE / "published"
target_published.mkdir(parents=True, exist_ok=True)

for item in PUBLISHED.iterdir():
    src = item
    dst = target_published / item.name
    if src.is_file():
        shutil.copy2(src, dst)
    elif src.is_dir():
        shutil.copytree(src, dst)