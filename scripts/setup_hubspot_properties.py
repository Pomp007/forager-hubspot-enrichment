"""
scripts/setup_hubspot_properties.py

Run this ONCE to create all custom Forager properties in your HubSpot portal.
This ensures the enrichment service can write Forager-specific fields to contacts
and companies without 400 errors.

Usage:
    python scripts/setup_hubspot_properties.py

Requires HUBSPOT_ACCESS_TOKEN in .env or environment.
"""

import os
import sys
import json

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import hubspot
from hubspot.crm.properties import PropertyCreate, ApiException

# ──────────────────────────────────────────────────────────────────────────────
# Property definitions
# ──────────────────────────────────────────────────────────────────────────────

CONTACT_PROPERTIES = [
    {
        "name": "forager_company_domain",
        "label": "Forager Company Domain",
        "type": "string",
        "field_type": "text",
        "group_name": "contactinformation",
        "description": "Employer domain from Forager enrichment (e.g. openai.com)",
    },
    {
        "name": "forager_company_linkedin",
        "label": "Forager Company LinkedIn URL",
        "type": "string",
        "field_type": "text",
        "group_name": "contactinformation",
        "description": "Employer LinkedIn URL from Forager enrichment",
    },
    {
        "name": "forager_description",
        "label": "Forager Bio / Description",
        "type": "string",
        "field_type": "textarea",
        "group_name": "contactinformation",
        "description": "LinkedIn About section / bio from Forager enrichment",
    },
    {
        "name": "forager_about",
        "label": "Forager Headline",
        "type": "string",
        "field_type": "text",
        "group_name": "contactinformation",
        "description": "Professional headline from Forager enrichment",
    },
    {
        "name": "forager_work_emails",
        "label": "Forager Work Emails",
        "type": "string",
        "field_type": "text",
        "group_name": "contactinformation",
        "description": "Verified work emails from Forager (semicolon-separated)",
    },
    {
        "name": "forager_personal_emails",
        "label": "Forager Personal Emails",
        "type": "string",
        "field_type": "text",
        "group_name": "contactinformation",
        "description": "Personal emails from Forager (semicolon-separated)",
    },
    {
        "name": "forager_all_phones",
        "label": "Forager All Phone Numbers",
        "type": "string",
        "field_type": "text",
        "group_name": "contactinformation",
        "description": "All phone numbers from Forager (semicolon-separated)",
    },
    {
        "name": "forager_enriched",
        "label": "Forager Enriched",
        "type": "bool",
        "field_type": "booleancheckbox",
        "group_name": "contactinformation",
        "description": "Set to true when this record has been enriched by Forager",
    },
]

COMPANY_PROPERTIES = [
    {
        "name": "forager_headcount",
        "label": "Forager Headcount",
        "type": "number",
        "field_type": "number",
        "group_name": "companyinformation",
        "description": "Employee headcount from Forager enrichment",
    },
    {
        "name": "forager_revenue",
        "label": "Forager Annual Revenue",
        "type": "number",
        "field_type": "number",
        "group_name": "companyinformation",
        "description": "Annual revenue from Forager enrichment",
    },
    {
        "name": "forager_specialties",
        "label": "Forager Specialties",
        "type": "string",
        "field_type": "textarea",
        "group_name": "companyinformation",
        "description": "Company specialties / focus areas from Forager",
    },
    {
        "name": "forager_technologies",
        "label": "Forager Tech Stack",
        "type": "string",
        "field_type": "textarea",
        "group_name": "companyinformation",
        "description": "Technologies used by the company, from Forager",
    },
    {
        "name": "forager_enriched",
        "label": "Forager Enriched",
        "type": "bool",
        "field_type": "booleancheckbox",
        "group_name": "companyinformation",
        "description": "Set to true when this record has been enriched by Forager",
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# Setup runner
# ──────────────────────────────────────────────────────────────────────────────

def create_properties(client, object_type: str, properties: list[dict]) -> None:
    """Create custom properties for a given object type (contacts / companies)."""
    print(f"\n{'─'*60}")
    print(f"  Creating {len(properties)} custom properties for {object_type.upper()}")
    print(f"{'─'*60}")

    for prop in properties:
        name = prop["name"]
        try:
            client.crm.properties.core_api.create(
                object_type=object_type,
                property_create=PropertyCreate(
                    name=prop["name"],
                    label=prop["label"],
                    type=prop["type"],
                    field_type=prop["field_type"],
                    group_name=prop["group_name"],
                    description=prop.get("description", ""),
                ),
            )
            print(f"  ✅  Created: {name}")
        except ApiException as exc:
            body = json.loads(exc.body) if exc.body else {}
            if body.get("category") == "VALIDATION_ERROR" and "already exists" in body.get("message", ""):
                print(f"  ⚪  Already exists: {name} (skipped)")
            else:
                print(f"  ❌  Failed: {name} — {exc.status}: {body.get('message', exc.reason)}")


def main() -> None:
    token = os.environ.get("HUBSPOT_ACCESS_TOKEN")
    if not token:
        print("ERROR: HUBSPOT_ACCESS_TOKEN is not set.")
        print("Copy .env.example → .env and fill in your token, then re-run.")
        sys.exit(1)

    print("Forager × HubSpot — Custom Property Setup")
    print(f"Portal: {os.environ.get('HUBSPOT_PORTAL_ID', '(unknown)')}")

    client = hubspot.Client.create(access_token=token)

    create_properties(client, "contacts", CONTACT_PROPERTIES)
    create_properties(client, "companies", COMPANY_PROPERTIES)

    print(f"\n{'─'*60}")
    print("  Setup complete! All custom properties are ready.")
    print("  You can now deploy the service and register the webhooks.")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    main()
