"""Pull new bufos from origin/main, integrate into manifest, upload, and announce."""

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .manifest import load_manifest, save_manifest, find_emoji_by_file
from .naming import resolve_collisions


IMAGE_DIR = Path("all-the-bufo")
IMAGE_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg"}

# Community contributions go to a special batch number
COMMUNITY_BATCH = "community"


def scan_image_files() -> list[str]:
    """Scan the all-the-bufo directory for image files. Returns filenames only."""
    files = []
    for p in sorted(IMAGE_DIR.iterdir()):
        if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file():
            files.append(p.name)
    return files


def git_pull() -> tuple[bool, list[str]]:
    """Pull latest from origin/main and return list of new files in all-the-bufo/.

    Returns (success, new_files) where new_files are paths added in the merge.
    """
    try:
        print("Fetching origin/main...")
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            check=True, capture_output=True, text=True,
        )

        # Check what will change before merging
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=A", "HEAD..origin/main", "--", "all-the-bufo/"],
            capture_output=True, text=True,
        )
        new_from_git = [
            line.replace("all-the-bufo/", "", 1)
            for line in diff_result.stdout.strip().splitlines()
            if line.startswith("all-the-bufo/")
        ]

        print("Merging origin/main...")
        result = subprocess.run(
            ["git", "merge", "origin/main", "--no-edit"],
            check=True, capture_output=True, text=True,
        )
        if result.stdout.strip():
            print(result.stdout.strip())
        return True, new_from_git
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e.stderr.strip()}")
        return False, []


def discover_new_files(manifest: dict) -> list[str]:
    """Find image files on disk that aren't in the manifest."""
    known_files = {e["source_file"] for e in manifest["emojis"]}
    disk_files = scan_image_files()
    return [f for f in disk_files if f not in known_files]


def add_to_manifest(manifest: dict, new_files: list[str]) -> list[dict]:
    """Add new files to the manifest as community contributions.

    Returns list of new emoji entries added.
    """
    # Generate names, resolving collisions with existing
    all_files = [e["source_file"] for e in manifest["emojis"]] + new_files
    name_map = resolve_collisions(all_files)

    # Find or create the community batch entry
    schedule = manifest["schedule"]
    community_entry = None
    for s in schedule:
        if s.get("day") == COMMUNITY_BATCH:
            community_entry = s
            break
    if not community_entry:
        community_entry = {"day": COMMUNITY_BATCH, "batch_size": 0, "cumulative": 0, "label": "Community contributions"}
        schedule.append(community_entry)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing_names = {e["slack_name"] for e in manifest["emojis"]}

    added = []
    for f in new_files:
        slack_name = name_map[f]
        # Resolve name collisions
        if slack_name in existing_names:
            i = 2
            while f"{slack_name}-{i}" in existing_names:
                i += 1
            slack_name = f"{slack_name}-{i}"

        entry = {
            "source_file": f,
            "slack_name": slack_name,
            "status": "pending",
            "batch": COMMUNITY_BATCH,
            "upload_date": None,
            "uploaded_by": None,
            "notes": f"community:{now}",
        }
        manifest["emojis"].append(entry)
        existing_names.add(slack_name)
        community_entry["batch_size"] += 1
        community_entry["cumulative"] += 1
        added.append(entry)
        print(f"  Added: {f} -> :{slack_name}:")

    return added


def upload_new_emoji(entries: list[dict]) -> tuple[int, int]:
    """Upload pending emoji entries to Slack.

    Returns (success_count, fail_count).
    """
    from .upload import check_upload_deps, load_credentials, upload_emoji

    if not check_upload_deps():
        return 0, len(entries)

    creds = load_credentials()
    if not creds:
        return 0, len(entries)
    cookie_d, workspace, token = creds

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    manifest = load_manifest()

    success = 0
    fail = 0
    for e in entries:
        file_path = IMAGE_DIR / e["source_file"]
        print(f"  Uploading :{e['slack_name']}: ... ", end="", flush=True)

        if upload_emoji(e["slack_name"], file_path, cookie_d, workspace, token):
            e["status"] = "uploaded"
            e["upload_date"] = now
            e["uploaded_by"] = "self"
            print("OK")
            success += 1
        else:
            print("FAILED")
            fail += 1

    return success, fail


def announce_community_drop(emoji_names: list[str], channel_id: str) -> bool:
    """Post a community drop announcement to Slack.

    Returns True if announcement posted successfully.
    """
    from .upload import load_bot_token, post_message, add_reaction

    bot_token = load_bot_token()
    if not bot_token:
        return False

    count = len(emoji_names)
    emoji_list = " ".join(f":{n}:" for n in emoji_names)

    if count == 1:
        text = f"New community bufo just dropped: {emoji_list}"
    else:
        text = f"{count} new community bufo just dropped: {emoji_list}"

    # Chunk if needed (3000 char limit per message)
    chunks = []
    prefix = text.split(": ", 1)[0] + ": "
    tokens = [f":{n}: " for n in emoji_names]

    current = prefix
    for tok in tokens:
        if len(current) + len(tok) > 3000 and current != prefix:
            chunks.append(current.rstrip())
            current = ""
        current += tok
    if current.strip():
        chunks.append(current.rstrip())

    first_ts = None
    for chunk in chunks:
        time.sleep(1)
        ts = post_message(chunk, channel_id, bot_token)
        if not ts:
            print("  Announcement failed to post.")
            return False
        if first_ts is None:
            first_ts = ts

    # React to own message
    if first_ts:
        add_reaction(channel_id, first_ts, "bufo-thanks-you-for-the-bufo", bot_token)

    return True


def sync_new_bufos(auto: bool = False, live: bool = False) -> int:
    """Sync new bufo images: pull, add to manifest, upload, and announce.

    Args:
        auto: If True, skip interactive prompts (for scheduled runs).
        live: If True, announce to #bufo-meta. Otherwise #bufo-test.

    Returns count of new emojis successfully uploaded.
    """
    from .manifest import MANIFEST_PATH
    from .upload import BUFO_META_CHANNEL_ID, BUFO_TEST_CHANNEL_ID

    # Step 1: Git pull
    git_ok, _ = git_pull()
    if not git_ok:
        print("Git sync failed. Continuing with local scan...")

    # Step 2: Load manifest and find new files
    manifest = load_manifest()
    new_files = discover_new_files(manifest)

    if not new_files:
        print("No new bufo images found.")
        return 0

    print(f"Found {len(new_files)} new bufo image(s).")

    # Step 3: Add to manifest
    new_entries = add_to_manifest(manifest, new_files)
    save_manifest(manifest)
    print(f"Manifest updated with {len(new_entries)} new emoji.")

    if not auto:
        confirm = input(f"\nUpload {len(new_entries)} new emoji to Slack? (Y/n) ").strip().lower()
        if confirm == "n":
            print("Skipped upload. New emoji are in the manifest as pending.")
            return 0

    # Step 4: Upload
    success, fail = upload_new_emoji(new_entries)
    save_manifest(manifest)
    print(f"\nUpload done. {success} uploaded, {fail} failed.")

    if success == 0:
        return 0

    # Step 5: Announce
    uploaded_names = [e["slack_name"] for e in new_entries if e["status"] == "uploaded"]
    channel_id = BUFO_META_CHANNEL_ID if live else BUFO_TEST_CHANNEL_ID
    target = "#bufo-meta" if live else "#bufo-test"

    if not auto:
        confirm = input(f"\nAnnounce {len(uploaded_names)} new emoji to {target}? (Y/n) ").strip().lower()
        if confirm == "n":
            print("Skipped announcement.")
            return success

    print(f"\n  Announcing to {target}...")
    if announce_community_drop(uploaded_names, channel_id):
        print("  Announcement posted!")
    else:
        print("  Announcement failed.")

    return success
