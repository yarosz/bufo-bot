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


BUFO_TEST_CHANNEL_ID = os.getenv("BUFO_TEST_CHANNEL_ID", "")
BUFO_META_CHANNEL_ID = os.getenv("BUFO_META_CHANNEL_ID", "")


def notify_new_drop(message: str, channel_id: str = BUFO_TEST_CHANNEL_ID) -> bool:
    """Send a batch announcement via the webhook.

    Args:
        message: The announcement text to post.
        channel_id: Slack channel ID to post to.

    Returns True on success, False on failure.
    """
    load_dotenv()
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        print("  No WEBHOOK_URL in .env, skipping notification")
        return False

    try:
        resp = requests.post(webhook_url, json={
            "channel_id": channel_id,
            "message": message,
        })
        return resp.ok
    except Exception as e:
        print(f"  Webhook error: {e}")
        return False


def load_bot_token() -> str | None:
    """Load the BOT_TOKEN from .env."""
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("  No BOT_TOKEN in .env. See scripts/manage-slack-app.py for setup.")
    return token


def post_message(text: str, channel_id: str, bot_token: str) -> str | None:
    """Post a message to Slack via chat.postMessage.

    Returns the message 'ts' on success, None on failure.
    """
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {bot_token}"},
            json={"channel": channel_id, "text": text},
        )
        result = resp.json()
    except Exception as e:
        print(f"  Post error: {e}")
        return None

    if result.get("ok"):
        return result.get("ts")

    print(f"  Post failed: {result.get('error')}")
    return None


def update_message(text: str, channel_id: str, ts: str, bot_token: str) -> bool:
    """Update an existing Slack message via chat.update.

    Returns True on success, False on failure.
    """
    try:
        resp = requests.post(
            "https://slack.com/api/chat.update",
            headers={"Authorization": f"Bearer {bot_token}"},
            json={"channel": channel_id, "ts": ts, "text": text},
        )
        result = resp.json()
    except Exception as e:
        print(f"  Update error: {e}")
        return False

    if result.get("ok"):
        return True

    print(f"  Update failed: {result.get('error')}")
    return False


def add_reaction(channel_id: str, ts: str, reaction: str, bot_token: str) -> bool:
    """Add a reaction to a message. Returns True on success."""
    try:
        resp = requests.post(
            "https://slack.com/api/reactions.add",
            headers={"Authorization": f"Bearer {bot_token}"},
            json={"channel": channel_id, "timestamp": ts, "name": reaction},
        )
        result = resp.json()
    except Exception as e:
        print(f"  Reaction error: {e}")
        return False

    if result.get("ok"):
        return True

    print(f"  Reaction failed: {result.get('error')}")
    return False


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


def remove_emoji(
    name: str,
    cookie_d: str,
    workspace: str,
    token: str,
) -> bool:
    """Remove a single emoji from Slack.

    Returns True on success, False on failure.
    """
    url = f"https://{workspace}.slack.com/api/emoji.remove"

    headers = {
        "Cookie": f"d={cookie_d}",
    }
    data = {
        "name": name,
        "token": token,
    }

    try:
        resp = requests.post(url, headers=headers, data=data)
        result = resp.json()
    except Exception as e:
        print(f"  Request error: {e}")
        return False

    if result.get("ok"):
        return True

    error = result.get("error", "unknown")
    if error == "no_permission":
        print(f"  No permission to remove :{name}:")
        return False

    print(f"  Remove failed: {error}")
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
