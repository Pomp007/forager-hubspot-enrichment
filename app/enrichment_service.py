"""
Core enrichment service.

Orchestrates Forager lookups + HubSpot writes.  All business logic lives here;
webhooks and API routes call these functions.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import current_app

from .forager_client import ForagerClient, _clean_domain
from .hubspot_client import HubSpotClient

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Company enrichment
# ──────────────────────────────────────────────────────────────────────────────


def enrich_hubspot_company(company_id: str, force: bool = False) -> dict[str, Any]:
    """
    Enrich a HubSpot company record using Forager.

    Steps:
    1. Fetch current HubSpot company (to get domain/name).
    2. Check if already enriched to prevent duplicate credit consumption.
    3. Call Forager to get full company data.
    4. Write enrichment back to HubSpot.
    5. Return a summary dict.
    """
    hs = HubSpotClient()

    logger.info("Enriching HubSpot company id=%s", company_id)

    # 1. Get current record
    company_record = hs.get_company(company_id)
    props = company_record.get("properties") or {}

    if not force and props.get("forager_enriched") in ("true", True, "True"):
        logger.info("Company %s already enriched by Forager; skipping", company_id)
        return {
            "company_id": company_id,
            "status": "already_enriched",
            "message": "Company was already enriched by Forager.",
        }

    forager = ForagerClient()
    raw_domain: str = props.get("domain") or props.get("website") or ""
    domain = _clean_domain(raw_domain)
    name: str = props.get("name") or ""

    # 2. Forager lookup (prefer domain, fall back to name)
    enriched: dict[str, Any] = {}
    if domain:
        enriched = forager.enrich_company_by_domain(domain)
    if not enriched and name:
        enriched = forager.enrich_company_by_name(name)

    if not enriched:
        logger.warning("Forager returned no data for company %s (domain=%s name=%s)", company_id, domain, name)
        return {
            "company_id": company_id,
            "status": "no_data",
            "message": "Forager returned no data for this company.",
        }

    # 3. Write back to HubSpot
    hs.update_company(company_id, enriched)

    return {
        "company_id": company_id,
        "status": "enriched",
        "forager_data": _safe_summary(enriched),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Contact enrichment
# ──────────────────────────────────────────────────────────────────────────────


def enrich_hubspot_contact(contact_id: str, force: bool = False) -> dict[str, Any]:
    """
    Enrich a HubSpot contact record using Forager.

    Lookup priority:
    1. LinkedIn URL (most reliable Forager identifier).
    2. Work email.
    3. Name + company domain.
    """
    hs = HubSpotClient()

    logger.info("Enriching HubSpot contact id=%s", contact_id)

    contact_record = hs.get_contact(contact_id)
    props = contact_record.get("properties") or {}

    if not force and props.get("forager_enriched") in ("true", True, "True"):
        logger.info("Contact %s already enriched by Forager; skipping", contact_id)
        return {
            "contact_id": contact_id,
            "status": "already_enriched",
            "message": "Contact was already enriched by Forager.",
        }

    forager = ForagerClient()

    linkedin_url: str = props.get("linkedin_url") or props.get("hs_linkedin_url") or ""
    email: str = props.get("email") or ""
    first_name: str = props.get("firstname") or ""
    last_name: str = props.get("lastname") or ""
    company_domain: str = props.get("forager_company_domain") or props.get("website") or ""

    enriched: dict[str, Any] = {}

    if linkedin_url:
        enriched = forager.enrich_contact_by_linkedin(linkedin_url)
    if not enriched and email:
        enriched = forager.enrich_contact_by_email(email)
    if not enriched and first_name and last_name and company_domain:
        enriched = forager.enrich_contact_by_name_and_company(first_name, last_name, company_domain)

    if not enriched:
        logger.warning(
            "Forager returned no data for contact %s (email=%s linkedin=%s)",
            contact_id, email, linkedin_url
        )
        return {
            "contact_id": contact_id,
            "status": "no_data",
            "message": "Forager returned no data for this contact.",
        }

    hs.update_contact(contact_id, enriched)

    return {
        "contact_id": contact_id,
        "status": "enriched",
        "forager_data": _safe_summary(enriched),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Company webhook automation (full flow)
# ──────────────────────────────────────────────────────────────────────────────


def handle_company_created(company_id: str) -> dict[str, Any]:
    """
    Full automation triggered when a new company is added to HubSpot.

    1. Enrich the company.
    2. Look for existing HubSpot contacts associated with this company.
       - If found → enrich each of them.
       - If none → discover new contacts via Forager and create them in HubSpot.
    """
    results: dict[str, Any] = {
        "company_id": company_id,
        "company_enrichment": None,
        "contacts_enriched": [],
        "contacts_created": [],
    }

    # Step 1: Enrich company
    company_result = enrich_hubspot_company(company_id)
    results["company_enrichment"] = company_result

    hs = HubSpotClient()
    forager = ForagerClient()

    # Step 2: Check for existing contacts
    existing_contacts = hs.get_company_contacts(company_id)

    if existing_contacts:
        logger.info(
            "Company %s has %d existing contacts — enriching them",
            company_id, len(existing_contacts)
        )
        for contact in existing_contacts:
            contact_id = contact.get("id")
            if contact_id:
                cr = enrich_hubspot_contact(contact_id)
                results["contacts_enriched"].append(cr)
    else:
        # Step 3: Discover new contacts
        logger.info("Company %s has no existing contacts — discovering via Forager", company_id)
        company_record = hs.get_company(company_id)
        props = company_record.get("properties") or {}
        domain: str = props.get("domain") or props.get("website") or ""

        if not domain:
            logger.warning("Cannot discover contacts for company %s — no domain", company_id)
            results["contacts_created"] = []
            return results

        job_titles: list[str] = current_app.config["DEFAULT_JOB_TITLES"]
        max_contacts: int = current_app.config["MAX_CONTACTS_PER_COMPANY"]

        discovered = forager.find_contacts_at_company(
            company_domain=domain,
            job_titles=job_titles,
            max_results=max_contacts,
        )

        for contact_data in discovered:
            try:
                created = hs.create_contact(contact_data, company_id=company_id)
                results["contacts_created"].append(
                    {
                        "hubspot_id": created.get("id"),
                        "name": contact_data.get("full_name"),
                        "email": contact_data.get("email"),
                        "title": contact_data.get("job_title"),
                    }
                )
            except Exception as exc:
                logger.error("Failed to create contact %s: %s", contact_data.get("full_name"), exc)

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Direct enrichment utilities (for API routes / manual triggers)
# ──────────────────────────────────────────────────────────────────────────────


def enrich_company_by_domain_and_update_hs(
    domain: str,
    company_id: str | None = None,
) -> dict[str, Any]:
    """
    Enrich a company by domain; if company_id is given, update that HubSpot record.
    If company_id is None, search HubSpot for a matching record first.
    """
    forager = ForagerClient()
    hs = HubSpotClient()

    enriched = forager.enrich_company_by_domain(domain)
    if not enriched:
        return {"status": "no_data", "domain": domain}

    # Resolve HubSpot company ID
    hs_id = company_id
    if not hs_id:
        matches = hs.search_companies_by_domain(domain)
        if matches:
            hs_id = matches[0].get("id")

    if hs_id:
        hs.update_company(hs_id, enriched)

    return {
        "status": "enriched",
        "company_id": hs_id,
        "domain": domain,
        "data": _safe_summary(enriched),
    }


def find_and_create_contacts(
    company_domain: str,
    company_id: str | None = None,
    job_titles: list[str] | None = None,
    max_results: int = 5,
) -> dict[str, Any]:
    """
    Discover contacts at a company via Forager and create them in HubSpot.

    job_titles: optional override list; defaults to app config.
    """
    forager = ForagerClient()
    hs = HubSpotClient()

    titles = job_titles or current_app.config["DEFAULT_JOB_TITLES"]
    discovered = forager.find_contacts_at_company(
        company_domain=company_domain,
        job_titles=titles,
        max_results=max_results,
    )

    created: list[dict] = []
    for contact_data in discovered:
        try:
            result = hs.create_contact(contact_data, company_id=company_id)
            created.append(
                {
                    "hubspot_id": result.get("id"),
                    "name": contact_data.get("full_name"),
                    "email": contact_data.get("email"),
                    "title": contact_data.get("job_title"),
                    "phone": contact_data.get("phone"),
                    "linkedin": contact_data.get("linkedin_url"),
                }
            )
        except Exception as exc:
            logger.error("Failed to create contact %s: %s", contact_data.get("full_name"), exc)

    return {
        "status": "done",
        "company_domain": company_domain,
        "discovered": len(discovered),
        "created": len(created),
        "contacts": created,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _safe_summary(data: dict) -> dict:
    """Return enrichment dict without the verbose _raw key."""
    return {k: v for k, v in data.items() if k != "_raw"}
