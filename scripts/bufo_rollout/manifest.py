"""CRUD operations for the bufo-manifest.json file."""

import json
from pathlib import Path

MANIFEST_PATH = Path("bufo-manifest.json")


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    """Load the manifest from disk."""
    with open(path) as f:
        return json.load(f)


def save_manifest(data: dict, path: Path = MANIFEST_PATH) -> None:
    """Save the manifest to disk."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def find_emoji(manifest: dict, slack_name: str) -> dict | None:
    """Find an emoji entry by its Slack name."""
    for e in manifest["emojis"]:
        if e["slack_name"] == slack_name:
            return e
    return None


def find_emoji_by_file(manifest: dict, source_file: str) -> dict | None:
    """Find an emoji entry by its source filename."""
    for e in manifest["emojis"]:
        if e["source_file"] == source_file:
            return e
    return None


def get_batch_emojis(manifest: dict, batch: int) -> list[dict]:
    """Get all emojis assigned to a specific batch."""
    return [e for e in manifest["emojis"] if e["batch"] == batch]


def get_pending_in_batch(manifest: dict, batch: int) -> list[dict]:
    """Get pending emojis in a specific batch."""
    return [
        e for e in manifest["emojis"]
        if e["batch"] == batch and e["status"] == "pending"
    ]


def mark_uploaded(manifest: dict, slack_name: str, upload_date: str, uploaded_by: str = "self") -> bool:
    """Mark an emoji as uploaded. Returns True if found."""
    emoji = find_emoji(manifest, slack_name)
    if emoji:
        emoji["status"] = "uploaded"
        emoji["upload_date"] = upload_date
        emoji["uploaded_by"] = uploaded_by
        return True
    return False


def mark_external(manifest: dict, slack_name: str, who: str, upload_date: str) -> bool:
    """Mark an emoji as uploaded by someone else. Returns True if found."""
    emoji = find_emoji(manifest, slack_name)
    if emoji:
        emoji["status"] = "uploaded-by-others"
        emoji["upload_date"] = upload_date
        emoji["uploaded_by"] = who
        return True
    return False


def mark_skipped(manifest: dict, slack_name: str, reason: str = None) -> bool:
    """Mark an emoji as skipped. Returns True if found."""
    emoji = find_emoji(manifest, slack_name)
    if emoji:
        emoji["status"] = "skipped"
        emoji["notes"] = reason
        return True
    return False


def validate_manifest(manifest: dict) -> list[str]:
    """Validate manifest integrity. Returns a list of issues."""
    issues = []

    # Check version
    if manifest.get("version") != 1:
        issues.append(f"Unexpected version: {manifest.get('version')}")

    # Check for duplicate slack names
    names = [e["slack_name"] for e in manifest["emojis"]]
    seen = set()
    for name in names:
        if name in seen:
            issues.append(f"Duplicate slack_name: {name}")
        seen.add(name)

    # Check batch assignments vs schedule
    schedule_days = {s["day"] for s in manifest["schedule"]}
    for e in manifest["emojis"]:
        if e["batch"] not in schedule_days:
            issues.append(f"Emoji {e['slack_name']} assigned to non-existent batch {e['batch']}")

    # Check batch sizes match schedule
    from collections import Counter
    batch_counts = Counter(e["batch"] for e in manifest["emojis"])
    for s in manifest["schedule"]:
        actual = batch_counts.get(s["day"], 0)
        if actual != s["batch_size"]:
            issues.append(
                f"Batch {s['day']}: expected {s['batch_size']} emojis, got {actual}"
            )

    # Check valid statuses
    valid_statuses = {"pending", "uploaded", "skipped", "uploaded-by-others"}
    for e in manifest["emojis"]:
        if e["status"] not in valid_statuses:
            issues.append(f"Invalid status '{e['status']}' for {e['slack_name']}")

    # Check source files exist
    image_dir = Path("all-the-bufo")
    for e in manifest["emojis"]:
        if not (image_dir / e["source_file"]).exists():
            issues.append(f"Source file missing: {e['source_file']}")

    return issues
