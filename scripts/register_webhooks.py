"""
scripts/register_webhooks.py

Programmatically registers HubSpot webhook subscriptions via the API.
Run this ONCE after deploying to Railway.

Usage:
    python scripts/register_webhooks.py --url https://your-app.up.railway.app

Requires: HUBSPOT_ACCESS_TOKEN and HUBSPOT_APP_ID in .env
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

import requests


HUBSPOT_WEBHOOKS_API = "https://api.hubapi.com/webhooks/v3/{app_id}/subscriptions"
HUBSPOT_SETTINGS_API = "https://api.hubapi.com/webhooks/v3/{app_id}/settings"

SUBSCRIPTION_TYPES = [
    "company.creation",
    "contact.creation",
]


def get_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def set_target_url(app_id: str, token: str, target_url: str) -> None:
    """Set the webhook target URL at the app level."""
    url = HUBSPOT_SETTINGS_API.format(app_id=app_id)
    payload = {
        "targetUrl": target_url,
        "throttling": {
            "period": "SECONDLY",
            "maxConcurrentRequests": 10,
        },
    }
    resp = requests.put(url, json=payload, headers=get_headers(token))
    if resp.ok:
        print(f"  ✅  Webhook target URL set: {target_url}")
    else:
        print(f"  ❌  Failed to set target URL: {resp.status_code} {resp.text}")


def list_existing_subscriptions(app_id: str, token: str) -> list[dict]:
    url = HUBSPOT_WEBHOOKS_API.format(app_id=app_id)
    resp = requests.get(url, headers=get_headers(token))
    if resp.ok:
        return resp.json().get("results", [])
    return []


def create_subscription(app_id: str, token: str, event_type: str) -> None:
    url = HUBSPOT_WEBHOOKS_API.format(app_id=app_id)
    payload = {
        "eventType": event_type,
        "active": True,
    }
    resp = requests.post(url, json=payload, headers=get_headers(token))
    if resp.ok:
        sub_id = resp.json().get("id")
        print(f"  ✅  Created subscription: {event_type} (id={sub_id})")
    elif resp.status_code == 409:
        print(f"  ⚪  Already exists: {event_type} (skipped)")
    else:
        print(f"  ❌  Failed: {event_type} — {resp.status_code}: {resp.text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Register HubSpot webhooks")
    parser.add_argument(
        "--url",
        required=True,
        help="Your deployed service URL, e.g. https://myapp.up.railway.app",
    )
    args = parser.parse_args()

    token = os.environ.get("HUBSPOT_ACCESS_TOKEN")
    app_id = os.environ.get("HUBSPOT_APP_ID")

    if not token:
        print("ERROR: HUBSPOT_ACCESS_TOKEN not set.")
        sys.exit(1)
    if not app_id:
        print("ERROR: HUBSPOT_APP_ID not set.")
        print("Find your App ID in HubSpot → Settings → Integrations → Private Apps → your app.")
        sys.exit(1)

    webhook_url = args.url.rstrip("/") + "/webhooks/hubspot"

    print("\nForager × HubSpot — Webhook Registration")
    print(f"App ID      : {app_id}")
    print(f"Webhook URL : {webhook_url}")
    print()

    # Set target URL
    set_target_url(app_id, token, webhook_url)

    # List and create subscriptions
    print("\nCreating subscriptions...")
    existing = list_existing_subscriptions(app_id, token)
    existing_types = {s.get("eventType") for s in existing}

    for event_type in SUBSCRIPTION_TYPES:
        if event_type in existing_types:
            print(f"  ⚪  Already exists: {event_type}")
        else:
            create_subscription(app_id, token, event_type)

    print("\nDone! Webhooks are registered and active.")
    print(f"Any new company or contact added to HubSpot will now trigger: {webhook_url}\n")


if __name__ == "__main__":
    main()
