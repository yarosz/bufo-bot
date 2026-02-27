"""Slack emoji upload functionality.

Vendored approach from Bear1110/slack-emoji-batch-uploader (MIT license).
Uses cookie + token auth to POST to Slack's internal emoji.add API.
"""

import os
import time
from pathlib import Path

try:
    import requests
    from dotenv import load_dotenv
    HAS_UPLOAD_DEPS = True
except ImportError:
    HAS_UPLOAD_DEPS = False


def check_upload_deps():
    """Check that upload dependencies are installed."""
    if not HAS_UPLOAD_DEPS:
        print("Upload dependencies not installed. Run:")
        print("  pip install requests backoff python-dotenv")
        return False
    return True


def load_credentials() -> tuple[str, str, str] | None:
    """Load Slack credentials from .env file.

    Returns (cookie_d, workspace, token) or None if missing.
    """
    load_dotenv()
    cookie_d = os.getenv("COOKIE_D")
    workspace = os.getenv("WORKSPACE")
    token = os.getenv("TOKEN")

    missing = []
    if not cookie_d:
        missing.append("COOKIE_D")
    if not workspace:
        missing.append("WORKSPACE")
    if not token:
        missing.append("TOKEN")

    if missing:
        print(f"Missing credentials in .env: {', '.join(missing)}")
        print("See .env.example for setup instructions.")
        return None

    return cookie_d, workspace, token


def upload_emoji(
    name: str,
    file_path: Path,
    cookie_d: str,
    workspace: str,
    token: str,
    max_retries: int = 10,
    backoff_seconds: float = 20.0,
) -> bool:
    """Upload a single emoji to Slack.

    Returns True on success, False on failure.
    """
    url = f"https://{workspace}.slack.com/api/emoji.add"

    headers = {
        "Cookie": f"d={cookie_d}",
    }

    for attempt in range(max_retries):
        with open(file_path, "rb") as f:
            data = {
                "mode": "data",
                "name": name,
                "token": token,
            }
            files = {
                "image": (file_path.name, f, _content_type(file_path)),
            }

            try:
                resp = requests.post(url, headers=headers, data=data, files=files)
                result = resp.json()
            except Exception as e:
                print(f"  Request error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(backoff_seconds)
                continue

            if result.get("ok"):
                return True

            error = result.get("error", "unknown")
            if error == "ratelimited" and attempt < max_retries - 1:
                print(f"  Rate limited, waiting {backoff_seconds}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(backoff_seconds)
                continue

            if error == "error_name_taken":
                print(f"  Emoji :{name}: already exists in workspace")
                return True  # Treat as success

            print(f"  Upload failed: {error}")
            return False

    print(f"  Max retries exceeded for :{name}:")
    return False


def _content_type(path: Path) -> str:
    """Get MIME type for an image file."""
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".gif": "image/gif",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(ext, "application/octet-stream")
