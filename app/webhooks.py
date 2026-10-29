"""
HubSpot Webhook receivers.

HubSpot sends an ARRAY of event objects to the target URL.
Each event contains:
  - subscriptionType: e.g. "contact.creation" or "company.creation"
  - objectId: HubSpot object ID

We validate the request, dispatch to the enrichment service, and return 200.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

from flask import Blueprint, current_app, jsonify, request

from .enrichment_service import handle_company_created, enrich_hubspot_contact

logger = logging.getLogger(__name__)

webhooks_bp = Blueprint("webhooks", __name__)


# ──────────────────────────────────────────────────────────────────────────────
# Signature validation
# ──────────────────────────────────────────────────────────────────────────────


def _validate_hubspot_signature(req: request) -> bool:
    """
    Validate the HubSpot V3 webhook signature.

    HubSpot signs requests with:
      base64( HMAC-SHA256( client_secret, HTTP_METHOD + URI + request_body + timestamp ) )

    Returns True if HUBSPOT_CLIENT_SECRET is not configured (skips validation) or if valid.
    """
    secret = current_app.config.get("HUBSPOT_CLIENT_SECRET")
    if not secret:
        # If no client secret is set, skip signature validation (convenient for testing & initial setup)
        return True

    signature = req.headers.get("X-HubSpot-Signature-v3")
    timestamp = req.headers.get("X-HubSpot-Request-Timestamp")

    if not signature or not timestamp:
        logger.warning("Missing HubSpot signature headers while HUBSPOT_CLIENT_SECRET is configured")
        return False

    # Reject requests with timestamp older than 5 minutes
    try:
        ts_seconds = float(timestamp) / 1000.0 if len(timestamp) > 10 else float(timestamp)
        if abs(time.time() - ts_seconds) > 300:
            logger.warning("HubSpot webhook timestamp too old")
            return False
    except (ValueError, TypeError):
        return False

    raw_body = req.get_data(as_text=True)
    message = f"{req.method}{req.url}{raw_body}{timestamp}"
    computed_digest = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    expected = base64.b64encode(computed_digest).decode("utf-8")

    return hmac.compare_digest(expected, signature)


# ──────────────────────────────────────────────────────────────────────────────
# Webhook endpoint
# ──────────────────────────────────────────────────────────────────────────────


@webhooks_bp.route("/hubspot", methods=["POST"])
def hubspot_webhook():
    """
    Unified HubSpot webhook endpoint.
    Handles both company.creation and contact.creation events.
    """
    if not _validate_hubspot_signature(request):
        return jsonify({"error": "Invalid signature"}), 401

    payload = request.get_json(force=True, silent=True)
    if not payload:
        return jsonify({"error": "Empty or invalid JSON body"}), 400

    # HubSpot sends an array of events
    events = payload if isinstance(payload, list) else [payload]

    results = []
    for event in events:
        subscription_type: str = event.get("subscriptionType", "")
        object_id: str = str(event.get("objectId", ""))

        logger.info("HubSpot webhook event: %s id=%s", subscription_type, object_id)

        try:
            if subscription_type == "company.creation":
                result = handle_company_created(object_id)
                results.append({"event": subscription_type, "id": object_id, "result": result})

            elif subscription_type == "contact.creation":
                result = enrich_hubspot_contact(object_id)
                results.append({"event": subscription_type, "id": object_id, "result": result})

            else:
                logger.debug("Ignored event type: %s", subscription_type)
                results.append({"event": subscription_type, "id": object_id, "result": "ignored"})

        except Exception as exc:
            logger.exception(
                "Error processing %s id=%s: %s", subscription_type, object_id, exc
            )
            results.append(
                {"event": subscription_type, "id": object_id, "error": str(exc)}
            )

    return jsonify({"status": "ok", "processed": len(results), "results": results}), 200


@webhooks_bp.route("/health", methods=["GET"])
def health():
    """Simple liveness probe for Railway / load balancers."""
    return jsonify({"status": "healthy"}), 200
