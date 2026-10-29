"""
Forager API client.

All endpoints documented at https://api-v2.forager.ai/docs
Authentication: X-API-KEY header.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import requests
from flask import current_app

logger = logging.getLogger(__name__)

# How long to poll for async work-email jobs (seconds)
WORK_EMAIL_POLL_TIMEOUT = 60
WORK_EMAIL_POLL_INTERVAL = 3


def _clean_domain(domain: str) -> str:
    """Normalize a domain string (strips protocol, www, trailing slashes/paths)."""
    if not domain:
        return ""
    d = str(domain).strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    return d.split("/")[0].split(":")[0].strip()


def _to_numeric(val: Any) -> int | float | None:
    """Extract an integer or float from a number or string representation."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return val
    s = str(val).strip().replace(",", "")
    if s.isdigit():
        return int(s)
    numbers = re.findall(r"\d+", s)
    if numbers:
        return int(numbers[-1])
    return None


def _extract_items(data: Any, *keys: str) -> list[str]:
    """Robustly extract string values (e.g. phones or emails) from varied API response formats."""
    if not data:
        return []

    raw_list: list[Any] = []
    if isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict):
        for k in keys:
            if k in data and isinstance(data[k], list):
                raw_list = data[k]
                break
        if not raw_list and "data" in data and isinstance(data["data"], dict):
            for k in keys:
                if k in data["data"] and isinstance(data["data"][k], list):
                    raw_list = data["data"][k]
                    break
        if not raw_list and "results" in data and isinstance(data["results"], list):
            raw_list = data["results"]
        if not raw_list and "search_results" in data and isinstance(data["search_results"], list):
            raw_list = data["search_results"]

    if not raw_list:
        return []

    extracted: list[str] = []
    for item in raw_list:
        if isinstance(item, str):
            val = item.strip()
            if val:
                extracted.append(val)
        elif isinstance(item, dict):
            for val_key in ("phone_number", "number", "phone", "email", "value", "address"):
                if item.get(val_key):
                    extracted.append(str(item[val_key]).strip())
                    break
    return [x for x in extracted if x]


class ForagerClient:
    """Thin wrapper around the Forager REST API."""

    def __init__(
        self,
        api_key: str | None = None,
        account_id: str | None = None,
        base_url: str | None = None,
    ) -> None:
        # Fall back gracefully to Flask config or environment variables
        cfg: dict[str, Any] = {}
        try:
            if current_app:
                cfg = current_app.config
        except RuntimeError:
            pass

        self.api_key: str = api_key or cfg.get("FORAGER_API_KEY") or os.environ.get("FORAGER_API_KEY", "")
        self.account_id: str = (
            account_id or cfg.get("FORAGER_ACCOUNT_ID") or os.environ.get("FORAGER_ACCOUNT_ID", "")
        )
        self.base_url: str = (
            base_url
            or cfg.get("FORAGER_BASE_URL")
            or os.environ.get("FORAGER_BASE_URL", "https://api-v2.forager.ai/api")
        ).rstrip("/")

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
        clean_path = path.lstrip("/")
        return f"{prefix}/datastorage/{clean_path}"

    def _post(self, url: str, payload: dict) -> Any:
        try:
            resp = self.session.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (402, 429):
                logger.warning("Forager API rate limited or credits exhausted (%d %s)", exc.response.status_code, exc.response.reason)
                return {}
            raise

    def _get(self, url: str) -> Any:
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (402, 429):
                logger.warning("Forager API rate limited or credits exhausted (%d %s)", exc.response.status_code, exc.response.reason)
                return {}
            raise

    # ──────────────────────────────────────────────────────────────────────────
    # Company / Organisation enrichment
    # ──────────────────────────────────────────────────────────────────────────

    def enrich_company_by_domain(self, domain: str) -> dict[str, Any]:
        """Return full company details for a given domain."""
        clean_dom = _clean_domain(domain)
        if not clean_dom:
            return {}

        logger.info("Forager: enriching company domain=%s", clean_dom)

        # Forager standard schema: domains array at root level
        payload = {
            "page": 0,
            "domains": [clean_dom],
        }
        try:
            data = self._post(self._url("organization_search/"), payload)
        except Exception as exc:
            # Fallback attempt with legacy filters wrapper if root-level schema rejected
            logger.debug("Root domains search failed (%s), attempting fallback query", exc)
            fallback_payload = {
                "filters": {"website": clean_dom},
                "page": 1,
                "page_size": 1,
            }
            data = self._post(self._url("organization_search/"), fallback_payload)

        results = (
            data.get("search_results")
            or data.get("results")
            or data.get("organizations")
            or []
        )
        if not results:
            logger.warning("Forager: no company found for domain=%s", clean_dom)
            return {}
        return self._normalise_company(results[0])

    def enrich_company_by_name(self, name: str) -> dict[str, Any]:
        """Return company details searching by name."""
        logger.info("Forager: enriching company name=%s", name)
        payload = {
            "page": 0,
            "description": name,
        }
        try:
            data = self._post(self._url("organization_search/"), payload)
        except Exception:
            fallback_payload = {
                "filters": {"company_name": name},
                "page": 1,
                "page_size": 1,
            }
            data = self._post(self._url("organization_search/"), fallback_payload)

        results = (
            data.get("search_results")
            or data.get("results")
            or data.get("organizations")
            or []
        )
        if not results:
            return {}
        return self._normalise_company(results[0])

    def _normalise_company(self, raw: dict) -> dict[str, Any]:
        """Map Forager company record to our internal schema."""
        location = raw.get("location") or {}
        if not isinstance(location, dict):
            location = {}

        city = location.get("city") or raw.get("city")
        state = location.get("state") or location.get("region") or raw.get("state")
        country = location.get("country") or raw.get("country")

        industry_val = raw.get("industry")
        if isinstance(industry_val, dict):
            industry = industry_val.get("name")
        else:
            industry = industry_val

        # LinkedIn page resolution
        linkedin_info = raw.get("linkedin_info") or {}
        linkedin_slug = (
            linkedin_info.get("public_identifier")
            if isinstance(linkedin_info, dict)
            else None
        )
        linkedin_url = (
            raw.get("linkedin_url")
            or raw.get("company_linkedin_url")
            or (f"https://www.linkedin.com/company/{linkedin_slug}" if linkedin_slug else None)
        )

        # Tech stack resolution
        technologies: list[str] = []
        if isinstance(raw.get("technographics"), list):
            for t in raw["technographics"]:
                if isinstance(t, dict) and t.get("name"):
                    technologies.append(t["name"])
                elif isinstance(t, str):
                    technologies.append(t)
        elif isinstance(raw.get("technologies"), list):
            technologies = raw.get("technologies", [])
        elif isinstance(raw.get("technologies"), str):
            technologies = [raw["technologies"]]

        headcount_raw = (
            raw.get("headcount")
            or raw.get("employee_count")
            or raw.get("employee_count_range")
        )
        revenue_raw = (
            raw.get("annual_revenue")
            or raw.get("revenue")
            or raw.get("estimated_revenue")
        )

        domain_raw = raw.get("domain") or raw.get("website") or raw.get("website_url")

        return {
            "name": raw.get("name") or raw.get("company_name"),
            "domain": _clean_domain(domain_raw) if domain_raw else None,
            "description": raw.get("description") or raw.get("short_description") or raw.get("about"),
            "industry": industry,
            "headcount": _to_numeric(headcount_raw),
            "headcount_raw": headcount_raw,
            "revenue": _to_numeric(revenue_raw),
            "revenue_raw": revenue_raw,
            "founded_year": raw.get("founded_year") or raw.get("founded"),
            "city": city,
            "state": state,
            "country": country,
            "linkedin_url": linkedin_url,
            "phone": raw.get("phone") or raw.get("phone_number"),
            "type": raw.get("company_type") or raw.get("type"),
            "specialties": raw.get("specialties"),
            "technologies": technologies if technologies else None,
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
        person = (
            data.get("person")
            or data.get("result")
            or (data.get("search_results") or [{}])[0]
            if isinstance(data, dict)
            else data
        )
        return self._enrich_contact_contacts(person)

    def enrich_contact_by_email(self, email: str) -> dict[str, Any]:
        """Enrich a contact using their work email."""
        logger.info("Forager: enriching contact email=%s", email)
        payload = {"email": email}
        data = self._post(self._url("person_detail_reverse_lookup/by_email/"), payload)
        person = (
            data.get("person")
            or data.get("result")
            or (data.get("search_results") or [{}])[0]
            if isinstance(data, dict)
            else data
        )
        return self._enrich_contact_contacts(person)

    def enrich_contact_by_name_and_company(
        self, first_name: str, last_name: str, company_domain: str
    ) -> dict[str, Any]:
        """Best-effort enrichment when only name + company domain are known."""
        clean_dom = _clean_domain(company_domain)
        logger.info("Forager: searching person %s %s at %s", first_name, last_name, clean_dom)

        payload = {
            "page": 0,
            "page_size": 1,
            "organization_domains": [clean_dom],
            "role_title": f"{first_name} {last_name}",
        }
        try:
            data = self._post(self._url("person_role_search/"), payload)
        except Exception:
            fallback_payload = {
                "filters": {
                    "first_name": first_name,
                    "last_name": last_name,
                    "company_domain": clean_dom,
                },
                "page": 1,
                "page_size": 1,
            }
            data = self._post(self._url("person_role_search/"), fallback_payload)

        results = (
            data.get("search_results")
            or data.get("results")
            or data.get("people")
            or []
        )
        if not results:
            return {}

        first_item = results[0]
        if isinstance(first_item, dict) and "person" in first_item:
            person_obj = dict(first_item["person"])
            person_obj.setdefault("role_title", first_item.get("role_title"))
            if "organization" in first_item and isinstance(first_item["organization"], dict):
                person_obj.setdefault("company_name", first_item["organization"].get("name"))
                person_obj.setdefault("company_domain", first_item["organization"].get("domain") or clean_dom)
            return self._enrich_contact_contacts(person_obj)

        return self._enrich_contact_contacts(first_item)

    def _enrich_contact_contacts(self, person: dict) -> dict[str, Any]:
        """Given a raw person dict, fetch phones + emails, then normalise."""
        if not person or not isinstance(person, dict):
            return {}

        person_id = person.get("person_id") or person.get("id")
        linkedin_url = person.get("linkedin_url", "")
        linkedin_id = (
            person.get("linkedin_public_identifier")
            or (linkedin_url.rstrip("/").split("/")[-1] if linkedin_url else "")
        )

        phone_numbers: list[str] = []
        personal_emails: list[str] = []
        work_emails: list[str] = []

        if person_id or linkedin_id:
            ref: dict[str, Any] = {}
            if person_id:
                ref["person_id"] = int(person_id) if str(person_id).isdigit() else person_id
            if linkedin_id:
                ref["linkedin_public_identifier"] = linkedin_id

            # Phones
            try:
                ph_data = self._post(self._url("person_contacts_lookup/phone_numbers/"), ref)
                phone_numbers = _extract_items(ph_data, "phone_numbers", "phones")
            except Exception as exc:
                logger.warning("Forager phone lookup failed: %s", exc)

            # Personal emails
            try:
                pe_data = self._post(self._url("person_contacts_lookup/personal_emails/"), ref)
                personal_emails = _extract_items(pe_data, "personal_emails", "emails")
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
        job_id = None
        if isinstance(job_data, dict):
            job_id = job_data.get("job_id") or job_data.get("id")

        if not job_id:
            return _extract_items(job_data, "work_emails", "emails")

        poll_url = self._url(f"person_contacts_lookup/work_emails/{job_id}/", v2=True)
        deadline = time.time() + WORK_EMAIL_POLL_TIMEOUT

        while time.time() < deadline:
            result = self._get(poll_url)
            if isinstance(result, dict):
                status = result.get("status", "pending")
                if status == "completed":
                    return _extract_items(result, "work_emails", "emails")
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

        # Resolve current role & organization info
        current_role = raw.get("current_role")
        if not isinstance(current_role, dict):
            experiences = raw.get("experiences") or raw.get("experience") or []
            current_role = experiences[0] if experiences and isinstance(experiences[0], dict) else {}

        org = current_role.get("organization") if isinstance(current_role.get("organization"), dict) else {}

        # First and last name parsing
        first_name = raw.get("first_name")
        last_name = raw.get("last_name")
        full_name = raw.get("full_name")
        if not full_name and (first_name or last_name):
            full_name = f"{first_name or ''} {last_name or ''}".strip()
        elif full_name and (not first_name or not last_name):
            parts = full_name.split(" ", 1)
            first_name = first_name or parts[0]
            last_name = last_name or (parts[1] if len(parts) > 1 else "")

        # LinkedIn URL resolution
        linkedin_id = (
            raw.get("linkedin_public_identifier")
            or raw.get("linkedin_url", "").rstrip("/").split("/")[-1]
        )
        linkedin_url = (
            raw.get("linkedin_url")
            or (f"https://www.linkedin.com/in/{linkedin_id}" if linkedin_id else None)
        )

        # Company details resolution
        company_name = (
            raw.get("company_name")
            or raw.get("current_company")
            or org.get("name")
            or current_role.get("company_name")
        )
        company_domain = (
            raw.get("company_domain")
            or org.get("domain")
            or current_role.get("company_domain")
        )
        if company_domain:
            company_domain = _clean_domain(company_domain)

        org_linkedin_id = org.get("linkedin_public_identifier")
        company_linkedin_url = (
            raw.get("company_linkedin_url")
            or current_role.get("company_linkedin_url")
            or (f"https://www.linkedin.com/company/{org_linkedin_id}" if org_linkedin_id else None)
        )

        job_title = (
            raw.get("job_title")
            or raw.get("role_title")
            or raw.get("current_title")
            or current_role.get("title")
        )

        # Location details
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        city = raw.get("locality") or raw.get("city") or location.get("city")
        state = raw.get("region") or raw.get("state") or location.get("state")
        country = raw.get("country") or location.get("country")

        return {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "linkedin_url": linkedin_url,
            "job_title": job_title,
            "company_name": company_name,
            "company_domain": company_domain,
            "company_linkedin_url": company_linkedin_url,
            "email": primary_email,
            "work_emails": work_emails,
            "personal_emails": personal_emails,
            "phone": primary_phone,
            "all_phones": phone_numbers,
            "city": city,
            "state": state,
            "country": country,
            # Nice-to-have extras
            "description": raw.get("summary") or raw.get("description"),
            "about": raw.get("headline") or raw.get("about"),
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
        """Discover contacts at a company, optionally filtered by job title."""
        clean_dom = _clean_domain(company_domain)
        if not clean_dom:
            return []

        logger.info(
            "Forager: finding contacts at domain=%s titles=%s limit=%d",
            clean_dom,
            job_titles,
            max_results,
        )

        payload: dict[str, Any] = {
            "page": 0,
            "page_size": max_results,
            "role_is_current": True,
            "organization_domains": [clean_dom],
        }

        # Forager person_role_search uses a boolean query string for role_title
        if job_titles:
            title_terms = [f'"{t}"' if " " in t else t for t in job_titles if t.strip()]
            if title_terms:
                payload["role_title"] = " OR ".join(title_terms)

        try:
            data = self._post(self._url("person_role_search/"), payload)
        except Exception as exc:
            logger.debug("Root person_role_search failed (%s), attempting fallback", exc)
            fallback_payload: dict[str, Any] = {
                "filters": {
                    "company_domain": clean_dom,
                },
                "page": 1,
                "page_size": max_results,
            }
            if job_titles:
                fallback_payload["filters"]["current_title"] = job_titles
            data = self._post(self._url("person_role_search/"), fallback_payload)

        results = (
            data.get("search_results")
            or data.get("results")
            or data.get("people")
            or []
        )

        enriched: list[dict[str, Any]] = []
        for item in results[:max_results]:
            if not isinstance(item, dict):
                continue

            # Role search records typically wrap the person profile inside item["person"]
            if "person" in item and isinstance(item["person"], dict):
                person_data = dict(item["person"])
                person_data.setdefault("role_title", item.get("role_title"))
                person_data.setdefault("current_title", item.get("role_title"))
                if "organization" in item and isinstance(item["organization"], dict):
                    person_data.setdefault("company_name", item["organization"].get("name"))
                    person_data.setdefault("company_domain", item["organization"].get("domain") or clean_dom)
                    if item["organization"].get("linkedin_public_identifier"):
                        person_data.setdefault(
                            "company_linkedin_url",
                            f"https://www.linkedin.com/company/{item['organization']['linkedin_public_identifier']}",
                        )
            else:
                person_data = item
                person_data.setdefault("company_domain", clean_dom)

            contact = self._enrich_contact_contacts(person_data)
            if contact:
                enriched.append(contact)

        return enriched
