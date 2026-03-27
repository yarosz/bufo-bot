#!/usr/bin/env python3
"""Manage the Bufo Slack app via the App Manifest APIs.

Usage:
  python scripts/manage-slack-app.py validate
  python scripts/manage-slack-app.py create
  python scripts/manage-slack-app.py update --app-id A0XXXXXXXXX
  python scripts/manage-slack-app.py export --app-id A0XXXXXXXXX
  python scripts/manage-slack-app.py rotate   # rotate config tokens

Config tokens are read from 1Password via `op` CLI.
Set OP_SLACK_ACCESS_TOKEN and OP_SLACK_REFRESH_TOKEN env vars to override the default vault paths.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MANIFEST_PATH = Path(__file__).parent / "slack-app-manifest.json"
SLACK_API = "https://slack.com/api"

# 1Password references — set these to your own op:// paths
OP_ACCESS_TOKEN = os.getenv("OP_SLACK_ACCESS_TOKEN", "")
OP_REFRESH_TOKEN = os.getenv("OP_SLACK_REFRESH_TOKEN", "")


def op_read(ref: str) -> str:
    """Read a secret from 1Password CLI."""
    try:
        result = subprocess.run(
            ["op", "read", ref],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"  op read failed: {result.stderr.strip()}")
            sys.exit(1)
        return result.stdout.strip()
    except FileNotFoundError:
        print("  1Password CLI (op) not found. Install it first.")
        sys.exit(1)


def op_write(ref: str, value: str):
    """Write a secret to 1Password CLI."""
    # Parse the reference: op://vault/item/field
    parts = ref.replace("op://", "").split("/")
    vault, item, field = parts[0], parts[1], parts[2]
    subprocess.run(
        ["op", "item", "edit", item, f"{field}={value}", f"--vault={vault}"],
        capture_output=True, text=True, timeout=15,
    )


def get_config_token() -> str:
    """Get the configuration access token from 1Password."""
    return op_read(OP_ACCESS_TOKEN)


def load_manifest() -> dict:
    """Load the manifest JSON."""
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def slack_api(method: str, token: str, **kwargs) -> dict:
    """Call a Slack API method."""
    import requests
    resp = requests.post(
        f"{SLACK_API}/{method}",
        headers={"Authorization": f"Bearer {token}"},
        json=kwargs,
    )
    return resp.json()


def cmd_validate(args):
    """Validate the manifest against Slack's schema."""
    token = get_config_token()
    manifest = load_manifest()

    result = slack_api("apps.manifest.validate", token, manifest=manifest)

    if result.get("ok"):
        print("Manifest is valid.")
    else:
        print(f"Validation failed: {result.get('error')}")
        for err in result.get("errors", []):
            print(f"  {err.get('field', '?')}: {err.get('message')}")
        return 1
    return 0


def cmd_create(args):
    """Create a new Slack app from the manifest."""
    token = get_config_token()
    manifest = load_manifest()

    print("Creating app from manifest...")
    result = slack_api("apps.manifest.create", token, manifest=manifest)

    if result.get("ok"):
        app_id = result.get("app_id")
        creds = result.get("credentials", {})
        print(f"\nApp created successfully!")
        print(f"  App ID: {app_id}")
        if creds:
            print(f"  Client ID: {creds.get('client_id')}")
            print(f"  Client Secret: {creds.get('client_secret')}")
            print(f"  Signing Secret: {creds.get('signing_secret')}")
        print(f"\nNext steps:")
        print(f"  1. Go to https://api.slack.com/apps/{app_id}/install-on-team")
        print(f"  2. Install the app to your workspace")
        print(f"  3. Copy the Bot User OAuth Token (xoxb-...)")
        print(f"  4. Add BOT_TOKEN=xoxb-... to your .env file")
        print(f"  5. Set the app icon at https://api.slack.com/apps/{app_id}/general")
    else:
        print(f"Create failed: {result.get('error')}")
        for err in result.get("errors", []):
            print(f"  {err.get('field', '?')}: {err.get('message')}")
        return 1
    return 0


def cmd_update(args):
    """Update an existing app's manifest."""
    token = get_config_token()
    manifest = load_manifest()

    print(f"Updating app {args.app_id}...")
    result = slack_api("apps.manifest.update", token,
                       app_id=args.app_id, manifest=manifest)

    if result.get("ok"):
        print("App updated successfully.")
    else:
        print(f"Update failed: {result.get('error')}")
        for err in result.get("errors", []):
            print(f"  {err.get('field', '?')}: {err.get('message')}")
        return 1
    return 0


def cmd_export(args):
    """Export an existing app's manifest."""
    token = get_config_token()

    result = slack_api("apps.manifest.export", token, app_id=args.app_id)

    if result.get("ok"):
        manifest = result.get("manifest", {})
        print(json.dumps(manifest, indent=2))
    else:
        print(f"Export failed: {result.get('error')}")
        return 1
    return 0


def cmd_rotate(args):
    """Rotate configuration tokens and save to 1Password."""
    access_token = op_read(OP_ACCESS_TOKEN)
    refresh_token = op_read(OP_REFRESH_TOKEN)

    print("Rotating configuration tokens...")
    result = slack_api("tooling.tokens.rotate", access_token,
                       refresh_token=refresh_token)

    if result.get("ok"):
        new_access = result.get("token")
        new_refresh = result.get("refresh_token")
        print("Tokens rotated. Saving to 1Password...")
        op_write(OP_ACCESS_TOKEN, new_access)
        op_write(OP_REFRESH_TOKEN, new_refresh)
        print("Done.")
    else:
        print(f"Rotation failed: {result.get('error')}")
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="Manage New Bufo Drop Slack app")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("validate", help="Validate manifest")
    sub.add_parser("create", help="Create app from manifest")

    p_update = sub.add_parser("update", help="Update existing app")
    p_update.add_argument("--app-id", required=True)

    p_export = sub.add_parser("export", help="Export existing app manifest")
    p_export.add_argument("--app-id", required=True)

    sub.add_parser("rotate", help="Rotate config tokens")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "validate": cmd_validate,
        "create": cmd_create,
        "update": cmd_update,
        "export": cmd_export,
        "rotate": cmd_rotate,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
