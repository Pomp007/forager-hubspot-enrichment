"""
REST API routes for manual triggering and testing.

All endpoints require the X-API-KEY header matching WEBHOOK_SECRET env var
(if set) for basic protection.

Endpoints:
  POST /api/enrich/company          — enrich a company by HubSpot ID or domain
  POST /api/enrich/contact          — enrich a contact by HubSpot ID, email, or LinkedIn
  POST /api/find-contacts           — discover & create contacts at a company
  POST /api/demo/openai             — run the full OpenAI demo enrichment
  GET  /api/status                  — health + config summary
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from .enrichment_service import (
    enrich_hubspot_company,
    enrich_hubspot_contact,
    enrich_company_by_domain_and_update_hs,
    find_and_create_contacts,
    handle_company_created,
)

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)


# ──────────────────────────────────────────────────────────────────────────────
# Auth guard
# ──────────────────────────────────────────────────────────────────────────────


def require_api_key(f):
    """Protect API routes with a simple bearer / header key check."""
    @wraps(f)
    def decorated(*args, **kwargs):
        secret = current_app.config.get("WEBHOOK_SECRET")
        if secret:
            provided = (
                request.headers.get("X-API-KEY")
                or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            )
            if provided != secret:
                return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────


@api_bp.route("/status", methods=["GET"])
def status():
    """Return service health and non-sensitive config."""
    cfg = current_app.config
    return jsonify(
        {
            "status": "ok",
            "forager_configured": bool(cfg.get("FORAGER_API_KEY")),
            "hubspot_configured": bool(cfg.get("HUBSPOT_ACCESS_TOKEN")),
            "default_job_titles": cfg.get("DEFAULT_JOB_TITLES"),
            "max_contacts_per_company": cfg.get("MAX_CONTACTS_PER_COMPANY"),
        }
    )


@api_bp.route("/enrich/company", methods=["POST"])
@require_api_key
def enrich_company():
    """
    Enrich a company by HubSpot ID or domain.

    Body (JSON):
      { "company_id": "123" }              — enrich existing HubSpot record
      { "domain": "openai.com" }           — look up in HubSpot by domain, then enrich
      { "domain": "openai.com", "company_id": "123" }  — direct update
    """
    body = request.get_json(force=True, silent=True) or {}
    company_id: str = body.get("company_id", "")
    domain: str = body.get("domain", "")

    if not company_id and not domain:
        return jsonify({"error": "Provide company_id or domain"}), 400

    try:
        if company_id:
            result = enrich_hubspot_company(company_id)
        else:
            result = enrich_company_by_domain_and_update_hs(domain)
        return jsonify(result)
    except Exception as exc:
        logger.exception("enrich_company error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/enrich/contact", methods=["POST"])
@require_api_key
def enrich_contact():
    """
    Enrich a contact by HubSpot contact_id.

    Body: { "contact_id": "456" }
    """
    body = request.get_json(force=True, silent=True) or {}
    contact_id: str = body.get("contact_id", "")

    if not contact_id:
        return jsonify({"error": "Provide contact_id"}), 400

    try:
        result = enrich_hubspot_contact(contact_id)
        return jsonify(result)
    except Exception as exc:
        logger.exception("enrich_contact error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/find-contacts", methods=["POST"])
@require_api_key
def find_contacts():
    """
    Discover contacts at a company domain and create them in HubSpot.

    Body:
    {
      "company_domain": "openai.com",
      "company_id": "123",          // optional — associates new contacts
      "job_titles": ["CTO", "VP Engineering"],  // optional — overrides default
      "max_results": 5              // optional
    }
    """
    body = request.get_json(force=True, silent=True) or {}
    domain: str = body.get("company_domain", "")
    company_id: str | None = body.get("company_id")
    job_titles: list[str] | None = body.get("job_titles")
    max_results: int = int(body.get("max_results", current_app.config["MAX_CONTACTS_PER_COMPANY"]))

    if not domain:
        return jsonify({"error": "Provide company_domain"}), 400

    try:
        result = find_and_create_contacts(
            company_domain=domain,
            company_id=company_id,
            job_titles=job_titles,
            max_results=max_results,
        )
        return jsonify(result)
    except Exception as exc:
        logger.exception("find_contacts error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/demo/openai", methods=["POST"])
@require_api_key
def demo_openai():
    """
    Run the full OpenAI demo enrichment:
    1. Enrich the OpenAI company record in HubSpot (by domain 'openai.com').
    2. Find / enrich 5 contacts with Engineering & Product titles.

    Body: { "company_id": "hs_openai_company_id" }   // optional
    """
    body = request.get_json(force=True, silent=True) or {}
    company_id: str | None = body.get("company_id")

    try:
        # Step 1: enrich company
        if company_id:
            company_result = enrich_hubspot_company(company_id)
        else:
            company_result = enrich_company_by_domain_and_update_hs("openai.com")
            company_id = company_result.get("company_id")

        # Step 2: find & create 5 contacts
        contacts_result = find_and_create_contacts(
            company_domain="openai.com",
            company_id=company_id,
            job_titles=[
                "CTO", "VP Engineering", "VP of Engineering",
                "Software Engineer", "Product Manager", "Director of Engineering",
                "Machine Learning Engineer", "Research Scientist",
            ],
            max_results=5,
        )

        return jsonify(
            {
                "status": "demo_complete",
                "company": company_result,
                "contacts": contacts_result,
            }
        )
    except Exception as exc:
        logger.exception("demo_openai error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/trigger/company-webhook", methods=["POST"])
@require_api_key
def trigger_company_webhook():
    """
    Manually simulate a company.creation webhook for a given HubSpot company ID.
    Useful for testing the full automation flow without waiting for a real webhook.

    Body: { "company_id": "123" }
    """
    body = request.get_json(force=True, silent=True) or {}
    company_id: str = body.get("company_id", "")
    if not company_id:
        return jsonify({"error": "Provide company_id"}), 400

    try:
        result = handle_company_created(company_id)
        return jsonify(result)
    except Exception as exc:
        logger.exception("trigger_company_webhook error: %s", exc)
        return jsonify({"error": str(exc)}), 500
