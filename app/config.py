"""
Central configuration — all sensitive values pulled from environment variables.
Never hard-code secrets here.
"""

import os


class Config:
    # ── Flask ──────────────────────────────────────────────────────────────────
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    DEBUG: bool = os.environ.get("DEBUG", "false").lower() == "true"

    # ── Forager ───────────────────────────────────────────────────────────────
    FORAGER_API_KEY: str = os.environ.get("FORAGER_API_KEY", "")
    FORAGER_ACCOUNT_ID: str = os.environ.get("FORAGER_ACCOUNT_ID", "")
    FORAGER_BASE_URL: str = os.environ.get("FORAGER_BASE_URL", "https://api-v2.forager.ai/api").rstrip("/")

    # ── HubSpot ───────────────────────────────────────────────────────────────
    HUBSPOT_ACCESS_TOKEN: str = os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
    HUBSPOT_PORTAL_ID: str = os.environ.get("HUBSPOT_PORTAL_ID", "")
    HUBSPOT_CLIENT_SECRET: str = os.environ.get("HUBSPOT_CLIENT_SECRET", "")

    # ── Contact-finding defaults ──────────────────────────────────────────────
    # Comma-separated list of job titles used when discovering contacts at a company.
    # Override via env var: DEFAULT_JOB_TITLES="CTO,VP Engineering,Software Engineer"
    DEFAULT_JOB_TITLES: list[str] = [
        t.strip()
        for t in os.environ.get(
            "DEFAULT_JOB_TITLES",
            "CTO,VP Engineering,VP of Engineering,Software Engineer,Product Manager",
        ).split(",")
        if t.strip()
    ]

    # Maximum contacts to surface per company during auto-discovery
    MAX_CONTACTS_PER_COMPANY: int = int(
        os.environ.get("MAX_CONTACTS_PER_COMPANY", "5")
    )

    # ── Webhook security ──────────────────────────────────────────────────────
    # Optional: shared secret to validate incoming webhook requests
    WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")
