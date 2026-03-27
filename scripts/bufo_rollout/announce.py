"""Generate and interactively review batch announcements for Slack."""

import re
import subprocess
import sys
import time
from collections import defaultdict


ANNOUNCE_STYLES = [
    "a bufo weather forecast (e.g. 'Today's bufo forecast: partly ribbity with a chance of ...')",
    "a bufo museum exhibit drop (e.g. 'Now on display at the Bufo National Museum: ...')",
    "a David Attenborough wildlife documentary narration about the bufo sighting",
    "a gacha game hype announcement (e.g. 'NEW BANNER DROP! SSR bufo_wizard has entered the pool!')",
    "a breaking news bulletin from the Bufo Broadcasting Corporation",
    "a bufo horoscope or mystical prophecy",
    "an infomercial pitch for the new bufos",
    "a nature trail field guide entry about the newly spotted bufos",
    "a NEW BUFO USER GUIDE — start with a short intro like 'NEW BUFO USER GUIDE:' then suggest when and how to use each bufo based on its name (e.g. 'Use :bufo-flex: when you ship that PR at 2am. Deploy :bufo-grapes: when someone brings snacks.'). Be specific and funny about the situations.",
    "a GRAND FINALE announcement — this is the LAST batch of the initial rollout. Celebrate the full journey (1,687 bufo deployed over 16 days on a Fibonacci schedule: 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, and this final batch). Be triumphant and a little emotional. End by noting that new bufo will continue to be added as they are contributed by the community — the bufo never truly end.",
]

# Short labels for display in the style picker
STYLE_LABELS = [
    "Weather forecast",
    "Museum exhibit",
    "Attenborough documentary",
    "Gacha hype",
    "Breaking news",
    "Horoscope / prophecy",
    "Infomercial",
    "Field guide",
    "User guide",
    "Grand finale",
]


def _default_styles(batch_num: int) -> list[str]:
    """Pick 4 default styles, rotating based on batch number."""
    start = (batch_num - 1) % len(ANNOUNCE_STYLES)
    return [ANNOUNCE_STYLES[(start + i) % len(ANNOUNCE_STYLES)] for i in range(4)]


def detect_puzzle_groups(emoji_names: list[str]) -> dict[str, dict[tuple[int, int], str]]:
    """Detect multi-piece puzzle emoji (e.g. bufo-blank-stare_0_0 .. _1_1).

    Returns dict mapping base_name -> {(col, row): full_slack_name, ...}.
    Only groups with more than one piece are returned.
    """
    pattern = re.compile(r"^(.+)_(\d+)_(\d+)$")
    groups: dict[str, dict[tuple[int, int], str]] = defaultdict(dict)
    for name in emoji_names:
        m = pattern.match(name)
        if m:
            base = m.group(1)
            row, col = int(m.group(2)), int(m.group(3))
            groups[base][(col, row)] = name
    return {k: v for k, v in groups.items() if len(v) > 1}


# Separator between emoji on the same row of a puzzle grid.
# Regular space works reliably in Slack; can be swapped to test alternatives.
_PUZZLE_COL_SEP = " "


def format_puzzle_grid(pieces: dict[tuple[int, int], str], col_sep: str | None = None) -> str:
    """Format puzzle pieces as a Slack-renderable grid.

    Each row becomes a line of adjacent :emoji: references (no spaces).
    """
    max_col = max(c for c, _ in pieces)
    max_row = max(r for _, r in pieces)
    lines = []
    for row in range(max_row + 1):
        row_parts = []
        for col in range(max_col + 1):
            if (col, row) in pieces:
                row_parts.append(f":{pieces[(col, row)]}:")
        sep = col_sep if col_sep is not None else _PUZZLE_COL_SEP
        lines.append(sep.join(row_parts))
    return "\n".join(lines)


def expand_puzzle_placeholders(
    text: str,
    puzzle_groups: dict[str, dict[tuple[int, int], str]],
) -> str:
    """Replace <<base_name>> placeholders with formatted puzzle grids."""
    for base_name, pieces in puzzle_groups.items():
        placeholder = f"<<{base_name}>>"
        if placeholder in text:
            grid = format_puzzle_grid(pieces)
            text = text.replace(placeholder, f"\n{grid}\n")
    return text


def strip_puzzle_grid_spaces(text: str) -> str:
    """Remove spaces between emoji in puzzle grid rows.

    Only strips spaces from rows with exactly 2 emoji (2-wide grids).
    Wider grids keep spaces to avoid Slack rendering issues with many adjacent emoji.
    """
    two_emoji_row_re = re.compile(r"^(:[a-z0-9_-]+:) (:[a-z0-9_-]+:)$")
    lines = text.split("\n")
    result = []
    for line in lines:
        if two_emoji_row_re.match(line.strip()):
            result.append(line.replace(" :", ":"))
        else:
            result.append(line)
    return "\n".join(result)


def _find_referenced_emoji(text: str, emoji_names: list[str]) -> set[str]:
    """Find which emoji from the batch are referenced in the text."""
    return {name for name in emoji_names if f":{name}:" in text}


def _build_roll_call(
    text: str,
    emoji_names: list[str],
    puzzle_groups: dict[str, dict[tuple[int, int], str]],
    max_chars: int = 3000,
) -> list[str]:
    """Build roll call messages for any emoji not referenced in the text.

    Returns a list of message strings chunked to stay under max_chars each,
    or an empty list if all emoji are covered.
    Puzzle pieces are counted as referenced if any piece appears.
    """
    # Collect all "logical" emoji names (puzzle groups count as one)
    puzzle_piece_names: set[str] = set()
    for pieces in puzzle_groups.values():
        puzzle_piece_names.update(pieces.values())
    regular_names = [n for n in emoji_names if n not in puzzle_piece_names]

    # Check which regular emoji are referenced
    referenced = _find_referenced_emoji(text, regular_names)
    missing = [n for n in regular_names if n not in referenced]

    # Check puzzle groups — referenced if any piece or the base name appears
    for base_name, pieces in puzzle_groups.items():
        piece_names = set(pieces.values())
        if not piece_names & _find_referenced_emoji(text, list(piece_names)):
            # Puzzle not referenced — add the base as missing
            missing.append(base_name)

    if not missing:
        return []

    # Chunk into messages that stay under max_chars
    prefix = "Also new today: "
    chunks = []
    current = prefix
    for name in missing:
        token = f":{name}: "
        if len(current) + len(token) > max_chars and current != prefix:
            chunks.append(current.rstrip())
            current = ""
        current += token
    if current.strip():
        chunks.append(current.rstrip())

    return chunks


def _build_prompt(
    emoji_names: list[str],
    batch_num: int,
    batch_size: int,
    styles: list[str],
    puzzle_groups: dict[str, dict[tuple[int, int], str]] | None = None,
) -> str:
    """Build the prompt for Claude haiku to generate announcement options."""
    if puzzle_groups is None:
        puzzle_groups = {}

    # Separate puzzle pieces from regular emoji
    puzzle_piece_names: set[str] = set()
    for pieces in puzzle_groups.values():
        puzzle_piece_names.update(pieces.values())
    regular_names = [n for n in emoji_names if n not in puzzle_piece_names]

    # Build display string
    parts = []
    if regular_names:
        parts.append(", ".join(f":{n}:" for n in regular_names))
    for base_name, pieces in puzzle_groups.items():
        max_col = max(c for c, _ in pieces) + 1
        max_row = max(r for _, r in pieces) + 1
        parts.append(f":{base_name}: ({max_col}x{max_row} puzzle grid — reference it as <<{base_name}>>)")
    names_str = ", ".join(parts)

    if batch_size <= 13:
        emoji_instruction = f"Here are all the emoji in today's batch: {names_str}"
    else:
        emoji_instruction = (
            f"Here are all {batch_size} emoji in today's batch: {names_str}\n"
            "Pick out the most ridiculous or funny names to highlight."
        )

    num_styles = len(styles)
    style_options = "\n".join(
        f"  {i+1}. {style}" for i, style in enumerate(styles)
    )

    puzzle_rules = ""
    if puzzle_groups:
        puzzle_rules = (
            "\n- Some emoji form multi-part puzzle grids. Reference each puzzle using <<name>> "
            "(double angle brackets). Write your sentence so <<name>> appears where the grid "
            "should be inserted — it will be placed on its own line automatically. "
            "NEVER list the individual puzzle pieces (e.g. _0_0, _0_1) separately."
        )

    return f"""\
You are the hype writer for a Slack channel where bufo toad emoji are being rolled out daily.

{emoji_instruction}

Write exactly {num_styles} announcement options for today's batch (batch #{batch_num}).
Each one should be a different style. Use these styles:
{style_options}

Rules:
- Each announcement should be 2-4 sentences, fun and playful
- Try to weave in ALL emoji from the batch — any you miss will be appended automatically, so it's better if you include them naturally
- Reference specific emoji names from the batch using :emoji_name: format
- NEVER put quotes or apostrophes around :emoji_name: references — Slack won't render them as emoji if quoted
- Don't use headers or markdown, just plain text
- Number them 1-{num_styles}, one per line
- Don't include any other text before or after the numbered list{puzzle_rules}

Example format:
1. [announcement in style 1]
2. [announcement in style 2]
3. [announcement in style 3]
4. [announcement in style 4]"""


def generate_options(
    emoji_names: list[str],
    batch_num: int,
    batch_size: int,
    styles: list[str] | None = None,
) -> tuple[list[str], list[list[str]]]:
    """Generate announcement options via `claude -p --model sonnet`.

    Returns (options, roll_calls) where each roll_call is a list of message
    chunks for emoji the model missed, or an empty list if all were covered.
    """
    if styles is None:
        styles = _default_styles(batch_num)

    puzzle_groups = detect_puzzle_groups(emoji_names)
    prompt = _build_prompt(emoji_names, batch_num, batch_size, styles, puzzle_groups)

    timeout_secs = 1500
    try:
        proc = subprocess.Popen(
            ["claude", "-p", "--model", "sonnet"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        proc.stdin.write(prompt)
        proc.stdin.close()

        # Spinner while waiting
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        start_time = time.time()
        idx = 0
        while proc.poll() is None:
            elapsed = int(time.time() - start_time)
            sys.stderr.write(f"\r  {spinner[idx % len(spinner)]} Generating ({elapsed}s)...")
            sys.stderr.flush()
            idx += 1
            if elapsed > timeout_secs:
                proc.kill()
                sys.stderr.write("\r" + " " * 40 + "\r")
                print("  Announcement generation timed out.")
                return [], []
            time.sleep(0.15)

        sys.stderr.write("\r" + " " * 40 + "\r")  # Clear spinner line

        stdout = proc.stdout.read()
        stderr = proc.stderr.read()
    except FileNotFoundError:
        print("  'claude' CLI not found. Install it to generate announcements.")
        return [], []

    if proc.returncode != 0:
        print(f"  claude CLI error (exit {proc.returncode}): {stderr.strip()}")
        if stdout.strip():
            print(f"  stdout: {stdout.strip()[:200]}")
        return [], []

    # Parse numbered lines from output
    options = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        match = re.match(r"^\d+[\.\)]\s*(.+)$", line)
        if match:
            options.append(match.group(1))

    if not options and stdout.strip():
        print(f"  No numbered options parsed from output:")
        print(f"  {stdout.strip()[:300]}")

    # Expand puzzle grid placeholders
    if puzzle_groups:
        options = [expand_puzzle_placeholders(opt, puzzle_groups) for opt in options]

    # Build roll calls for any emoji the model missed (separate from main text)
    roll_calls = [_build_roll_call(opt, emoji_names, puzzle_groups) for opt in options]

    return options, roll_calls


def _pick_styles() -> list[str] | None:
    """Let the user pick which styles to generate from.

    Returns a list of style descriptions, or None to cancel.
    """
    print("\n  Available styles:")
    for i, label in enumerate(STYLE_LABELS, 1):
        print(f"    {i}. {label}")
    print(f"    c. Custom (describe your own style)")

    while True:
        choice = input(
            "\n  Pick up to 4 styles (e.g. '1,3,4'), 'c' for custom, or Enter for all: "
        ).strip().lower()

        if choice == "":
            # All built-in styles — haiku will get all 8 and do its best
            return list(ANNOUNCE_STYLES)

        if choice == "c":
            desc = input("  Describe your style: ").strip()
            if desc:
                return [desc]
            print("  Empty description, try again.")
            continue

        # Parse comma-separated numbers, may also include 'c'
        parts = [p.strip() for p in choice.split(",")]
        styles = []
        for p in parts:
            if p == "c":
                desc = input("  Describe your custom style: ").strip()
                if desc:
                    styles.append(desc)
                continue
            if p.isdigit() and 1 <= int(p) <= len(ANNOUNCE_STYLES):
                styles.append(ANNOUNCE_STYLES[int(p) - 1])
            else:
                print(f"  Invalid choice: {p}")
                styles = []
                break

        if styles:
            return styles


def interactive_review(
    emoji_names: list[str],
    batch_num: int,
    batch_size: int,
    existing: str | None = None,
) -> tuple[str | None, list[str]]:
    """Interactively review and select an announcement.

    If `existing` is set, offers to reuse it. Otherwise generates fresh options.
    Returns (announcement, roll_call_chunks) where roll_call_chunks is a list
    of message strings for emoji the model missed, or [] if all were covered.
    """
    if existing:
        print(f"\n  Saved announcement for batch {batch_num}:")
        print(f"    {existing}")
        while True:
            choice = input("\n  (u)se this / (r)egenerate / (n)o announcement? ").strip().lower()
            if choice == "u":
                puzzle_groups = detect_puzzle_groups(emoji_names)
                roll_call = _build_roll_call(existing, emoji_names, puzzle_groups)
                return existing, roll_call
            elif choice == "r":
                break  # Fall through to generation
            elif choice == "n":
                return None, []
            else:
                print("  Please enter 'u', 'r', or 'n'.")

    # Pick styles before first generation
    styles = _pick_styles()
    if not styles:
        return None, []

    # Generation loop
    while True:
        print("\n  Generating announcement options...", flush=True)
        result = generate_options(emoji_names, batch_num, batch_size, styles)

        if not result or not result[0]:
            print("  Could not generate options.")
            choice = input("  (r)etry / (n)o announcement? ").strip().lower()
            if choice == "r":
                continue
            return None, []

        options, roll_calls = result

        print(f"\n  Announcement options for batch {batch_num}:")
        for i, opt in enumerate(options, 1):
            lines = opt.split("\n")
            print(f"    {i}. {lines[0]}")
            for line in lines[1:]:
                print(f"       {line}")
            if roll_calls[i - 1]:
                rc = roll_calls[i - 1]
                print(f"       + roll call: {len(rc)} message(s)")

        while True:
            choice = input(
                f"\n  Pick 1-{len(options)}, (r)egenerate, (t)heme, or (n)o announcement: "
            ).strip().lower()
            if choice == "n":
                return None, []
            elif choice == "r":
                break  # Regenerate with same styles
            elif choice == "t":
                new_styles = _pick_styles()
                if new_styles:
                    styles = new_styles
                break  # Regenerate with new styles
            elif choice.isdigit() and 1 <= int(choice) <= len(options):
                idx = int(choice) - 1
                return options[idx], roll_calls[idx]
            else:
                print(f"  Please enter 1-{len(options)}, 'r', 't', or 'n'.")
        # Loop back to regenerate
