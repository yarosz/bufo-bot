"""Pull new bufos from origin/main and integrate into manifest."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .manifest import load_manifest, save_manifest, find_emoji_by_file
from .naming import resolve_collisions


IMAGE_DIR = Path("all-the-bufo")
IMAGE_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg"}


def scan_image_files() -> list[str]:
    """Scan the all-the-bufo directory for image files. Returns filenames only."""
    files = []
    for p in sorted(IMAGE_DIR.iterdir()):
        if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file():
            files.append(p.name)
    return files


def git_fetch_and_merge() -> bool:
    """Fetch origin/main and merge into current branch.

    Returns True on success.
    """
    try:
        print("Fetching origin/main...")
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            check=True, capture_output=True, text=True,
        )
        print("Merging origin/main...")
        result = subprocess.run(
            ["git", "merge", "origin/main", "--no-edit"],
            check=True, capture_output=True, text=True,
        )
        if result.stdout.strip():
            print(result.stdout.strip())
        return True
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e.stderr.strip()}")
        return False


def sync_new_bufos(manifest_path: Path = None) -> int:
    """Sync new bufo images into the manifest.

    1. git fetch + merge origin/main
    2. Scan for images not in manifest
    3. Assign to batch 16 or overflow
    4. Returns count of new emojis added
    """
    from .manifest import MANIFEST_PATH
    path = manifest_path or MANIFEST_PATH

    # Step 1: Git fetch and merge
    if not git_fetch_and_merge():
        print("Git sync failed. You can still scan for local new files.")
        print("Continuing with local scan...")

    # Step 2: Load manifest and scan disk
    manifest = load_manifest(path)
    known_files = {e["source_file"] for e in manifest["emojis"]}
    disk_files = scan_image_files()
    new_files = [f for f in disk_files if f not in known_files]

    if not new_files:
        print("No new bufo images found.")
        return 0

    print(f"Found {len(new_files)} new bufo image(s).")

    # Step 3: Check for deleted files
    disk_set = set(disk_files)
    for emoji in manifest["emojis"]:
        if emoji["source_file"] not in disk_set:
            if emoji["status"] == "pending":
                emoji["status"] = "skipped"
                emoji["notes"] = "File deleted upstream"
                print(f"  Marked as skipped (deleted): {emoji['source_file']}")

    # Step 4: Generate names for new files, resolving collisions with existing
    all_files = [e["source_file"] for e in manifest["emojis"]] + new_files
    name_map = resolve_collisions(all_files)

    # Find last batch and its remaining capacity
    last_batch = manifest["schedule"][-1]
    last_day = last_batch["day"]
    current_in_last = sum(1 for e in manifest["emojis"] if e["batch"] == last_day)
    # The Fibonacci schedule often has slack in the last batch

    now = datetime.now(timezone.utc).isoformat()

    added = 0
    for f in new_files:
        slack_name = name_map[f]
        # Check for name collision with existing entries
        existing_names = {e["slack_name"] for e in manifest["emojis"]}
        if slack_name in existing_names:
            # Append a suffix
            i = 2
            while f"{slack_name}-{i}" in existing_names:
                i += 1
            slack_name = f"{slack_name}-{i}"

        manifest["emojis"].append({
            "source_file": f,
            "slack_name": slack_name,
            "status": "pending",
            "batch": last_day,
            "upload_date": None,
            "uploaded_by": None,
            "notes": f"added_in_sync:{now}",
        })
        last_batch["batch_size"] += 1
        last_batch["cumulative"] += 1
        added += 1
        print(f"  Added: {f} -> :{slack_name}:")

    save_manifest(manifest, path)
    print(f"Manifest updated. {added} new emoji(s) added to batch {last_day}.")
    return added
