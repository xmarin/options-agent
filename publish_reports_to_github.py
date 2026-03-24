import os
import subprocess
from datetime import date
from dotenv import load_dotenv

load_dotenv()

GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "xmarin/options-agent")

if not GITHUB_USERNAME or not GITHUB_TOKEN:
    raise RuntimeError("Missing GITHUB_USERNAME or GITHUB_TOKEN in environment")

REPO_URL = f"https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"


def run(cmd: list[str]) -> None:
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    run(["git", "config", "--global", "user.name", GITHUB_USERNAME])
    run(["git", "config", "--global", "user.email", f"{GITHUB_USERNAME}@users.noreply.github.com"])
    run(["git", "remote", "set-url", "origin", REPO_URL])

    run(["git", "add", "published/"])

    diff_result = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    if diff_result.returncode == 0:
      print("No published file changes to commit.")
      return

    commit_message = f"Update published reports {date.today().isoformat()}"
    run(["git", "commit", "-m", commit_message])
    run(["git", "push", "origin", "main"])


if __name__ == "__main__":
    main()