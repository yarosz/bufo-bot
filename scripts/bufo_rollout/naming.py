"""Convert bufo image filenames to valid Slack emoji names."""

import re
import unicodedata
from pathlib import Path


def transliterate(text: str) -> str:
    """Transliterate non-ASCII characters to ASCII equivalents."""
    # Normalize unicode to decomposed form, strip combining marks
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = ""
    for ch in nfkd:
        if ord(ch) < 128:
            ascii_text += ch
        elif unicodedata.category(ch) == "Mn":
            # Skip combining marks (accents, etc.)
            continue
        # Skip other non-ASCII (curly apostrophes, etc.)
    return ascii_text


def filename_to_slack_name(filename: str) -> str:
    """Convert an image filename to a valid Slack emoji name.

    Rules:
    - Strip extension, lowercase
    - Transliterate non-ASCII
    - Replace + with -plus-
    - Replace remaining invalid chars with -
    - Collapse consecutive hyphens
    - Valid chars: [a-z0-9_-], max 100 chars
    """
    stem = Path(filename).stem.lower()
    stem = transliterate(stem)
    stem = stem.replace("+", "-plus-")
    stem = re.sub(r"[^a-z0-9_-]", "-", stem)
    stem = re.sub(r"-{2,}", "-", stem)
    stem = stem.strip("-")
    return stem[:100]


def resolve_collisions(files: list[str]) -> dict[str, str]:
    """Map filenames to Slack names, resolving collisions.

    For duplicate stems (e.g. bufo-lurk.gif and bufo-lurk.png),
    the GIF keeps the base name and the PNG gets a -static suffix.
    """
    # First pass: compute raw slack names
    raw_names: dict[str, str] = {}
    for f in files:
        raw_names[f] = filename_to_slack_name(f)

    # Find collisions: group files by their raw slack name
    from collections import defaultdict
    name_groups: dict[str, list[str]] = defaultdict(list)
    for f, name in raw_names.items():
        name_groups[name].append(f)

    # Resolve collisions
    result: dict[str, str] = {}
    for name, group in name_groups.items():
        if len(group) == 1:
            result[group[0]] = name
        else:
            # GIF keeps base name, PNG gets -static suffix
            for f in group:
                ext = Path(f).suffix.lower()
                if ext == ".gif":
                    result[f] = name
                else:
                    result[f] = f"{name}-static"

    return result
