import os
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "xmarin/options-agent")

if not GITHUB_USERNAME or not GITHUB_TOKEN:
    raise RuntimeError("Missing GITHUB_USERNAME or GITHUB_TOKEN in environment")

REPO_URL = f"https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
LOCAL_PUBLISHED_DIR = Path("published")

def run(cmd, cwd=None):
    printable = " ".join(cmd).replace(GITHUB_TOKEN, "***REDACTED***")
    print("Running:", printable)
    subprocess.run(cmd, check=True, cwd=cwd)

def main():
    if not LOCAL_PUBLISHED_DIR.exists():
        raise RuntimeError("Local published/ folder does not exist")

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"

        # Clone fresh repo into temp directory
        run(["git", "clone", REPO_URL, str(repo_dir)])

        # Configure git identity
        run(["git", "config", "user.name", GITHUB_USERNAME], cwd=repo_dir)
        run(
            ["git", "config", "user.email", f"{GITHUB_USERNAME}@users.noreply.github.com"],
            cwd=repo_dir,
        )

        target_published_dir = repo_dir / "published"
        target_published_dir.mkdir(parents=True, exist_ok=True)

        # Remove old published files in clone
        for item in target_published_dir.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)

        # Copy fresh published files into clone
        for item in LOCAL_PUBLISHED_DIR.iterdir():
            src = item
            dst = target_published_dir / item.name
            if src.is_file():
                shutil.copy2(src, dst)
            elif src.is_dir():
                shutil.copytree(src, dst)

        # Commit only if there are changes
        run(["git", "add", "published/"], cwd=repo_dir)

        diff_result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_dir,
            check=False,
        )

        if diff_result.returncode == 0:
            print("No published file changes to commit.")
            return

        commit_message = f"Update published reports {date.today().isoformat()}"
        run(["git", "commit", "-m", commit_message], cwd=repo_dir)
        run(["git", "push", "origin", "main"], cwd=repo_dir)


if __name__ == "__main__":
    main()