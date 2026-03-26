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

TOKENIZED_REPO_URL = f"https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"


def run(cmd: list[str]) -> None:
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def get_remote_url() -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main() -> None:
    original_remote = get_remote_url()

    try:
        run(["git", "config", "--global", "user.name", GITHUB_USERNAME])
        run(["git", "config", "--global", "user.email", f"{GITHUB_USERNAME}@users.noreply.github.com"])
        run(["git", "remote", "set-url", "origin", TOKENIZED_REPO_URL])

        run(["git", "add", "published/"])

        diff_result = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
        if diff_result.returncode == 0:
            print("No published file changes to commit.")
            return

        commit_message = f"Update published reports {date.today().isoformat()}"
        run(["git", "commit", "-m", commit_message])
        run(["git", "push", "origin", "main"])
    finally:
        run(["git", "remote", "set-url", "origin", original_remote])


if __name__ == "__main__":
    main()