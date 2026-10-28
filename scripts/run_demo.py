"""
scripts/run_demo.py

Standalone demo script — runs the full OpenAI enrichment without needing
the Flask server to be running. Reads credentials from .env.

Steps:
  1. Enrich the OpenAI company in HubSpot (by domain: openai.com)
  2. Discover 5 contacts (CTO, VP Eng, SWE, PM, ML roles)
  3. Create / update them in HubSpot and associate to the company
  4. Print a formatted summary report

Usage:
    # With a known HubSpot company ID:
    python scripts/run_demo.py --company-id 12345

    # Without — will search HubSpot by domain first:
    python scripts/run_demo.py

    # Override contact limit and job titles:
    python scripts/run_demo.py --titles "CTO,VP Engineering" --max 3
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ── Bootstrap Flask app so clients work ──────────────────────────────────────
# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import create_app
from app.enrichment_service import (
    enrich_company_by_domain_and_update_hs,
    find_and_create_contacts,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

OPENAI_DOMAIN = "openai.com"
DEFAULT_TITLES = [
    "CTO",
    "VP Engineering",
    "VP of Engineering",
    "Director of Engineering",
    "Software Engineer",
    "Machine Learning Engineer",
    "Research Scientist",
    "Product Manager",
]


def print_separator(title: str = "") -> None:
    width = 60
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{'─' * pad} {title} {'─' * pad}")
    else:
        print("─" * width)


def print_company(data: dict) -> None:
    print_separator("COMPANY ENRICHMENT")
    d = data.get("data") or data.get("forager_data") or {}
    fields = [
        ("Name",        d.get("name")),
        ("Domain",      d.get("domain")),
        ("Description", (d.get("description") or "")[:200]),
        ("Industry",    d.get("industry")),
        ("Headcount",   d.get("headcount")),
        ("Revenue",     d.get("revenue")),
        ("City",        d.get("city")),
        ("State",       d.get("state")),
        ("Country",     d.get("country")),
        ("LinkedIn",    d.get("linkedin_url")),
        ("Phone",       d.get("phone")),
        ("Type",        d.get("type")),
    ]
    for label, value in fields:
        if value:
            print(f"  {label:<14} {value}")
    print(f"\n  HubSpot ID: {data.get('company_id') or '(not found in HubSpot)'}")
    print(f"  Status:     {data.get('status')}")


def print_contacts(contacts_result: dict) -> None:
    print_separator("CONTACTS ENRICHED")
    contacts = contacts_result.get("contacts") or []
    print(f"  Discovered: {contacts_result.get('discovered', 0)}")
    print(f"  Created:    {contacts_result.get('created', 0)}")
    print()
    for i, c in enumerate(contacts, 1):
        print(f"  [{i}] {c.get('name') or 'Unknown'}")
        print(f"       Title:     {c.get('title') or '—'}")
        print(f"       Email:     {c.get('email') or '—'}")
        print(f"       Phone:     {c.get('phone') or '—'}")
        print(f"       LinkedIn:  {c.get('linkedin') or '—'}")
        print(f"       HubSpot:   {c.get('hubspot_id') or '—'}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OpenAI demo enrichment")
    parser.add_argument("--company-id", help="HubSpot company ID for OpenAI")
    parser.add_argument(
        "--titles",
        help="Comma-separated job titles to filter (default: CTO, VP Eng, SWE, PM ...)",
        default=None,
    )
    parser.add_argument(
        "--max",
        type=int,
        default=5,
        help="Max number of contacts to discover (default: 5)",
    )
    args = parser.parse_args()

    # Validate credentials
    for var in ("FORAGER_API_KEY", "FORAGER_ACCOUNT_ID", "HUBSPOT_ACCESS_TOKEN"):
        if not os.environ.get(var):
            print(f"ERROR: {var} is not set. Check your .env file.")
            sys.exit(1)

    titles = [t.strip() for t in args.titles.split(",")] if args.titles else DEFAULT_TITLES

    print("\n" + "═" * 60)
    print("  Forager × HubSpot  —  OpenAI Demo Enrichment")
    print("═" * 60)
    print(f"  Domain  : {OPENAI_DOMAIN}")
    print(f"  Titles  : {', '.join(titles[:4])}{'...' if len(titles) > 4 else ''}")
    print(f"  Max     : {args.max} contacts")
    print("═" * 60)

    app = create_app()

    with app.app_context():
        # Step 1: Enrich company
        print("\n⏳ Enriching company...")
        company_result = enrich_company_by_domain_and_update_hs(
            domain=OPENAI_DOMAIN,
            company_id=args.company_id,
        )
        print_company(company_result)

        company_id = company_result.get("company_id") or args.company_id

        # Step 2: Find & create contacts
        print("\n⏳ Discovering contacts...")
        contacts_result = find_and_create_contacts(
            company_domain=OPENAI_DOMAIN,
            company_id=company_id,
            job_titles=titles,
            max_results=args.max,
        )
        print_contacts(contacts_result)

    print_separator()
    print("  ✅  Demo complete!")
    print("  → Check your HubSpot portal to see the enriched records.\n")


if __name__ == "__main__":
    main()
