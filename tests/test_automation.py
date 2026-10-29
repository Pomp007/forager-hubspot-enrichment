"""
Comprehensive test suite for Forager x HubSpot Enrichment Automation.
Tests all clients, services, webhooks, and API routes using standard library unittest.
"""

import base64
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import Config
from app.forager_client import (
    ForagerClient,
    _clean_domain,
    _extract_items,
    _to_numeric,
)
from app.hubspot_client import HubSpotClient, _to_numeric_str
from app.enrichment_service import (
    enrich_hubspot_company,
    enrich_hubspot_contact,
    handle_company_created,
    enrich_company_by_domain_and_update_hs,
    find_and_create_contacts,
)


class TestConfig(Config):
    TESTING = True
    DEBUG = False
    SECRET_KEY = "test-secret"
    FORAGER_API_KEY = "test-forager-key"
    FORAGER_ACCOUNT_ID = "test-account-123"
    FORAGER_BASE_URL = "https://api-v2.forager.ai/api"
    HUBSPOT_ACCESS_TOKEN = "test-hubspot-token"
    HUBSPOT_PORTAL_ID = "999999"
    HUBSPOT_CLIENT_SECRET = "test-hs-client-secret"
    DEFAULT_JOB_TITLES = ["CTO", "VP Engineering", "Software Engineer"]
    MAX_CONTACTS_PER_COMPANY = 3
    WEBHOOK_SECRET = "test-webhook-secret"


class ForagerClientTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.client = ForagerClient()

    def tearDown(self):
        self.ctx.pop()

    def test_clean_domain(self):
        self.assertEqual(_clean_domain("https://openai.com/"), "openai.com")
        self.assertEqual(_clean_domain("http://www.openai.com/about"), "openai.com")
        self.assertEqual(_clean_domain("WWW.OPENAI.COM"), "openai.com")
        self.assertEqual(_clean_domain("openai.com:443"), "openai.com")
        self.assertEqual(_clean_domain(""), "")

    def test_to_numeric(self):
        self.assertEqual(_to_numeric(500), 500)
        self.assertEqual(_to_numeric("1,250"), 1250)
        self.assertEqual(_to_numeric("201-500"), 500)
        self.assertEqual(_to_numeric(None), None)

    def test_extract_items(self):
        # Plain list of strings
        self.assertEqual(_extract_items(["+1234567890"], "phones"), ["+1234567890"])
        # List of dicts
        self.assertEqual(
            _extract_items([{"phone_number": "+14155552671"}], "phone_numbers"),
            ["+14155552671"],
        )
        # Dict with data key
        data_dict = {
            "status": "success",
            "data": {"personal_emails": [{"email": "user@gmail.com"}]},
        }
        self.assertEqual(_extract_items(data_dict, "personal_emails"), ["user@gmail.com"])

    @patch("requests.Session.post")
    def test_enrich_company_by_domain(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "search_results": [
                {
                    "name": "OpenAI",
                    "domain": "openai.com",
                    "description": "AI research company",
                    "headcount": 1000,
                    "annual_revenue": 2000000000,
                    "founded_year": 2015,
                    "location": {
                        "city": "San Francisco",
                        "state": "California",
                        "country": "United States",
                    },
                    "linkedin_info": {"public_identifier": "openai"},
                    "technographics": [{"name": "Python"}, {"name": "Kubernetes"}],
                }
            ]
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        res = self.client.enrich_company_by_domain("https://openai.com/")

        self.assertEqual(res["name"], "OpenAI")
        self.assertEqual(res["domain"], "openai.com")
        self.assertEqual(res["city"], "San Francisco")
        self.assertEqual(res["state"], "California")
        self.assertEqual(res["country"], "United States")
        self.assertEqual(res["linkedin_url"], "https://www.linkedin.com/company/openai")
        self.assertEqual(res["technologies"], ["Python", "Kubernetes"])
        self.assertEqual(res["headcount"], 1000)

    @patch("requests.Session.post")
    def test_find_contacts_at_company(self, mock_post):
        # 1st call: person_role_search, 2nd & 3rd: phones / emails
        def post_side_effect(url, **kwargs):
            m = MagicMock()
            m.raise_for_status.return_value = None
            if "person_role_search" in url:
                m.json.return_value = {
                    "search_results": [
                        {
                            "id": 1,
                            "role_title": "Software Engineer",
                            "person": {
                                "id": 101,
                                "first_name": "Greg",
                                "last_name": "Brockman",
                                "linkedin_public_identifier": "greg-brockman",
                                "summary": "President and Co-founder",
                                "locality": "San Francisco",
                                "region": "California",
                                "country": "United States",
                            },
                            "organization": {
                                "name": "OpenAI",
                                "domain": "openai.com",
                                "linkedin_public_identifier": "openai",
                            },
                        }
                    ]
                }
            elif "phone_numbers" in url:
                m.json.return_value = [{"phone_number": "+14155550100"}]
            elif "personal_emails" in url:
                m.json.return_value = [{"email": "greg@example.com"}]
            elif "work_emails" in url:
                m.json.return_value = {"work_emails": ["greg@openai.com"]}
            return m

        mock_post.side_effect = post_side_effect

        contacts = self.client.find_contacts_at_company("openai.com", ["Software Engineer"], max_results=1)

        self.assertEqual(len(contacts), 1)
        c = contacts[0]
        self.assertEqual(c["first_name"], "Greg")
        self.assertEqual(c["last_name"], "Brockman")
        self.assertEqual(c["job_title"], "Software Engineer")
        self.assertEqual(c["company_name"], "OpenAI")
        self.assertEqual(c["company_domain"], "openai.com")
        self.assertEqual(c["city"], "San Francisco")
        self.assertEqual(c["state"], "California")
        self.assertEqual(c["country"], "United States")
        self.assertEqual(c["phone"], "+14155550100")
        self.assertIn("greg@openai.com", c["work_emails"])


class HubSpotClientTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    @patch("hubspot.Client.create")
    def test_get_company_contacts_v4(self, mock_hs_factory):
        mock_hs = MagicMock()
        mock_hs_factory.return_value = mock_hs

        # Mock v4 get_page
        mock_assoc = MagicMock()
        mock_assoc.to_object_id = 998877
        page_mock = MagicMock()
        page_mock.results = [mock_assoc]
        mock_hs.crm.associations.v4.basic_api.get_page.return_value = page_mock

        # Mock get_contact
        contact_res = MagicMock()
        contact_res.to_dict.return_value = {"id": "998877", "properties": {"firstname": "Test"}}
        mock_hs.crm.contacts.basic_api.get_by_id.return_value = contact_res

        client = HubSpotClient("test-token")
        contacts = client.get_company_contacts("12345")

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["id"], "998877")
        mock_hs.crm.associations.v4.basic_api.get_page.assert_called_once_with(
            object_type="companies",
            object_id="12345",
            to_object_type="contacts",
        )

    @patch("hubspot.Client.create")
    def test_associate_contact_to_company_v4(self, mock_hs_factory):
        mock_hs = MagicMock()
        mock_hs_factory.return_value = mock_hs

        client = HubSpotClient("test-token")
        client._associate_contact_to_company("456", "123")

        mock_hs.crm.associations.v4.basic_api.create_default.assert_called_once_with(
            from_object_type="contacts",
            from_object_id="456",
            to_object_type="companies",
            to_object_id="123",
        )


class EnrichmentServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    @patch("app.enrichment_service.HubSpotClient")
    @patch("app.enrichment_service.ForagerClient")
    def test_enrich_hubspot_company_already_enriched(self, mock_forager_cls, mock_hs_cls):
        mock_hs = MagicMock()
        mock_hs_cls.return_value = mock_hs
        mock_hs.get_company.return_value = {
            "id": "123",
            "properties": {"name": "OpenAI", "forager_enriched": "true"},
        }

        res = enrich_hubspot_company("123", force=False)
        self.assertEqual(res["status"], "already_enriched")
        mock_forager_cls.assert_not_called()

    @patch("app.enrichment_service.HubSpotClient")
    @patch("app.enrichment_service.ForagerClient")
    def test_handle_company_created_without_existing_contacts(self, mock_forager_cls, mock_hs_cls):
        mock_hs = MagicMock()
        mock_hs_cls.return_value = mock_hs
        mock_forager = MagicMock()
        mock_forager_cls.return_value = mock_forager

        # Step 1: enrich company
        mock_hs.get_company.return_value = {
            "id": "123",
            "properties": {"name": "OpenAI", "domain": "openai.com"},
        }
        mock_forager.enrich_company_by_domain.return_value = {
            "name": "OpenAI",
            "domain": "openai.com",
            "description": "AI lab",
        }

        # Step 2: no existing contacts in HubSpot
        mock_hs.get_company_contacts.return_value = []

        # Step 3: discover contacts
        mock_forager.find_contacts_at_company.return_value = [
            {
                "first_name": "Mira",
                "last_name": "Murati",
                "full_name": "Mira Murati",
                "job_title": "CTO",
                "email": "mira@openai.com",
                "phone": "+14155551111",
                "linkedin_url": "https://www.linkedin.com/in/mira-murati",
                "city": "San Francisco",
                "state": "California",
                "country": "United States",
            }
        ]
        mock_hs.create_contact.return_value = {"id": "999"}

        res = handle_company_created("123")

        self.assertEqual(res["company_id"], "123")
        self.assertEqual(len(res["contacts_created"]), 1)
        self.assertEqual(res["contacts_created"][0]["hubspot_id"], "999")
        self.assertEqual(res["contacts_created"][0]["name"], "Mira Murati")

    @patch("app.enrichment_service.HubSpotClient")
    @patch("app.enrichment_service.ForagerClient")
    def test_handle_company_created_with_existing_contacts(self, mock_forager_cls, mock_hs_cls):
        mock_hs = MagicMock()
        mock_hs_cls.return_value = mock_hs
        mock_forager = MagicMock()
        mock_forager_cls.return_value = mock_forager

        # Company record
        mock_hs.get_company.return_value = {
            "id": "123",
            "properties": {"name": "OpenAI", "domain": "openai.com"},
        }
        mock_forager.enrich_company_by_domain.return_value = {"name": "OpenAI"}

        # Existing contacts
        mock_hs.get_company_contacts.return_value = [
            {"id": "501", "properties": {"firstname": "Existing", "email": "exist@openai.com"}}
        ]
        mock_hs.get_contact.return_value = {
            "id": "501",
            "properties": {"firstname": "Existing", "email": "exist@openai.com"},
        }
        mock_forager.enrich_contact_by_email.return_value = {
            "first_name": "Existing",
            "email": "exist@openai.com",
            "job_title": "Staff Engineer",
        }

        res = handle_company_created("123")

        self.assertEqual(res["company_id"], "123")
        self.assertEqual(len(res["contacts_enriched"]), 1)
        self.assertEqual(len(res["contacts_created"]), 0)
        mock_forager.find_contacts_at_company.assert_not_called()


class WebhooksAndApiTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.client = self.app.test_client()

    def test_health_check(self):
        resp = self.client.get("/webhooks/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "healthy")

    def test_api_status(self):
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["forager_configured"])
        self.assertTrue(data["hubspot_configured"])

    def test_api_auth_guard_unauthorized(self):
        resp = self.client.post("/api/enrich/company", json={"domain": "openai.com"})
        self.assertEqual(resp.status_code, 401)

    @patch("app.webhooks.handle_company_created")
    def test_webhook_company_creation(self, mock_handle_company):
        mock_handle_company.return_value = {"status": "ok"}
        payload = [
            {
                "subscriptionType": "company.creation",
                "objectId": "12345",
            }
        ]

        # Valid HubSpot V3 Signature calculation
        secret = TestConfig.HUBSPOT_CLIENT_SECRET
        ts = str(int(time.time() * 1000))
        raw_body = json.dumps(payload)
        url = "http://localhost/webhooks/hubspot"
        msg = f"POST{url}{raw_body}{ts}"
        digest = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
        sig = base64.b64encode(digest).decode()

        resp = self.client.post(
            "/webhooks/hubspot",
            data=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-HubSpot-Signature-v3": sig,
                "X-HubSpot-Request-Timestamp": ts,
            },
        )
        self.assertEqual(resp.status_code, 200)
        mock_handle_company.assert_called_once_with("12345")

    @patch("app.api.find_and_create_contacts")
    @patch("app.api.enrich_company_by_domain_and_update_hs")
    def test_demo_openai_route(self, mock_enrich_company, mock_find_contacts):
        mock_enrich_company.return_value = {"status": "enriched", "company_id": "hs_123"}
        mock_find_contacts.return_value = {"status": "done", "created": 5}

        resp = self.client.post(
            "/api/demo/openai",
            headers={"X-API-KEY": TestConfig.WEBHOOK_SECRET},
            json={},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "demo_complete")
        mock_enrich_company.assert_called_once_with("openai.com")
        mock_find_contacts.assert_called_once()


if __name__ == "__main__":
    unittest.main()
