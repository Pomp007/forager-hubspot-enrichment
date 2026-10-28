"""
Forager API client.

All endpoints documented at https://api-v2.forager.ai/docs
Authentication: X-API-KEY header.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from flask import current_app

logger = logging.getLogger(__name__)

# How long to poll for async work-email jobs (seconds)
WORK_EMAIL_POLL_TIMEOUT = 60
WORK_EMAIL_POLL_INTERVAL = 3


class ForagerClient:
    """Thin wrapper around the Forager REST API."""

    def __init__(self) -> None:
        self.api_key: str = current_app.config["FORAGER_API_KEY"]
        self.account_id: str = current_app.config["FORAGER_ACCOUNT_ID"]
        self.base_url: str = current_app.config["FORAGER_BASE_URL"]
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-API-KEY": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _url(self, path: str, v2: bool = False) -> str:
        """Build a full endpoint URL.

        v2=True  → https://api-v2.forager.ai/api/v2/{account_id}/datastorage/...
        v2=False → https://api-v2.forager.ai/api/{account_id}/datastorage/...
        """
        prefix = f"{self.base_url}/v2/{self.account_id}" if v2 else f"{self.base_url}/{self.account_id}"
        return f"{prefix}/datastorage/{path}"

    def _post(self, url: str, payload: dict) -> dict:
        resp = self.session.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _get(self, url: str) -> dict:
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ──────────────────────────────────────────────────────────────────────────
    # Company / Organisation enrichment
    # ──────────────────────────────────────────────────────────────────────────

    def enrich_company_by_domain(self, domain: str) -> dict[str, Any]:
        """Return full company details for a given domain.

        Uses the organization_search endpoint filtered to the exact domain.
        """
        logger.info("Forager: enriching company domain=%s", domain)
        payload = {
            "filters": {
                "website": domain,
            },
            "page": 1,
            "page_size": 1,
        }
        data = self._post(self._url("organization_search/"), payload)
        results = data.get("results") or data.get("organizations") or []
        if not results:
            logger.warning("Forager: no company found for domain=%s", domain)
            return {}
        return self._normalise_company(results[0])

    def enrich_company_by_name(self, name: str) -> dict[str, Any]:
        """Return company details searching by name."""
        logger.info("Forager: enriching company name=%s", name)
        payload = {
            "filters": {"company_name": name},
            "page": 1,
            "page_size": 1,
        }
        data = self._post(self._url("organization_search/"), payload)
        results = data.get("results") or data.get("organizations") or []
        if not results:
            return {}
        return self._normalise_company(results[0])

    def _normalise_company(self, raw: dict) -> dict[str, Any]:
        """Map Forager company record to our internal schema."""
        return {
            "name": raw.get("company_name") or raw.get("name"),
            "domain": raw.get("website") or raw.get("domain"),
            "description": raw.get("description") or raw.get("short_description"),
            "industry": raw.get("industry"),
            "headcount": raw.get("employee_count") or raw.get("headcount"),
            "revenue": raw.get("annual_revenue") or raw.get("revenue"),
            "founded_year": raw.get("founded") or raw.get("founded_year"),
            "city": raw.get("city"),
            "state": raw.get("state"),
            "country": raw.get("country"),
            "linkedin_url": raw.get("linkedin_url") or raw.get("company_linkedin_url"),
            "phone": raw.get("phone"),
            "type": raw.get("company_type") or raw.get("type"),
            "specialties": raw.get("specialties"),
            "technologies": raw.get("technologies"),
            "_raw": raw,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Contact / Person enrichment
    # ──────────────────────────────────────────────────────────────────────────

    def enrich_contact_by_linkedin(self, linkedin_url: str) -> dict[str, Any]:
        """Enrich a contact using their LinkedIn profile URL."""
        logger.info("Forager: enriching contact linkedin=%s", linkedin_url)
        identifier = linkedin_url.rstrip("/").split("/")[-1]
        payload = {"linkedin_public_identifier": identifier}
        data = self._post(self._url("person_detail_lookup/"), payload)
        person = data.get("person") or data.get("result") or data
        return self._enrich_contact_contacts(person)

    def enrich_contact_by_email(self, email: str) -> dict[str, Any]:
        """Enrich a contact using their work email."""
        logger.info("Forager: enriching contact email=%s", email)
        payload = {"email": email}
        data = self._post(
            self._url("person_detail_reverse_lookup/by_email/"), payload
        )
        person = data.get("person") or data.get("result") or data
        return self._enrich_contact_contacts(person)

    def enrich_contact_by_name_and_company(
        self, first_name: str, last_name: str, company_domain: str
    ) -> dict[str, Any]:
        """Best-effort enrichment when only name + company domain are known."""
        logger.info(
            "Forager: searching person %s %s at %s", first_name, last_name, company_domain
        )
        payload = {
            "filters": {
                "first_name": first_name,
                "last_name": last_name,
                "company_domain": company_domain,
            },
            "page": 1,
            "page_size": 1,
        }
        data = self._post(self._url("person_role_search/"), payload)
        results = data.get("results") or data.get("people") or []
        if not results:
            return {}
        person = results[0]
        return self._enrich_contact_contacts(person)

    def _enrich_contact_contacts(self, person: dict) -> dict[str, Any]:
        """Given a raw person dict, fetch phones + emails, then normalise."""
        if not person:
            return {}
        person_id = person.get("person_id") or person.get("id")
        linkedin_id = person.get("linkedin_public_identifier") or person.get("linkedin_url", "").rstrip("/").split("/")[-1]

        phone_numbers: list[str] = []
        personal_emails: list[str] = []
        work_emails: list[str] = []

        if person_id or linkedin_id:
            ref = {"person_id": person_id} if person_id else {"linkedin_public_identifier": linkedin_id}

            # Phones
            try:
                ph_data = self._post(self._url("person_contacts_lookup/phone_numbers/"), ref)
                phone_numbers = ph_data.get("phone_numbers") or []
            except Exception as exc:
                logger.warning("Forager phone lookup failed: %s", exc)

            # Personal emails
            try:
                pe_data = self._post(self._url("person_contacts_lookup/personal_emails/"), ref)
                personal_emails = pe_data.get("personal_emails") or []
            except Exception as exc:
                logger.warning("Forager personal email lookup failed: %s", exc)

            # Work emails (async via v2 endpoint)
            try:
                work_emails = self._fetch_work_emails(ref)
            except Exception as exc:
                logger.warning("Forager work email lookup failed: %s", exc)

        return self._normalise_contact(person, phone_numbers, personal_emails, work_emails)

    def _fetch_work_emails(self, ref: dict) -> list[str]:
        """Submit async work-email job and poll until resolved."""
        submit_url = self._url("person_contacts_lookup/work_emails/", v2=True)
        job_data = self._post(submit_url, ref)
        job_id = job_data.get("job_id") or job_data.get("id")

        if not job_id:
            # Some accounts return inline results
            return job_data.get("work_emails") or []

        poll_url = self._url(f"person_contacts_lookup/work_emails/{job_id}/", v2=True)
        deadline = time.time() + WORK_EMAIL_POLL_TIMEOUT

        while time.time() < deadline:
            result = self._get(poll_url)
            status = result.get("status", "pending")
            if status == "completed":
                return result.get("work_emails") or []
            if status == "failed":
                logger.warning("Forager work email job %s failed", job_id)
                return []
            time.sleep(WORK_EMAIL_POLL_INTERVAL)

        logger.warning("Forager work email job %s timed out", job_id)
        return []

    def _normalise_contact(
        self,
        raw: dict,
        phone_numbers: list[str],
        personal_emails: list[str],
        work_emails: list[str],
    ) -> dict[str, Any]:
        """Map Forager person record to our internal contact schema."""
        all_emails = work_emails + personal_emails
        primary_email = all_emails[0] if all_emails else None
        primary_phone = phone_numbers[0] if phone_numbers else None

        experience = raw.get("experience") or []
        current_role = experience[0] if experience else {}

        linkedin_id = raw.get("linkedin_public_identifier") or raw.get("linkedin_url", "").rstrip("/").split("/")[-1]
        linkedin_url = (
            raw.get("linkedin_url")
            or (f"https://www.linkedin.com/in/{linkedin_id}" if linkedin_id else None)
        )

        return {
            "first_name": raw.get("first_name"),
            "last_name": raw.get("last_name"),
            "full_name": raw.get("full_name") or f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip(),
            "linkedin_url": linkedin_url,
            "job_title": raw.get("current_title") or current_role.get("title"),
            "company_name": raw.get("current_company") or current_role.get("company_name"),
            "company_domain": raw.get("company_domain") or current_role.get("company_domain"),
            "company_linkedin_url": raw.get("company_linkedin_url") or current_role.get("company_linkedin_url"),
            "email": primary_email,
            "work_emails": work_emails,
            "personal_emails": personal_emails,
            "phone": primary_phone,
            "all_phones": phone_numbers,
            "city": raw.get("city"),
            "state": raw.get("state"),
            "country": raw.get("country"),
            # Nice-to-have extras
            "description": raw.get("description") or raw.get("summary"),
            "about": raw.get("about") or raw.get("headline"),
            "skills": raw.get("skills"),
            "education": raw.get("education"),
            "_raw": raw,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Contact discovery
    # ──────────────────────────────────────────────────────────────────────────

    def find_contacts_at_company(
        self,
        company_domain: str,
        job_titles: list[str] | None = None,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Discover contacts at a company, optionally filtered by job title.

        job_titles — list of title keywords; any match qualifies a person.
        max_results — cap on number of people returned.
        """
        logger.info(
            "Forager: finding contacts at domain=%s titles=%s limit=%d",
            company_domain,
            job_titles,
            max_results,
        )
        filters: dict[str, Any] = {"company_domain": company_domain}
        if job_titles:
            filters["current_title"] = job_titles  # Forager accepts a list for OR matching

        payload = {
            "filters": filters,
            "page": 1,
            "page_size": max_results,
        }
        data = self._post(self._url("person_role_search/"), payload)
        results = data.get("results") or data.get("people") or []

        enriched: list[dict[str, Any]] = []
        for person in results[:max_results]:
            contact = self._enrich_contact_contacts(person)
            if contact:
                enriched.append(contact)

        return enriched
