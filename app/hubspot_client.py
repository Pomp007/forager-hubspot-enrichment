"""
HubSpot CRM client — compatible with hubspot-api-client v12.

Uses the hubspot-api-client SDK and the v3/v4 REST API.
All CRM operations:
  - Get/update/create Company records
  - Get/update/create Contact records
  - Associate contacts to companies (v4 associations API)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import hubspot
from hubspot.crm.companies import (
    SimplePublicObjectInput,
    SimplePublicObjectInputForCreate,
    PublicObjectSearchRequest,
    Filter,
    FilterGroup,
    ApiException as CompanyApiException,
)
from hubspot.crm.contacts import (
    SimplePublicObjectInput as ContactInput,
    SimplePublicObjectInputForCreate as ContactInputForCreate,
    PublicObjectSearchRequest as ContactSearchRequest,
    Filter as ContactFilter,
    FilterGroup as ContactFilterGroup,
    ApiException as ContactApiException,
)
from hubspot.crm.associations.v4 import (
    AssociationSpec,
)
from flask import current_app

logger = logging.getLogger(__name__)


def _to_numeric_str(val: Any) -> str | None:
    """Format numeric values safely for HubSpot number properties."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return str(int(val) if isinstance(val, int) or val.is_integer() else val)
    s = str(val).strip().replace(",", "")
    if s.isdigit():
        return s
    nums = re.findall(r"\d+", s)
    if nums:
        return nums[-1]
    return None


class HubSpotClient:
    """Wrapper around the HubSpot CRM API (hubspot-api-client v12)."""

    def __init__(self, access_token: str | None = None) -> None:
        token = access_token
        if not token:
            try:
                if current_app:
                    token = current_app.config.get("HUBSPOT_ACCESS_TOKEN")
            except RuntimeError:
                pass
        token = token or os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
        self.client = hubspot.Client.create(access_token=token)

    # ──────────────────────────────────────────────────────────────────────────
    # Company operations
    # ──────────────────────────────────────────────────────────────────────────

    _COMPANY_PROPS = [
        "name", "domain", "description", "industry", "numberofemployees",
        "annualrevenue", "city", "state", "country",
        "linkedin_company_page", "phone", "type", "website",
        "forager_headcount", "forager_revenue", "forager_specialties",
        "forager_technologies", "forager_enriched",
    ]

    def get_company(self, company_id: str) -> dict[str, Any]:
        """Fetch a HubSpot company by ID with all relevant properties."""
        try:
            result = self.client.crm.companies.basic_api.get_by_id(
                company_id=company_id,
                properties=self._COMPANY_PROPS,
            )
            return result.to_dict()
        except CompanyApiException as exc:
            logger.error("HubSpot get_company %s failed: %s", company_id, exc)
            raise

    def update_company(self, company_id: str, enriched: dict[str, Any]) -> dict[str, Any]:
        """Write enrichment data back to a HubSpot company record."""
        props = self._build_company_properties(enriched)
        if not props:
            logger.info("No enrichment props to write for company %s", company_id)
            return {}

        return self._execute_resilient_company_update(company_id, props)

    def create_company(self, enriched: dict[str, Any]) -> dict[str, Any]:
        """Create a new HubSpot company record populated with enrichment data."""
        props = self._build_company_properties(enriched)
        if not props.get("name") and not props.get("domain"):
            raise ValueError("Company must have at least a name or domain to create")

        return self._execute_resilient_company_create(props)

    def _build_company_properties(self, enriched: dict[str, Any]) -> dict[str, str]:
        """Map internal company dictionary to HubSpot properties."""
        props: dict[str, str] = {}

        def _set(hs_key: str, value: Any) -> None:
            if value is not None and str(value).strip():
                props[hs_key] = str(value).strip()

        _set("name", enriched.get("name"))
        _set("domain", enriched.get("domain"))
        _set("website", enriched.get("domain") or enriched.get("website"))
        _set("description", enriched.get("description"))
        _set("industry", enriched.get("industry"))
        _set("city", enriched.get("city"))
        _set("state", enriched.get("state"))
        _set("country", enriched.get("country"))
        _set("linkedin_company_page", enriched.get("linkedin_url"))
        _set("phone", enriched.get("phone"))
        _set("type", enriched.get("type"))
        _set("forager_enriched", "true")

        # Headcount & Revenue (support both standard and custom forager fields)
        num_emp = _to_numeric_str(enriched.get("headcount"))
        if num_emp:
            _set("numberofemployees", num_emp)
            _set("forager_headcount", num_emp)

        num_rev = _to_numeric_str(enriched.get("revenue"))
        if num_rev:
            _set("annualrevenue", num_rev)
            _set("forager_revenue", num_rev)

        if enriched.get("specialties"):
            v = enriched["specialties"]
            _set("forager_specialties", ", ".join(v) if isinstance(v, list) else str(v))
        if enriched.get("technologies"):
            v = enriched["technologies"]
            _set("forager_technologies", ", ".join(v) if isinstance(v, list) else str(v))

        return props

    def _execute_resilient_company_update(self, company_id: str, props: dict[str, str]) -> dict[str, Any]:
        """Update company with automatic retry if an unconfigured custom property causes a 400 error."""
        current_props = dict(props)
        for _ in range(5):
            try:
                result = self.client.crm.companies.basic_api.update(
                    company_id=company_id,
                    simple_public_object_input=SimplePublicObjectInput(properties=current_props),
                )
                logger.info("Updated HubSpot company %s with %d props", company_id, len(current_props))
                return result.to_dict()
            except CompanyApiException as exc:
                missing_prop = self._extract_missing_property(exc)
                if missing_prop and missing_prop in current_props:
                    logger.warning(
                        "Company property '%s' not present in HubSpot schema; dropping and retrying",
                        missing_prop,
                    )
                    del current_props[missing_prop]
                    continue
                logger.error("HubSpot update_company %s failed: %s", company_id, exc)
                raise
        return {}

    def _execute_resilient_company_create(self, props: dict[str, str]) -> dict[str, Any]:
        """Create company with automatic retry if an unconfigured custom property causes a 400 error."""
        current_props = dict(props)
        for _ in range(5):
            try:
                result = self.client.crm.companies.basic_api.create(
                    simple_public_object_input_for_create=SimplePublicObjectInputForCreate(
                        properties=current_props
                    )
                )
                logger.info("Created HubSpot company %s (%s)", result.id, current_props.get("name"))
                return result.to_dict()
            except CompanyApiException as exc:
                missing_prop = self._extract_missing_property(exc)
                if missing_prop and missing_prop in current_props:
                    logger.warning(
                        "Company property '%s' not present in HubSpot schema; dropping and retrying",
                        missing_prop,
                    )
                    del current_props[missing_prop]
                    continue
                logger.error("HubSpot create_company failed: %s", exc)
                raise
        return {}

    def get_company_contacts(self, company_id: str) -> list[dict[str, Any]]:
        """Return all contacts associated with a HubSpot company using the v4 associations API."""
        contacts: list[dict[str, Any]] = []
        try:
            # Dedicated v4 associations API
            page = self.client.crm.associations.v4.basic_api.get_page(
                object_type="companies",
                object_id=str(company_id),
                to_object_type="contacts",
            )
            associations = page.results or []
            for assoc in associations:
                contact_id = (
                    getattr(assoc, "to_object_id", None)
                    or getattr(assoc, "toObjectId", None)
                    or getattr(assoc, "id", None)
                )
                if contact_id:
                    try:
                        contact = self.get_contact(str(contact_id))
                        contacts.append(contact)
                    except Exception as fetch_exc:
                        logger.warning("Failed to fetch associated contact %s: %s", contact_id, fetch_exc)
            return contacts
        except Exception as exc:
            logger.warning(
                "associations.v4.get_page failed for company %s (%s); trying fallback get_by_id",
                company_id,
                exc,
            )
            try:
                company = self.client.crm.companies.basic_api.get_by_id(
                    company_id=str(company_id),
                    associations=["contacts"],
                )
                company_dict = company.to_dict()
                assocs = (
                    (company_dict.get("associations") or {})
                    .get("contacts", {})
                    .get("results", [])
                )
                for a in assocs:
                    c_id = a.get("id")
                    if c_id:
                        contacts.append(self.get_contact(str(c_id)))
                return contacts
            except Exception as fallback_exc:
                logger.error("Fallback get_company_contacts %s failed: %s", company_id, fallback_exc)
                return []

    def search_companies_by_domain(self, domain: str) -> list[dict[str, Any]]:
        """Find HubSpot companies by domain."""
        clean_domain = domain.strip().lower()
        f = Filter(property_name="domain", operator="EQ", value=clean_domain)
        fg = FilterGroup(filters=[f])
        req = PublicObjectSearchRequest(
            filter_groups=[fg],
            properties=self._COMPANY_PROPS,
        )
        try:
            result = self.client.crm.companies.search_api.do_search(
                public_object_search_request=req
            )
            return [r.to_dict() for r in (result.results or [])]
        except CompanyApiException as exc:
            logger.error("HubSpot search_companies_by_domain %s: %s", domain, exc)
            return []

    # ──────────────────────────────────────────────────────────────────────────
    # Contact operations
    # ──────────────────────────────────────────────────────────────────────────

    _CONTACT_PROPS = [
        "firstname", "lastname", "email", "phone",
        "jobtitle", "company", "website",
        "city", "state", "country",
        "hs_linkedin_url", "linkedin_url",
        "forager_company_domain", "forager_company_linkedin",
        "forager_description", "forager_about", "forager_enriched",
        "forager_work_emails", "forager_personal_emails", "forager_all_phones",
    ]

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        """Fetch a HubSpot contact by ID."""
        try:
            result = self.client.crm.contacts.basic_api.get_by_id(
                contact_id=str(contact_id),
                properties=self._CONTACT_PROPS,
            )
            return result.to_dict()
        except ContactApiException as exc:
            logger.error("HubSpot get_contact %s failed: %s", contact_id, exc)
            raise

    def update_contact(self, contact_id: str, enriched: dict[str, Any]) -> dict[str, Any]:
        """Write enrichment data back to a HubSpot contact record."""
        props = self._build_contact_properties(enriched)
        if not props:
            return {}

        return self._execute_resilient_contact_update(str(contact_id), props)

    def create_contact(
        self, enriched: dict[str, Any], company_id: str | None = None
    ) -> dict[str, Any]:
        """Create a new HubSpot contact from enriched data and optionally associate to a company."""
        props = self._build_contact_properties(enriched)
        contact_id = None
        result_dict: dict[str, Any] = {}

        current_props = dict(props)
        for _ in range(5):
            try:
                result = self.client.crm.contacts.basic_api.create(
                    simple_public_object_input_for_create=ContactInputForCreate(
                        properties=current_props,
                        associations=[],
                    )
                )
                contact_id = result.id
                result_dict = result.to_dict()
                logger.info("Created HubSpot contact %s (%s)", contact_id, enriched.get("full_name"))
                break
            except ContactApiException as exc:
                if exc.status == 409:
                    logger.warning(
                        "Contact with email %s already exists — attempting update via search",
                        enriched.get("email"),
                    )
                    existing = self.search_contacts_by_email(enriched.get("email", ""))
                    if existing:
                        existing_id = existing[0].get("id")
                        updated = self.update_contact(existing_id, enriched)
                        contact_id = existing_id
                        result_dict = updated
                        break

                missing_prop = self._extract_missing_property(exc)
                if missing_prop and missing_prop in current_props:
                    logger.warning(
                        "Contact property '%s' not present in HubSpot schema; dropping and retrying",
                        missing_prop,
                    )
                    del current_props[missing_prop]
                    continue
                logger.error("HubSpot create_contact failed: %s", exc)
                raise

        if contact_id and company_id:
            self._associate_contact_to_company(str(contact_id), str(company_id))

        return result_dict

    def _build_contact_properties(self, enriched: dict[str, Any]) -> dict[str, str]:
        """Map internal contact dictionary to HubSpot properties."""
        props: dict[str, str] = {}

        def _set(hs_key: str, value: Any) -> None:
            if value is not None and str(value).strip():
                props[hs_key] = str(value).strip()

        _set("firstname", enriched.get("first_name"))
        _set("lastname", enriched.get("last_name"))
        _set("email", enriched.get("email"))
        _set("phone", enriched.get("phone"))
        _set("jobtitle", enriched.get("job_title"))
        _set("company", enriched.get("company_name"))
        _set("city", enriched.get("city"))
        _set("state", enriched.get("state"))
        _set("country", enriched.get("country"))

        # Both native hs_linkedin_url and custom linkedin_url
        linkedin_url = enriched.get("linkedin_url")
        if linkedin_url:
            _set("hs_linkedin_url", linkedin_url)
            _set("linkedin_url", linkedin_url)

        _set("forager_company_domain", enriched.get("company_domain"))
        _set("forager_company_linkedin", enriched.get("company_linkedin_url"))
        _set("forager_description", enriched.get("description"))
        _set("forager_about", enriched.get("about"))
        _set("forager_enriched", "true")

        if enriched.get("work_emails"):
            _set("forager_work_emails", "; ".join(enriched["work_emails"]))
        if enriched.get("personal_emails"):
            _set("forager_personal_emails", "; ".join(enriched["personal_emails"]))
        if enriched.get("all_phones"):
            _set("forager_all_phones", "; ".join(enriched["all_phones"]))

        return props

    def _execute_resilient_contact_update(self, contact_id: str, props: dict[str, str]) -> dict[str, Any]:
        """Update contact with automatic retry if an unconfigured custom property causes a 400 error."""
        current_props = dict(props)
        for _ in range(5):
            try:
                result = self.client.crm.contacts.basic_api.update(
                    contact_id=contact_id,
                    simple_public_object_input=ContactInput(properties=current_props),
                )
                logger.info("Updated HubSpot contact %s with %d props", contact_id, len(current_props))
                return result.to_dict()
            except ContactApiException as exc:
                missing_prop = self._extract_missing_property(exc)
                if missing_prop and missing_prop in current_props:
                    logger.warning(
                        "Contact property '%s' not present in HubSpot schema; dropping and retrying",
                        missing_prop,
                    )
                    del current_props[missing_prop]
                    continue
                logger.error("HubSpot update_contact %s failed: %s", contact_id, exc)
                raise
        return {}

    def _associate_contact_to_company(self, contact_id: str, company_id: str) -> None:
        """Create a HubSpot association between a contact and a company using the v4 API."""
        try:
            # Standard v4 default association
            self.client.crm.associations.v4.basic_api.create_default(
                from_object_type="contacts",
                from_object_id=str(contact_id),
                to_object_type="companies",
                to_object_id=str(company_id),
            )
            logger.info("Associated contact %s → company %s", contact_id, company_id)
        except Exception as exc:
            # Fallback using explicit AssociationSpec
            try:
                self.client.crm.associations.v4.basic_api.create(
                    object_type="contacts",
                    object_id=str(contact_id),
                    to_object_type="companies",
                    to_object_id=str(company_id),
                    association_spec=[
                        AssociationSpec(
                            association_category="HUBSPOT_DEFINED",
                            association_type_id=280,
                        )
                    ],
                )
                logger.info("Associated contact %s → company %s (fallback spec)", contact_id, company_id)
            except Exception as fallback_exc:
                logger.warning(
                    "Could not associate contact %s to company %s: %s | fallback error: %s",
                    contact_id,
                    company_id,
                    exc,
                    fallback_exc,
                )

    def search_contacts_by_email(self, email: str) -> list[dict[str, Any]]:
        """Find HubSpot contacts by email address."""
        if not email:
            return []
        f = ContactFilter(property_name="email", operator="EQ", value=email.strip())
        fg = ContactFilterGroup(filters=[f])
        req = ContactSearchRequest(
            filter_groups=[fg],
            properties=self._CONTACT_PROPS,
        )
        try:
            result = self.client.crm.contacts.search_api.do_search(
                public_object_search_request=req
            )
            return [r.to_dict() for r in (result.results or [])]
        except ContactApiException as exc:
            logger.error("HubSpot search_contacts_by_email %s: %s", email, exc)
            return []

    @staticmethod
    def _extract_missing_property(exc: Any) -> str | None:
        """Parse error message to extract unrecognized or invalid property name that caused a 400."""
        msg = str(getattr(exc, "body", "") or str(exc))
        try:
            b = json.loads(getattr(exc, "body", "") or "{}")
            for err in b.get("errors", []):
                prop_list = (err.get("context") or {}).get("propertyName")
                if prop_list and isinstance(prop_list, list) and prop_list[0]:
                    return prop_list[0]
                if err.get("name"):
                    return err.get("name")
        except Exception:
            pass

        # Common regex patterns: Property "xyz" does not exist / invalid option
        match = re.search(r'Property ["\']?([a-zA-Z0-9_]+)["\']? does not exist', msg)
        if match:
            return match.group(1)
        match = re.search(r'["\']name["\']:\s*["\']([a-zA-Z0-9_]+)["\']', msg)
        if match:
            return match.group(1)
        match = re.search(r'([a-zA-Z0-9_]+) was not valid for [a-zA-Z0-9_]+', msg)
        if match:
            return match.group(1)
        return None
