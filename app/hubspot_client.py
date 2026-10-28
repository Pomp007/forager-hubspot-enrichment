"""
HubSpot CRM client — compatible with hubspot-api-client v12.

Uses the hubspot-api-client SDK and the v3 REST API.
All CRM operations:
  - Get/update Company records
  - Get/update/create Contact records
  - Associate contacts to companies
"""

from __future__ import annotations

import logging
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
    PublicAssociationMultiPost,
    BatchInputPublicAssociationMultiPost,
)
from flask import current_app

logger = logging.getLogger(__name__)


class HubSpotClient:
    """Wrapper around the HubSpot CRM API (hubspot-api-client v12)."""

    def __init__(self) -> None:
        token = current_app.config["HUBSPOT_ACCESS_TOKEN"]
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
        props: dict[str, str] = {}

        def _set(hs_key: str, value: Any) -> None:
            if value is not None:
                props[hs_key] = str(value)

        _set("name", enriched.get("name"))
        _set("domain", enriched.get("domain"))
        _set("description", enriched.get("description"))
        _set("industry", enriched.get("industry"))
        _set("numberofemployees", enriched.get("headcount"))
        _set("annualrevenue", enriched.get("revenue"))
        _set("city", enriched.get("city"))
        _set("state", enriched.get("state"))
        _set("country", enriched.get("country"))
        _set("linkedin_company_page", enriched.get("linkedin_url"))
        _set("phone", enriched.get("phone"))
        _set("type", enriched.get("type"))
        _set("forager_headcount", enriched.get("headcount"))
        _set("forager_revenue", enriched.get("revenue"))
        _set("forager_enriched", "true")

        if enriched.get("specialties"):
            v = enriched["specialties"]
            _set("forager_specialties", ", ".join(v) if isinstance(v, list) else v)
        if enriched.get("technologies"):
            v = enriched["technologies"]
            _set("forager_technologies", ", ".join(v) if isinstance(v, list) else v)

        if not props:
            logger.info("No enrichment props to write for company %s", company_id)
            return {}

        try:
            result = self.client.crm.companies.basic_api.update(
                company_id=company_id,
                simple_public_object_input=SimplePublicObjectInput(properties=props),
            )
            logger.info("Updated HubSpot company %s with %d props", company_id, len(props))
            return result.to_dict()
        except CompanyApiException as exc:
            logger.error("HubSpot update_company %s failed: %s", company_id, exc)
            raise

    def get_company_contacts(self, company_id: str) -> list[dict[str, Any]]:
        """Return all contacts associated with a HubSpot company."""
        try:
            result = self.client.crm.companies.associations_api.get_all(
                company_id=company_id,
                to_object_type="contacts",
            )
            associations = result.results or []
            contacts = []
            for assoc in associations:
                try:
                    contact = self.get_contact(assoc.id)
                    contacts.append(contact)
                except Exception as exc:
                    logger.warning("Failed to fetch contact %s: %s", assoc.id, exc)
            return contacts
        except Exception as exc:
            logger.error("HubSpot get_company_contacts %s failed: %s", company_id, exc)
            return []

    def search_companies_by_domain(self, domain: str) -> list[dict[str, Any]]:
        """Find HubSpot companies by domain."""
        f = Filter(property_name="domain", operator="EQ", value=domain)
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
        "linkedin_url",
        "forager_company_domain", "forager_company_linkedin",
        "forager_description", "forager_about", "forager_enriched",
        "forager_work_emails", "forager_personal_emails", "forager_all_phones",
    ]

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        """Fetch a HubSpot contact by ID."""
        try:
            result = self.client.crm.contacts.basic_api.get_by_id(
                contact_id=contact_id,
                properties=self._CONTACT_PROPS,
            )
            return result.to_dict()
        except ContactApiException as exc:
            logger.error("HubSpot get_contact %s failed: %s", contact_id, exc)
            raise

    def update_contact(self, contact_id: str, enriched: dict[str, Any]) -> dict[str, Any]:
        """Write enrichment data back to a HubSpot contact record."""
        props: dict[str, str] = {}

        def _set(hs_key: str, value: Any) -> None:
            if value is not None:
                props[hs_key] = str(value)

        _set("firstname", enriched.get("first_name"))
        _set("lastname", enriched.get("last_name"))
        _set("email", enriched.get("email"))
        _set("phone", enriched.get("phone"))
        _set("jobtitle", enriched.get("job_title"))
        _set("company", enriched.get("company_name"))
        _set("city", enriched.get("city"))
        _set("state", enriched.get("state"))
        _set("country", enriched.get("country"))
        _set("linkedin_url", enriched.get("linkedin_url"))
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

        if not props:
            return {}

        try:
            result = self.client.crm.contacts.basic_api.update(
                contact_id=contact_id,
                simple_public_object_input=ContactInput(properties=props),
            )
            logger.info("Updated HubSpot contact %s with %d props", contact_id, len(props))
            return result.to_dict()
        except ContactApiException as exc:
            logger.error("HubSpot update_contact %s failed: %s", contact_id, exc)
            raise

    def create_contact(
        self, enriched: dict[str, Any], company_id: str | None = None
    ) -> dict[str, Any]:
        """Create a new HubSpot contact from enriched data and optionally associate to a company."""
        props: dict[str, str] = {}

        def _set(hs_key: str, value: Any) -> None:
            if value is not None:
                props[hs_key] = str(value)

        _set("firstname", enriched.get("first_name"))
        _set("lastname", enriched.get("last_name"))
        _set("email", enriched.get("email"))
        _set("phone", enriched.get("phone"))
        _set("jobtitle", enriched.get("job_title"))
        _set("company", enriched.get("company_name"))
        _set("city", enriched.get("city"))
        _set("state", enriched.get("state"))
        _set("country", enriched.get("country"))
        _set("linkedin_url", enriched.get("linkedin_url"))
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

        try:
            result = self.client.crm.contacts.basic_api.create(
                simple_public_object_input_for_create=ContactInputForCreate(
                    properties=props,
                    associations=[],
                )
            )
            contact_id = result.id
            logger.info("Created HubSpot contact %s (%s)", contact_id, enriched.get("full_name"))

            if company_id:
                self._associate_contact_to_company(contact_id, company_id)

            return result.to_dict()
        except ContactApiException as exc:
            # If contact already exists (duplicate email), try to update instead
            if exc.status == 409:
                logger.warning(
                    "Contact with email %s already exists — attempting update via search",
                    enriched.get("email"),
                )
                existing = self.search_contacts_by_email(enriched.get("email", ""))
                if existing:
                    existing_id = existing[0].get("id")
                    return self.update_contact(existing_id, enriched)
            logger.error("HubSpot create_contact failed: %s", exc)
            raise

    def _associate_contact_to_company(self, contact_id: str, company_id: str) -> None:
        """Create a HubSpot association between a contact and a company using v4 API."""
        try:
            self.client.crm.associations.v4.basic_api.create(
                object_type="contacts",
                object_id=contact_id,
                to_object_type="companies",
                to_object_id=company_id,
                association_spec=[
                    AssociationSpec(
                        association_category="HUBSPOT_DEFINED",
                        association_type_id=280,  # Contact → Company (primary)
                    )
                ],
            )
            logger.info("Associated contact %s → company %s", contact_id, company_id)
        except Exception as exc:
            logger.warning(
                "Could not associate contact %s to company %s: %s",
                contact_id, company_id, exc,
            )

    def search_contacts_by_email(self, email: str) -> list[dict[str, Any]]:
        """Find HubSpot contacts by email address."""
        f = ContactFilter(property_name="email", operator="EQ", value=email)
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
