#!/usr/bin/env python3
"""Bufo Bot — Socket Mode listener for discovery reactions and /bufo-suggest.

Discovery: When someone reacts with any bufo emoji in a watched channel, checks
if they're already in the bufo secret channels. If not, prompts them with an
interactive button to enter the void. Only triggers for first-time reactors.

Suggest: /bufo-suggest <situation> returns the best bufo emoji for the moment,
powered by Haiku via the claude CLI.
"""

import json
import os
import random
import re
import subprocess
import sys
import time
import logging
import threading
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bufo-discovery")

# --- Config ---

BOT_TOKEN = os.getenv("BOT_TOKEN")
APP_TOKEN = os.getenv("APP_TOKEN")  # xapp- token for Socket Mode

if not BOT_TOKEN or not APP_TOKEN:
    print("Missing BOT_TOKEN or APP_TOKEN in .env")
    print("APP_TOKEN is an app-level token (xapp-) with connections:write scope.")
    print("Generate one in your Slack app settings under 'App-Level Tokens'.")
    sys.exit(1)

# Error reporting channel
BUFO_TEST_CHANNEL_ID = os.getenv("BUFO_TEST_CHANNEL_ID", "")


def report_to_test(msg: str):
    """Post a message to #bufo-test for visibility."""
    try:
        requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            json={"channel": BUFO_TEST_CHANNEL_ID, "text": msg},
        )
    except Exception:
        pass  # Best effort — don't crash the bot over reporting


def report_error(msg: str):
    """Post an error message to #bufo-test."""
    report_to_test(f":bufo-siren: {msg}")


# No hardcoded channel list needed — the bot only receives events from
# channels it's a member of. Add/remove Bufo from channels to control scope.

# Channels to invite discovered users to (in order)
BUFO_CHANNEL = os.getenv("BUFO_CHANNEL_ID", "")
BUFO_META_CHANNEL = os.getenv("BUFO_META_CHANNEL_ID", "")
INVITE_CHANNELS = [BUFO_CHANNEL, BUFO_META_CHANNEL]

# For testing, only react to events in bufo-test
BUFO_TEST_CHANNEL = os.getenv("BUFO_TEST_CHANNEL_ID", "")
TEST_MODE = os.getenv("BUFO_DISCOVERY_TEST", "").lower() in ("1", "true", "yes")

# Override invite targets for testing (comma-separated channel IDs)
TEST_INVITE_CHANNELS = os.getenv("BUFO_TEST_INVITE_CHANNELS", "").split(",") if os.getenv("BUFO_TEST_INVITE_CHANNELS") else None

# Delays (seconds) between invite steps
INVITE_DELAY = 5 if TEST_MODE else 60

# Canvas for tracking opted-out users (in #bufo-meta)
OPT_OUT_CANVAS_ID = os.getenv("OPT_OUT_CANVAS_ID", "")

# In-memory cache of opted-out user IDs (loaded from canvas on startup)
_opted_out_users: set[str] = set()

# Track how many times each user has been prompted (in-memory, resets on restart)
_prompt_counts: dict[str, int] = {}

# Users currently being invited (prevents duplicate invite threads from button mashing)
_inviting: set[str] = set()

# Users who have already responded to the current prompt (prevents button mashing)
# Values: "accepted" or "declined"
_responded: dict[str, str] = {}

# Bufo emoji prefixes that trigger discovery
BUFO_PREFIXES = ("bufo", "bigbufo", "smol-bufo", "child-bufo")

# Non-prefix bufo emoji
BUFO_EXTRAS = {
    "you-have-awoken-the-bufo", "get-in-lets-bufo", "i-dont-trust-bufo",
    "it-takes-a-bufo-to-know-a-bufo", "looks-good-to-bufo", "maam-this-is-a-bufo",
    "old-bufo-yells-at-cloud", "one-of-101-bufos", "sir-bufo-esquire",
    "sir-this-is-a-bufo", "vin-bufo", "wreck-it-bufo", "yay-bufo-1",
    "yay-bufo-2", "yay-bufo-3", "yay-bufo-4", "silver-bufo", "dalmatian-bufo",
    "doctor-bufo", "super-bufo", "senor-bufo", "house-of-bufo",
    "interdimensional-bufo-rests-atop-the-terrarium-of-existence",
    "constipated-bufo-is-trying-his-hardest", "just-hear-bufo-out-for-a-sec",
    "whatever-youre-doing-its-attracting-the-bufos",
    "with-friends-like-this-bufo-doesnt-need-enemies",
    "this-will-be-bufos-little-secret",
}


def check_canvas_access():
    """Verify the bot can access the opt-out canvas on startup."""
    try:
        resp = requests.post(
            "https://slack.com/api/canvases.sections.lookup",
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            json={"canvas_id": OPT_OUT_CANVAS_ID, "criteria": {"contains_text": "opt-out"}},
        )
        result = resp.json()
        if result.get("ok"):
            log.info("Opt-out canvas accessible (ID: %s).", OPT_OUT_CANVAS_ID)
        else:
            log.warning("Cannot access opt-out canvas: %s. Share it with the Bufo app.", result.get("error"))
    except Exception as e:
        log.warning("Failed to check opt-out canvas: %s", e)


def save_opted_out_user(user_id: str):
    """Add a user ID to the opt-out canvas and local cache."""
    _opted_out_users.add(user_id)
    try:
        resp = requests.post(
            "https://slack.com/api/canvases.edit",
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            json={
                "canvas_id": OPT_OUT_CANVAS_ID,
                "changes": [
                    {
                        "operation": "insert_at_end",
                        "document_content": {
                            "type": "markdown",
                            "markdown": f"\n{user_id}\n",
                        },
                    }
                ],
            },
        )
        result = resp.json()
        if result.get("ok"):
            log.info("Saved opt-out for %s to canvas.", user_id)
        else:
            log.warning("Failed to save opt-out: %s", result.get("error"))
    except Exception as e:
        log.warning("Failed to write opt-out canvas: %s", e)


def is_opted_out(user_id: str) -> bool:
    """Check if a user has opted out by looking up the canvas every time.

    Always checks the canvas so that removing a user ID re-enables prompts.
    """
    try:
        resp = requests.post(
            "https://slack.com/api/canvases.sections.lookup",
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            json={"canvas_id": OPT_OUT_CANVAS_ID, "criteria": {"contains_text": user_id}},
        )
        result = resp.json()
        if result.get("ok") and result.get("sections"):
            return True
    except Exception as e:
        log.warning("Failed to check opt-out canvas: %s", e)

    return False


# --- Bufo Suggest ---

MANIFEST_PATH = Path(__file__).parent.parent / "bufo-manifest.json"
DESCRIPTIONS_PATH = Path(__file__).parent.parent / "bufo-descriptions.json"

# Cached system prompt (built on startup)
_suggest_system_prompt: str = ""

# Set of all valid emoji names (for validating model output)
_valid_emoji: set[str] = set()


def load_emoji_catalog() -> str:
    """Load emoji names from the manifest and build the system prompt for suggestions."""
    global _suggest_system_prompt, _valid_emoji

    # Load manifest
    try:
        if not MANIFEST_PATH.is_file():
            raise FileNotFoundError
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log.warning("Manifest not found at %s. /bufo-suggest will not work.", MANIFEST_PATH)
        return ""

    # Load descriptions
    descriptions = {}
    try:
        with open(DESCRIPTIONS_PATH) as f:
            descriptions = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Filter to uploaded emoji, exclude puzzle pieces
    puzzle_pattern = re.compile(r"^(.+)_(\d+)_(\d+)$")
    puzzle_bases = set()
    emoji_names = []

    for e in manifest["emojis"]:
        if e["status"] != "uploaded":
            continue
        name = e["slack_name"]
        m = puzzle_pattern.match(name)
        if m:
            puzzle_bases.add(m.group(1))
            continue
        emoji_names.append(name)

    # Build emoji list with descriptions where available
    emoji_lines = []
    for name in sorted(emoji_names):
        if name in descriptions:
            emoji_lines.append(f":{name}: — {descriptions[name]}")
        else:
            emoji_lines.append(f":{name}:")

    # Add puzzle bases as non-usable references
    for base in sorted(puzzle_bases):
        emoji_lines.append(f":{base}: — (multi-piece grid, not individually usable)")

    emoji_list = "\n".join(emoji_lines)

    _suggest_system_prompt = f"""You are the bufo emoji concierge — a witty expert on the full catalog of bufo toad emoji. When given a situation, mood, or moment, you suggest the most fitting bufo emoji.

Rules:
- Suggest the best matching bufo emoji from the catalog below
- For each suggestion, show the emoji as :name: · followed by a short, funny reason (one line each)
- Suggest as many as fit naturally — usually 3-5, fewer for narrow queries, more for broad ones
- Be creative and funny with your reasons
- Only suggest emoji from the catalog — never invent names
- Do not include any preamble, headers, or closing remarks — just the suggestions

Available bufo emoji ({len(emoji_names)} total):

{emoji_list}"""

    _valid_emoji = set(emoji_names)

    log.info("Loaded %d emoji for /bufo-suggest (%d with descriptions, %d puzzle bases excluded).",
             len(emoji_names), len(descriptions), len(puzzle_bases))
    return _suggest_system_prompt


def suggest_bufo(situation: str) -> str | None:
    """Call claude CLI with haiku to get bufo suggestions.

    Returns the suggestion text, or None on error.
    """
    if not _suggest_system_prompt:
        return None

    prompt = f"{_suggest_system_prompt}\n\n---\n\nSituation: {situation}"

    try:
        proc = subprocess.Popen(
            ["claude", "-p", "--model", "haiku"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(input=prompt, timeout=30)
    except FileNotFoundError:
        log.error("claude CLI not found. /bufo-suggest will not work.")
        return None
    except subprocess.TimeoutExpired:
        proc.kill()
        log.warning("/bufo-suggest timed out after 30s.")
        return None

    if proc.returncode != 0:
        log.warning("/bufo-suggest CLI error (exit %d): %s", proc.returncode, stderr.strip()[:200])
        return None

    result = stdout.strip()
    if not result:
        log.warning("/bufo-suggest returned empty response.")
        return None

    return result


def handle_slash_command(payload: dict):
    """Handle a /bufo-suggest slash command."""
    command = payload.get("command", "")
    text = payload.get("text", "").strip()
    user_id = payload.get("user_id", "")
    response_url = payload.get("response_url", "")

    if command != "/bufo-suggest":
        return

    log.info("/bufo-suggest from %s (%d chars)", user_id, len(text))

    # Empty input — return usage hint
    if not text:
        requests.post(response_url, json={
            "response_type": "ephemeral",
            "text": ":bufo-has-a-question: Tell me what's going on and I'll find the perfect bufo.\n\nExample: `/bufo-suggest my deploy just broke production`",
        })
        return

    # Send thinking indicator
    requests.post(response_url, json={
        "response_type": "ephemeral",
        "text": f":bufo-inspecting: Finding the perfect bufo for _{text}_...",
    })

    # Strip URLs from input to prevent the model from accessing external content
    sanitized_text = re.sub(r'https?://\S+', '[link removed]', text)

    # Call Haiku in a background thread with progress updates
    def do_suggest():
        # Start the CLI call
        try:
            proc = subprocess.Popen(
                ["claude", "-p", "--model", "haiku"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            proc.stdin.write(_suggest_system_prompt + f"\n\n---\n\nSituation: {sanitized_text}")
            proc.stdin.close()
        except FileNotFoundError:
            log.error("claude CLI not found.")
            report_error("/bufo-suggest failed: `claude` CLI not found on host.")
            requests.post(response_url, json={
                "response_type": "ephemeral",
                "replace_original": True,
                "text": ":bufo-shrug: Bufo's brain is temporarily unavailable. Try again in a moment.",
            })
            return

        # Poll with progress updates using random bufo-thinks-about-* emoji
        thinks_emoji = [
            "bufo-thinks-about-a11y", "bufo-thinks-about-azure",
            "bufo-thinks-about-azure-front-door", "bufo-thinks-about-azure-front-door-intensifies",
            "bufo-thinks-about-cheeky-nandos", "bufo-thinks-about-chocolate",
            "bufo-thinks-about-climbing", "bufo-thinks-about-docs",
            "bufo-thinks-about-fishsticks", "bufo-thinks-about-mountains",
            "bufo-thinks-about-omelette", "bufo-thinks-about-pancakes",
            "bufo-thinks-about-quarter", "bufo-thinks-about-redis",
            "bufo-thinks-about-rubberduck", "bufo-thinks-about-slack",
            "bufo-thinks-about-steak", "bufo-thinks-about-steakholder",
            "bufo-thinks-about-teams", "bufo-thinks-about-telemetry",
            "bufo-thinks-about-terraform", "bufo-thinks-about-ufo",
            "bufo-thinks-about-vacation",
        ]
        shuffled = random.sample(thinks_emoji, min(3, len(thinks_emoji)))
        update_schedule = [2, 6, 14]  # exponential-ish: fast feedback, then slower
        elapsed = 0
        tick = 0
        while proc.poll() is None:
            time.sleep(1)
            elapsed += 1
            if elapsed >= 30:
                proc.kill()
                log.warning("/bufo-suggest timed out after 30s.")
                report_error("/bufo-suggest timed out after 30s.")
                requests.post(response_url, json={
                    "response_type": "ephemeral",
                    "text": ":bufo-shrug: Bufo's brain is temporarily slow. Try again in a moment.",
                })
                return
            if tick < len(update_schedule) and tick < len(shuffled) and elapsed >= update_schedule[tick]:
                resp = requests.post(response_url, json={
                    "response_type": "ephemeral",
                    "text": f":{shuffled[tick]}:",
                })
                if not resp.ok:
                    log.warning("/bufo-suggest progress update %d failed (HTTP %d): %s",
                                tick + 1, resp.status_code, resp.text[:200])
                tick += 1

        stdout = proc.stdout.read().strip()
        stderr = proc.stderr.read().strip()

        if proc.returncode != 0:
            log.warning("/bufo-suggest CLI error (exit %d): %s", proc.returncode, stderr[:200])
            report_error(f"/bufo-suggest CLI error (exit {proc.returncode})")
            requests.post(response_url, json={
                "response_type": "ephemeral",
                "replace_original": True,
                "text": ":bufo-shrug: Bufo's brain is temporarily unavailable. Try again in a moment.",
            })
            return

        if stdout:
            validated = validate_suggestions(stdout)
            requests.post(response_url, json={
                "response_type": "ephemeral",
                "text": validated or ":bufo-shrug: Bufo drew a blank. Try rephrasing?",
            })
        else:
            requests.post(response_url, json={
                "response_type": "ephemeral",
                "replace_original": True,
                "text": ":bufo-shrug: Bufo drew a blank. Try rephrasing?",
            })

    threading.Thread(target=do_suggest, daemon=True).start()


def validate_suggestions(text: str) -> str:
    """Validate emoji names in suggestions. Fuzzy-match close names, strip the rest."""
    import difflib
    if not _valid_emoji:
        return text
    valid_list = sorted(_valid_emoji)  # difflib needs a sequence
    validated_lines = []
    for line in text.strip().splitlines():
        match = re.search(r":([a-z0-9_-]+):", line)
        if match:
            name = match.group(1)
            if name in _valid_emoji:
                validated_lines.append(line)
            else:
                # Try exact match with bufo- prefix
                if f"bufo-{name}" in _valid_emoji:
                    fixed = f"bufo-{name}"
                    log.info("Prefix-matched :%s: → :%s:", name, fixed)
                    validated_lines.append(line.replace(f":{name}:", f":{fixed}:"))
                    continue

                # Try fuzzy match
                close = difflib.get_close_matches(name, valid_list, n=1, cutoff=0.6)
                if close:
                    fixed = close[0]
                    log.info("Fuzzy-matched :%s: → :%s:", name, fixed)
                    validated_lines.append(line.replace(f":{name}:", f":{fixed}:"))
                else:
                    log.warning("Stripped hallucinated emoji :%s: (no close match).", name)
        else:
            validated_lines.append(line)
    return "\n".join(validated_lines)


def is_bufo_reaction(reaction: str) -> bool:
    """Check if a reaction is a bufo emoji."""
    for prefix in BUFO_PREFIXES:
        if reaction == prefix or reaction.startswith(f"{prefix}-") or reaction.startswith(f"{prefix}_"):
            return True
    return reaction in BUFO_EXTRAS


def is_plus_one(channel_id: str, message_ts: str, reaction: str) -> bool:
    """Check if this reaction already has other users on it (plus-one).

    Returns True if someone else already reacted with the same emoji.
    """
    resp = requests.get(
        "https://slack.com/api/reactions.get",
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        params={"channel": channel_id, "timestamp": message_ts, "full": "true"},
    )
    result = resp.json()
    if not result.get("ok"):
        log.warning("reactions.get failed: %s", result.get("error"))
        return False

    message = result.get("message", {})
    for r in message.get("reactions", []):
        if r.get("name") == reaction:
            return r.get("count", 0) > 1

    return False


def slack_api(method: str, **kwargs) -> dict:
    """Call a Slack API method."""
    resp = requests.post(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        json=kwargs,
    )
    return resp.json()


def is_member(user_id: str, channel_id: str) -> bool:
    """Check if a user is already a member of a channel."""
    cursor = None
    while True:
        params = {"channel": channel_id, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            "https://slack.com/api/conversations.members",
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            params=params,
        )
        result = resp.json()
        if not result.get("ok"):
            log.warning("conversations.members failed for %s: %s", channel_id, result.get("error"))
            return False
        if user_id in result.get("members", []):
            return True
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return False


def send_discovery_prompt(user_id: str, channel_id: str, show_never_ask: bool = False) -> bool:
    """Send the interactive bufo discovery prompt with buttons."""
    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Enter the void"},
            "style": "primary",
            "action_id": "bufo_enter_void",
            "value": json.dumps({"user_id": user_id, "channel_id": channel_id}),
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Not today"},
            "action_id": "bufo_not_today",
            "value": json.dumps({"user_id": user_id, "channel_id": channel_id}),
        },
    ]

    if show_never_ask:
        buttons.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Never ask again"},
            "style": "danger",
            "action_id": "bufo_never_ask",
            "value": json.dumps({"user_id": user_id, "channel_id": channel_id}),
        })

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":you-have-awoken-the-bufo:  You found bufo.",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "There are channels that do not exist :this-will-be-bufos-little-secret:",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Will you :bufo-enter: or :bufo-exit:? The void awaits.",
            },
        },
        {"type": "actions", "elements": buttons},
    ]

    result = slack_api(
        "chat.postEphemeral",
        channel=channel_id,
        user=user_id,
        text="You found bufo. Will you enter the void?",
        blocks=blocks,
    )
    return result.get("ok", False)


def invite_to_channel(user_id: str, channel_id: str) -> bool:
    """Invite a user to a channel. Returns True on success or already_in_channel."""
    result = slack_api("conversations.invite", channel=channel_id, users=user_id)
    if result.get("ok"):
        return True
    error = result.get("error", "")
    if error in ("already_in_channel", "cant_invite_self"):
        return True
    log.warning("conversations.invite failed for %s -> %s: %s", user_id, channel_id, error)
    return False


def do_invite(user_id: str, invite_targets: list[str]):
    """Invite a user to channels with delays between each."""
    for ch in invite_targets:
        if not is_member(user_id, ch):
            time.sleep(INVITE_DELAY)
            log.info("Inviting %s to %s", user_id, ch)
            invite_to_channel(user_id, ch)
    log.info("Discovery complete for %s", user_id)


def handle_reaction(event: dict):
    """Handle a reaction_added event."""
    reaction = event.get("reaction", "")
    user_id = event.get("user", "")
    item = event.get("item", {})
    channel_id = item.get("channel", "")
    message_ts = item.get("ts", "")

    if not is_bufo_reaction(reaction):
        return

    if TEST_MODE:
        if channel_id != BUFO_TEST_CHANNEL:
            return
        log.info("TEST MODE: :%s: reaction from %s in bufo-test", reaction, user_id)
    else:
        log.info(":%s: reaction from %s in %s", reaction, user_id, channel_id)

    # Check if user has permanently opted out
    if is_opted_out(user_id):
        log.info("User %s has opted out, skipping.", user_id)
        return

    # Check if this is a plus-one
    if is_plus_one(channel_id, message_ts, reaction):
        log.info("Plus-one detected for :%s:, skipping.", reaction)
        return

    # Determine invite targets
    invite_targets = TEST_INVITE_CHANNELS if (TEST_MODE and TEST_INVITE_CHANNELS) else INVITE_CHANNELS

    # Check if user is already in all target channels
    already_in = [ch for ch in invite_targets if is_member(user_id, ch)]

    if len(already_in) == len(invite_targets):
        log.info("User %s already in all bufo channels, skipping.", user_id)
        return

    # Track prompt count — show "Never ask again" after first decline
    _prompt_counts[user_id] = _prompt_counts.get(user_id, 0) + 1
    show_never = _prompt_counts[user_id] > 1

    # Send the interactive prompt
    _responded.pop(user_id, None)
    log.info("Sending discovery prompt to %s (prompt #%d)", user_id, _prompt_counts[user_id])
    send_discovery_prompt(user_id, channel_id, show_never_ask=show_never)


def handle_interaction(payload: dict):
    """Handle an interactive button click."""
    actions = payload.get("actions", [])
    if not actions:
        return

    action = actions[0]
    action_id = action.get("action_id", "")
    user_id = payload.get("user", {}).get("id", "")

    try:
        value = json.loads(action.get("value", "{}"))
    except json.JSONDecodeError:
        return

    # Guard against button mashing on discovery buttons
    # _responded tracks: None = no response yet, "declined" = said not today, "accepted" = entering void
    prev = _responded.get(user_id)

    if action_id in ("bufo_enter_void", "bufo_not_today", "bufo_never_ask"):
        channel_id = value.get("channel_id", "")

        if prev == "accepted":
            # Already entering the void — any further clicks get this
            log.info("User %s already accepted, ignoring duplicate click.", user_id)
            if channel_id:
                slack_api(
                    "chat.postEphemeral",
                    channel=channel_id,
                    user=user_id,
                    text=":bufo-shaking-head: Too late. The void has already claimed you.",
                )
            return

        if prev == "declined" and action_id in ("bufo_not_today", "bufo_never_ask"):
            # Declined again — silently ignore
            log.info("User %s declined again, silently ignoring.", user_id)
            return

        if prev == "declined" and action_id == "bufo_enter_void":
            # Changed mind from decline to accept
            log.info("User %s changed their mind — now entering the void!", user_id)
            if channel_id:
                slack_api(
                    "chat.postEphemeral",
                    channel=channel_id,
                    user=user_id,
                    text=":bufo-reverse: Bufo knew you'd come around.",
                )
            _responded[user_id] = "accepted"
            # Fall through to the invite logic below

    if action_id == "bufo_enter_void":
        if user_id in _inviting:
            log.info("User %s already being invited, ignoring duplicate click.", user_id)
            channel_id = value.get("channel_id", "")
            if channel_id:
                slack_api(
                    "chat.postEphemeral",
                    channel=channel_id,
                    user=user_id,
                    text=":bufo-shaking-head: Too late. The void has already claimed you.",
                )
            return

        log.info("User %s clicked 'Enter the void' — accepted invite (prompt #%d)",
                 user_id, _prompt_counts.get(user_id, 0))
        _responded[user_id] = "accepted"
        _inviting.add(user_id)

        # Determine invite targets
        invite_targets = TEST_INVITE_CHANNELS if (TEST_MODE and TEST_INVITE_CHANNELS) else INVITE_CHANNELS

        # Replace the prompt with confirmation
        channel_id = value.get("channel_id", "")
        if channel_id:
            slack_api(
                "chat.postEphemeral",
                channel=channel_id,
                user=user_id,
                text=":bufo-enters-the-void: The void welcomes you...",
            )

        # Invite in a background thread (so we don't block the ack)
        def invite_and_cleanup():
            do_invite(user_id, invite_targets)
            _inviting.discard(user_id)

        threading.Thread(target=invite_and_cleanup, daemon=True).start()

    elif action_id == "bufo_not_today":
        log.info("User %s clicked 'Not today' — declined invite (prompt #%d)",
                 user_id, _prompt_counts.get(user_id, 0))
        _responded[user_id] = "declined"

        channel_id = value.get("channel_id", "")
        if channel_id:
            slack_api(
                "chat.postEphemeral",
                channel=channel_id,
                user=user_id,
                text=":bufo-wave: The void will wait. Bufo is patient.",
            )

    elif action_id == "bufo_never_ask":
        log.info("User %s clicked 'Never ask again' — opted out permanently (prompt #%d)",
                 user_id, _prompt_counts.get(user_id, 0))
        _responded[user_id] = "declined"
        save_opted_out_user(user_id)

        channel_id = value.get("channel_id", "")
        if channel_id:
            slack_api(
                "chat.postEphemeral",
                channel=channel_id,
                user=user_id,
                text=":bufo-wave: Understood. Bufo will not ask again.",
            )


def run_socket_mode():
    """Connect to Slack via Socket Mode and listen for events."""
    try:
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.request import SocketModeRequest
        from slack_sdk.socket_mode.response import SocketModeResponse
    except ImportError:
        print("slack_sdk not installed. Run:")
        print("  pip install slack_sdk")
        sys.exit(1)

    client = SocketModeClient(app_token=APP_TOKEN)

    def process(client: SocketModeClient, req: SocketModeRequest):
        # Acknowledge immediately
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        try:
            if req.type == "events_api":
                event = req.payload.get("event", {})
                if event.get("type") == "reaction_added":
                    handle_reaction(event)

            elif req.type == "interactive":
                handle_interaction(req.payload)

            elif req.type == "slash_commands":
                handle_slash_command(req.payload)
        except Exception as e:
            log.exception("Unhandled error processing %s event", req.type)
            report_error(f"Unhandled error in {req.type}: {e}")

    client.socket_mode_request_listeners.append(process)

    mode = "TEST MODE (bufo-test only)" if TEST_MODE else "LIVE"
    log.info("Bufo Discovery Bot starting in %s mode...", mode)
    log.info("Monitoring all channels Bufo is a member of, all bufo emoji, plus-one filtering ON")
    check_canvas_access()
    load_emoji_catalog()
    client.connect()
    log.info("Connected! Listening for bufo reactions and /bufo-suggest...")
    report_to_test(f":bufo-standing: Bufo bot started ({mode}).")

    # Keep alive and monitor connection
    last_connected = time.time()
    was_disconnected = False
    try:
        while True:
            time.sleep(5)
            connected = client.is_connected()
            if connected and was_disconnected:
                gap = time.time() - last_connected
                mins = int(gap // 60)
                secs = int(gap % 60)
                gap_str = f"{mins}m {secs}s" if mins else f"{secs}s"
                log.info("Reconnected after %s dark.", gap_str)
                report_to_test(f":bufo-sleep: Bufo was offline for {gap_str}. Back now.")
                was_disconnected = False
            if connected:
                last_connected = time.time()
            elif not was_disconnected:
                log.warning("Connection lost. Waiting for auto-reconnect...")
                was_disconnected = True
    except KeyboardInterrupt:
        log.info("Shutting down.")
        report_to_test(":bufo-exit: Bufo bot shutting down.")
        client.close()
    except Exception as e:
        log.exception("Bufo bot crashed.")
        report_to_test(f":bufo-siren: Bufo bot crashed: {e}")
        client.close()


if __name__ == "__main__":
    run_socket_mode()
