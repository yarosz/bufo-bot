#!/usr/bin/env python3
"""Bufo Emoji Rollout System — CLI entry point.

Sneakily upload bufo emojis to Slack on a Fibonacci daily schedule.
"""

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.bufo_rollout.manifest import (
    MANIFEST_PATH, load_manifest, save_manifest,
    find_emoji, get_batch_emojis, get_pending_in_batch,
    mark_uploaded, mark_external, mark_skipped, validate_manifest,
)
from scripts.bufo_rollout.naming import resolve_collisions
from scripts.bufo_rollout.schedule import fibonacci_schedule, assign_batches
from scripts.bufo_rollout.sync import scan_image_files, sync_new_bufos
from scripts.bufo_rollout.status import print_status, print_today, print_batch, print_schedule


IMAGE_DIR = Path("all-the-bufo")


def cmd_init(args):
    """Scan images and generate the manifest."""
    start_date = args.start_date

    # Validate date
    try:
        date.fromisoformat(start_date)
    except ValueError:
        print(f"Invalid date format: {start_date}. Use YYYY-MM-DD.")
        return 1

    print(f"Scanning {IMAGE_DIR}...")
    files = scan_image_files()
    print(f"Found {len(files)} image files.")

    if not files:
        print("No images found. Is the all-the-bufo directory present?")
        return 1

    # Generate schedule
    schedule = fibonacci_schedule(len(files))
    print(f"Generated {len(schedule)}-day Fibonacci schedule.")

    # Resolve naming collisions
    name_map = resolve_collisions(files)

    # Assign batches
    emojis = assign_batches(files, schedule, name_map)

    # Build manifest
    manifest = {
        "version": 1,
        "schedule_start_date": start_date,
        "schedule": schedule,
        "emojis": emojis,
    }

    save_manifest(manifest)
    print(f"Manifest written to {MANIFEST_PATH} ({len(emojis)} emojis).")

    # Quick validation
    issues = validate_manifest(manifest)
    if issues:
        print(f"\nValidation issues ({len(issues)}):")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    else:
        print("Validation passed.")

    return 0


def cmd_status(args):
    """Show overall rollout status."""
    manifest = load_manifest()
    print_status(manifest)
    return 0


def cmd_today(args):
    """Show today's batch."""
    manifest = load_manifest()
    print_today(manifest)
    return 0


def cmd_batch(args):
    """Show a specific batch."""
    manifest = load_manifest()
    print_batch(manifest, args.batch_num)
    return 0


def cmd_upload(args):
    """Upload a batch to Slack."""
    manifest = load_manifest()

    # Determine which batch
    if args.today:
        start = date.fromisoformat(manifest["schedule_start_date"])
        today = date.today()
        day_num = (today - start).days + 1
        if day_num < 1:
            print(f"Rollout hasn't started yet. Starts {manifest['schedule_start_date']}.")
            return 1
        batch_num = day_num
    elif args.batch is not None:
        batch_num = args.batch
    else:
        print("Specify --today or --batch N")
        return 1

    pending = get_pending_in_batch(manifest, batch_num)
    all_in_batch = get_batch_emojis(manifest, batch_num)

    if not all_in_batch:
        print(f"No emojis in batch {batch_num}.")
        return 1

    if not pending:
        print(f"Batch {batch_num}: all {len(all_in_batch)} emojis already uploaded/skipped.")
        return 0

    print(f"Batch {batch_num}: {len(pending)} pending out of {len(all_in_batch)} total.")

    if args.dry_run:
        print("\n[DRY RUN] Would upload:")
        for e in pending:
            print(f"  :{e['slack_name']}: <- {e['source_file']}")
        return 0

    # Only check upload deps when actually uploading
    from scripts.bufo_rollout.upload import check_upload_deps, load_credentials, upload_emoji

    if not check_upload_deps():
        return 1

    # Load credentials
    creds = load_credentials()
    if not creds:
        return 1
    cookie_d, workspace, token = creds

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    success = 0
    fail = 0

    for e in pending:
        file_path = IMAGE_DIR / e["source_file"]
        print(f"  Uploading :{e['slack_name']}: ... ", end="", flush=True)

        if upload_emoji(e["slack_name"], file_path, cookie_d, workspace, token):
            mark_uploaded(manifest, e["slack_name"], now)
            save_manifest(manifest)  # Crash-safe: save after each
            print("OK")
            success += 1
        else:
            print("FAILED")
            fail += 1

    print(f"\nDone. {success} uploaded, {fail} failed.")
    return 0 if fail == 0 else 1


def cmd_mark_uploaded(args):
    """Manually mark emoji(s) as uploaded."""
    manifest = load_manifest()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if args.name:
        if mark_uploaded(manifest, args.name, now):
            save_manifest(manifest)
            print(f"Marked :{args.name}: as uploaded.")
        else:
            print(f"Emoji :{args.name}: not found.")
            return 1
    elif args.batch is not None:
        pending = get_pending_in_batch(manifest, args.batch)
        if not pending:
            print(f"No pending emojis in batch {args.batch}.")
            return 0
        for e in pending:
            mark_uploaded(manifest, e["slack_name"], now)
        save_manifest(manifest)
        print(f"Marked {len(pending)} emojis in batch {args.batch} as uploaded.")
    else:
        print("Specify --name NAME or --batch N")
        return 1

    return 0


def cmd_mark_external(args):
    """Mark an emoji as uploaded by someone else."""
    manifest = load_manifest()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if mark_external(manifest, args.name, args.who, now):
        save_manifest(manifest)
        print(f"Marked :{args.name}: as uploaded by {args.who}.")
    else:
        print(f"Emoji :{args.name}: not found.")
        return 1

    return 0


def cmd_sync(args):
    """Sync new bufos from origin/main."""
    sync_new_bufos()
    return 0


def cmd_schedule(args):
    """Print the Fibonacci schedule."""
    manifest = load_manifest()
    print_schedule(manifest)
    return 0


def cmd_validate(args):
    """Validate manifest integrity."""
    manifest = load_manifest()
    issues = validate_manifest(manifest)

    if issues:
        print(f"Validation failed ({len(issues)} issues):")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    else:
        print(f"Manifest valid. {len(manifest['emojis'])} emojis, {len(manifest['schedule'])} batches.")
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Bufo Emoji Rollout System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Scan images and generate manifest")
    p_init.add_argument("--start-date", required=True, help="Rollout start date (YYYY-MM-DD)")

    # status
    sub.add_parser("status", help="Show overall rollout status")

    # today
    sub.add_parser("today", help="Show today's batch")

    # batch
    p_batch = sub.add_parser("batch", help="Show a specific batch")
    p_batch.add_argument("batch_num", type=int, help="Batch/day number")

    # upload
    p_upload = sub.add_parser("upload", help="Upload a batch to Slack")
    p_upload.add_argument("--today", action="store_true", help="Upload today's batch")
    p_upload.add_argument("--batch", type=int, help="Upload a specific batch")
    p_upload.add_argument("--dry-run", action="store_true", help="Preview without uploading")

    # mark-uploaded
    p_mark = sub.add_parser("mark-uploaded", help="Manually mark emoji(s) as uploaded")
    p_mark.add_argument("--name", help="Slack emoji name")
    p_mark.add_argument("--batch", type=int, help="Mark entire batch as uploaded")

    # mark-external
    p_ext = sub.add_parser("mark-external", help="Mark emoji as uploaded by someone else")
    p_ext.add_argument("--name", required=True, help="Slack emoji name")
    p_ext.add_argument("--who", required=True, help="Who uploaded it")

    # sync
    sub.add_parser("sync", help="Fetch origin/main and integrate new bufos")

    # schedule
    sub.add_parser("schedule", help="Print the Fibonacci schedule")

    # validate
    sub.add_parser("validate", help="Validate manifest integrity")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "init": cmd_init,
        "status": cmd_status,
        "today": cmd_today,
        "batch": cmd_batch,
        "upload": cmd_upload,
        "mark-uploaded": cmd_mark_uploaded,
        "mark-external": cmd_mark_external,
        "sync": cmd_sync,
        "schedule": cmd_schedule,
        "validate": cmd_validate,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
