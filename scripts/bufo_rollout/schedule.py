"""Fibonacci schedule generation and batch assignment logic."""

import re
import random
from pathlib import Path

from .curated import CURATED_DAYS, CURATED_FILES
from .naming import resolve_collisions


def fibonacci_schedule(total: int) -> list[dict]:
    """Generate a Fibonacci release schedule to cover `total` emojis.

    Returns a list of dicts: {day, batch_size, cumulative}.
    The last batch is a remainder if Fibonacci sum exceeds total.
    """
    fibs = [1, 1]
    while sum(fibs) < total:
        fibs.append(fibs[-1] + fibs[-2])

    schedule = []
    cumulative = 0
    for i, size in enumerate(fibs, start=1):
        remaining = total - cumulative
        actual_size = min(size, remaining)
        cumulative += actual_size
        schedule.append({
            "day": i,
            "batch_size": actual_size,
            "cumulative": cumulative,
        })
        if cumulative >= total:
            break

    return schedule


def detect_multipart_groups(files: list[str]) -> dict[str, list[str]]:
    """Detect multi-part emoji groups like bigbufo_0_0.png.

    Pattern: {name}_{digit}_{digit}.{ext}
    Returns: {group_name: [filenames sorted]}
    """
    pattern = re.compile(r"^(.+)_(\d+)_(\d+)\.(png|gif|jpg)$")
    groups: dict[str, list[str]] = {}

    for f in files:
        m = pattern.match(f)
        if m:
            group_name = m.group(1)
            groups.setdefault(group_name, []).append(f)

    # Sort each group for deterministic ordering
    for name in groups:
        groups[name].sort()

    return groups


def assign_batches(
    all_files: list[str],
    schedule: list[dict],
    name_map: dict[str, str],
) -> list[dict]:
    """Assign all emoji files to batches.

    Returns a list of emoji dicts for the manifest.

    Assignment order:
    1. Curated starters fill days 1-5
    2. Multi-part groups placed in first batch large enough (day 6+)
    3. Remaining emojis shuffled (seed=42) and fill remaining slots
    """
    # Track remaining capacity per batch
    capacity = {entry["day"]: entry["batch_size"] for entry in schedule}
    assignments: dict[str, int] = {}  # filename -> batch day
    assigned_files: set[str] = set()

    # Step 1: Curated starters
    for day, filenames in CURATED_DAYS:
        for f in filenames:
            assignments[f] = day
            assigned_files.add(f)
            capacity[day] -= 1

    # Step 2: Multi-part groups
    multipart = detect_multipart_groups(all_files)
    multipart_files: set[str] = set()
    for group_name, group_files in sorted(multipart.items()):
        multipart_files.update(group_files)
        group_size = len(group_files)

        # Skip any already assigned (shouldn't happen with curated)
        unassigned = [f for f in group_files if f not in assigned_files]
        if not unassigned:
            continue

        needed = len(unassigned)
        # Find first batch from day 6 onward with enough capacity
        placed = False
        for entry in schedule:
            if entry["day"] < 6:
                continue
            if capacity[entry["day"]] >= needed:
                for f in unassigned:
                    assignments[f] = entry["day"]
                    assigned_files.add(f)
                    capacity[entry["day"]] -= 1
                placed = True
                break

        if not placed:
            # Fallback: place in last batch
            last_day = schedule[-1]["day"]
            for f in unassigned:
                assignments[f] = last_day
                assigned_files.add(f)
                capacity[last_day] -= 1

    # Step 3: Remaining emojis, shuffled deterministically
    remaining = [f for f in all_files if f not in assigned_files]
    rng = random.Random(42)
    rng.shuffle(remaining)

    # Fill batches in order
    batch_idx = 0
    for f in remaining:
        # Find next batch with capacity
        while batch_idx < len(schedule) and capacity[schedule[batch_idx]["day"]] <= 0:
            batch_idx += 1
        if batch_idx >= len(schedule):
            # Shouldn't happen if schedule covers all files
            raise ValueError(f"No batch capacity left for {f}")
        day = schedule[batch_idx]["day"]
        assignments[f] = day
        capacity[day] -= 1

    # Build emoji list
    emojis = []
    for f in sorted(all_files):
        emojis.append({
            "source_file": f,
            "slack_name": name_map[f],
            "status": "pending",
            "batch": assignments[f],
            "upload_date": None,
            "uploaded_by": None,
            "notes": None,
        })

    return emojis
