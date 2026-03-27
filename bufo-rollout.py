#!/usr/bin/env python3
"""Bufo Emoji Rollout System — CLI entry point.

Sneakily upload bufo emojis to Slack on a Fibonacci daily schedule.
"""

import argparse
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.bufo_rollout.manifest import (
    MANIFEST_PATH, load_manifest, save_manifest,
    find_emoji, get_batch_emojis, get_pending_in_batch,
    mark_uploaded, mark_external, mark_skipped, mark_pending, validate_manifest,
    get_announcement, set_announcement,
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
    else:
        print(f"Batch {batch_num}: {len(pending)} pending out of {len(all_in_batch)} total.")

    if args.dry_run:
        if pending:
            print("\n[DRY RUN] Would upload:")
            for e in pending:
                print(f"  :{e['slack_name']}: <- {e['source_file']}")
        return 0

    # Announcement review (before upload)
    announcement = None
    roll_call = None
    if not args.no_announce:
        from scripts.bufo_rollout.announce import interactive_review

        emoji_names = [e["slack_name"] for e in all_in_batch]
        existing = get_announcement(manifest, batch_num)
        announcement, roll_call = interactive_review(emoji_names, batch_num, len(all_in_batch), existing)

        if announcement:
            set_announcement(manifest, batch_num, announcement)
            save_manifest(manifest)

    # Upload pending emojis (if any)
    from scripts.bufo_rollout.upload import check_upload_deps, load_credentials, upload_emoji, BUFO_META_CHANNEL_ID, BUFO_TEST_CHANNEL_ID

    success = 0
    fail = 0

    if pending:
        if not check_upload_deps():
            return 1

        creds = load_credentials()
        if not creds:
            return 1
        cookie_d, workspace, token = creds

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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

    # Send batch announcement via bot
    if announcement:
        if not pending or success > 0:
            from scripts.bufo_rollout.announce import strip_puzzle_grid_spaces
            from scripts.bufo_rollout.upload import load_bot_token, post_message, update_message

            bot_token = load_bot_token()
            if not bot_token:
                return 1

            channel_id = BUFO_META_CHANNEL_ID if args.live else BUFO_TEST_CHANNEL_ID
            target = "#bufo-meta" if args.live else "#bufo-test"

            # Post with spaces (so Slack renders emoji), then edit to remove them
            final_text = strip_puzzle_grid_spaces(announcement)
            needs_edit = final_text != announcement

            print(f"\n  Posting announcement to {target}...")
            ts = post_message(announcement, channel_id, bot_token)
            if ts:
                print("  Announcement posted!")
                if needs_edit:
                    time.sleep(2)
                    print("  Editing to remove grid spaces...")
                    if update_message(final_text, channel_id, ts, bot_token):
                        print("  Edit applied!")
                    else:
                        print("  Edit failed (announcement still has spaces).")

                # Post roll call as separate message(s)
                for i, rc_chunk in enumerate(roll_call):
                    time.sleep(1)
                    label = f"roll call ({i+1}/{len(roll_call)})" if len(roll_call) > 1 else "roll call"
                    print(f"  Posting {label}...")
                    rc_ts = post_message(rc_chunk, channel_id, bot_token)
                    if rc_ts:
                        print(f"  {label.capitalize()} posted!")
                    else:
                        print(f"  {label.capitalize()} failed to post.")
            else:
                print("  Announcement failed to post.")

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


def cmd_rollback(args):
    """Roll back a batch: remove emojis from Slack and reset to pending."""
    manifest = load_manifest()

    batch_num = args.batch
    batch_emojis = get_batch_emojis(manifest, batch_num)

    if not batch_emojis:
        print(f"No emojis in batch {batch_num}.")
        return 1

    uploaded = [e for e in batch_emojis if e["status"] == "uploaded"]
    if not uploaded:
        print(f"Batch {batch_num}: no uploaded emojis to roll back.")
        return 0

    print(f"Batch {batch_num}: will roll back {len(uploaded)} emoji:")
    for e in uploaded:
        print(f"  :{e['slack_name']}:")

    confirm = input(f"\nRemove these {len(uploaded)} emoji from Slack and reset to pending? (y/N) ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return 0

    from scripts.bufo_rollout.upload import check_upload_deps, load_credentials, remove_emoji

    if not check_upload_deps():
        return 1

    creds = load_credentials()
    if not creds:
        return 1
    cookie_d, workspace, token = creds

    removed = 0
    failed = 0

    for e in uploaded:
        print(f"  Removing :{e['slack_name']}: ... ", end="", flush=True)
        if remove_emoji(e["slack_name"], cookie_d, workspace, token):
            mark_pending(manifest, e["slack_name"])
            save_manifest(manifest)
            print("OK")
            removed += 1
        else:
            print("FAILED")
            failed += 1

    # Clear saved announcement for this batch
    announcements = manifest.get("batch_announcements", {})
    if str(batch_num) in announcements:
        del announcements[str(batch_num)]
        save_manifest(manifest)

    print(f"\nDone. {removed} removed, {failed} failed.")
    return 0 if failed == 0 else 1


def cmd_sync(args):
    """Sync new bufos from origin/main, upload, and announce."""
    count = sync_new_bufos(auto=args.auto, live=args.live)
    if count > 0:
        print(f"\n{count} new bufo successfully deployed!")
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
    p_upload.add_argument("--no-announce", action="store_true", help="Skip announcement review")
    p_upload.add_argument("--live", action="store_true", help="Post announcement to #bufo-meta (default: #bufo-test)")

    # mark-uploaded
    p_mark = sub.add_parser("mark-uploaded", help="Manually mark emoji(s) as uploaded")
    p_mark.add_argument("--name", help="Slack emoji name")
    p_mark.add_argument("--batch", type=int, help="Mark entire batch as uploaded")

    # mark-external
    p_ext = sub.add_parser("mark-external", help="Mark emoji as uploaded by someone else")
    p_ext.add_argument("--name", required=True, help="Slack emoji name")
    p_ext.add_argument("--who", required=True, help="Who uploaded it")

    # rollback
    p_rollback = sub.add_parser("rollback", help="Remove a batch from Slack and reset to pending")
    p_rollback.add_argument("--batch", type=int, required=True, help="Batch number to roll back")

    # sync
    p_sync = sub.add_parser("sync", help="Pull new bufo from upstream, upload, and announce")
    p_sync.add_argument("--auto", action="store_true", help="Non-interactive mode (for scheduled runs)")
    p_sync.add_argument("--live", action="store_true", help="Announce to #bufo-meta (default: #bufo-test)")

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
        "rollback": cmd_rollback,
        "sync": cmd_sync,
        "schedule": cmd_schedule,
        "validate": cmd_validate,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
