"""Terminal status display for the bufo rollout."""

from datetime import datetime, date


def progress_bar(done: int, total: int, width: int = 40) -> str:
    """Render an ASCII progress bar."""
    if total == 0:
        return f"[{'=' * width}] 0/0"
    filled = int(width * done / total)
    bar = "=" * filled + "-" * (width - filled)
    pct = done * 100 // total
    return f"[{bar}] {done}/{total} ({pct}%)"


def print_status(manifest: dict) -> None:
    """Print overall rollout status."""
    emojis = manifest["emojis"]
    total = len(emojis)

    uploaded = sum(1 for e in emojis if e["status"] == "uploaded")
    by_others = sum(1 for e in emojis if e["status"] == "uploaded-by-others")
    skipped = sum(1 for e in emojis if e["status"] == "skipped")
    pending = sum(1 for e in emojis if e["status"] == "pending")

    done = uploaded + by_others

    print(f"\nBufo Emoji Rollout Status")
    print(f"{'=' * 50}")
    print(f"  Start date:  {manifest['schedule_start_date']}")
    print(f"  Total emojis: {total}")
    print()
    print(f"  {progress_bar(done, total)}")
    print()
    print(f"  Uploaded (by us):     {uploaded}")
    print(f"  Uploaded (by others): {by_others}")
    print(f"  Skipped:              {skipped}")
    print(f"  Pending:              {pending}")
    print()

    # Show per-batch summary
    print(f"  {'Batch':<7} {'Size':<6} {'Done':<6} {'Pending':<8} {'Status'}")
    print(f"  {'-' * 45}")
    for s in manifest["schedule"]:
        day = s["day"]
        batch_emojis = [e for e in emojis if e["batch"] == day]
        b_done = sum(1 for e in batch_emojis if e["status"] in ("uploaded", "uploaded-by-others"))
        b_pending = sum(1 for e in batch_emojis if e["status"] == "pending")
        b_total = len(batch_emojis)

        if b_done == b_total:
            status = "DONE"
        elif b_done > 0:
            status = "PARTIAL"
        else:
            status = ""

        print(f"  Day {day:<3} {b_total:<6} {b_done:<6} {b_pending:<8} {status}")
    print()


def print_today(manifest: dict) -> None:
    """Print today's batch info based on the schedule start date."""
    start = date.fromisoformat(manifest["schedule_start_date"])
    today = date.today()
    day_num = (today - start).days + 1

    if day_num < 1:
        print(f"Rollout hasn't started yet. Starts {manifest['schedule_start_date']}.")
        return

    # Find the batch for today
    batch = None
    for s in manifest["schedule"]:
        if s["day"] == day_num:
            batch = s
            break

    if not batch:
        max_day = max(s["day"] for s in manifest["schedule"])
        if day_num > max_day:
            print(f"Day {day_num}: Rollout complete! All batches have been scheduled.")
        else:
            print(f"Day {day_num}: No batch scheduled for today.")
        return

    emojis = [e for e in manifest["emojis"] if e["batch"] == day_num]
    pending = [e for e in emojis if e["status"] == "pending"]
    done = [e for e in emojis if e["status"] in ("uploaded", "uploaded-by-others")]

    print(f"\nDay {day_num} — {today.isoformat()}")
    print(f"Batch size: {len(emojis)} | Pending: {len(pending)} | Done: {len(done)}")
    print()

    for e in emojis:
        status_icon = {
            "pending": "  ",
            "uploaded": "OK",
            "uploaded-by-others": "EX",
            "skipped": "SK",
        }.get(e["status"], "??")
        print(f"  [{status_icon}] :{e['slack_name']}: ({e['source_file']})")
    print()


def print_batch(manifest: dict, batch_num: int) -> None:
    """Print contents of a specific batch."""
    emojis = [e for e in manifest["emojis"] if e["batch"] == batch_num]
    if not emojis:
        print(f"No emojis in batch {batch_num}.")
        return

    pending = [e for e in emojis if e["status"] == "pending"]
    done = [e for e in emojis if e["status"] in ("uploaded", "uploaded-by-others")]

    print(f"\nBatch {batch_num}")
    print(f"Size: {len(emojis)} | Pending: {len(pending)} | Done: {len(done)}")
    print()

    for e in emojis:
        status_icon = {
            "pending": "  ",
            "uploaded": "OK",
            "uploaded-by-others": "EX",
            "skipped": "SK",
        }.get(e["status"], "??")
        print(f"  [{status_icon}] :{e['slack_name']}: ({e['source_file']})")
    print()


def print_schedule(manifest: dict) -> None:
    """Print the full Fibonacci schedule."""
    print(f"\nBufo Rollout Schedule (start: {manifest['schedule_start_date']})")
    print(f"{'=' * 50}")
    print(f"  {'Day':<6} {'Batch Size':<12} {'Cumulative':<12}")
    print(f"  {'-' * 35}")

    for s in manifest["schedule"]:
        print(f"  {s['day']:<6} +{s['batch_size']:<11} {s['cumulative']:<12}")

    total = manifest["schedule"][-1]["cumulative"]
    print(f"\n  Total emojis: {total}")
    print(f"  Total days: {manifest['schedule'][-1]['day']}")
    print()
