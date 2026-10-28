# Forager × HubSpot Enrichment Automation

A production-ready Python/Flask service that enriches HubSpot companies and contacts
using the [Forager](https://forager.ai) API, with webhook-driven automation.

---

## Architecture

```
HubSpot Webhook (company.creation / contact.creation)
        │
        ▼
  Flask App (Railway)
        │
   ┌────┴────────────────────────────┐
   │                                 │
   ▼                                 ▼
Forager API                    HubSpot CRM API
(enrich data)             (read + write records)
```

---

## Features

| # | Feature | Endpoint / Trigger |
|---|---------|-------------------|
| 1 | **Enrich company** | `POST /api/enrich/company` |
| 2 | **Enrich contact** | `POST /api/enrich/contact` |
| 3 | **Find contacts at company** (with job-title filter) | `POST /api/find-contacts` |
| 4 | **Company webhook automation** | `POST /webhooks/hubspot` → `company.creation` |
| 5 | **Contact webhook automation** | `POST /webhooks/hubspot` → `contact.creation` |
| 6 | **OpenAI demo run** | `POST /api/demo/openai` |

---

## Quick Start (Local)

### 1. Clone & create virtual environment

```bash
git clone <your-repo-url>
cd forager-hubspot
python -m venv venv

# Activate (Linux/Mac)
source venv/bin/activate

# Activate (Windows PowerShell)
.\venv\bin\Activate.ps1
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up environment variables

```bash
cp .env.example .env
# Edit .env with your real API keys
```

### 4. Run locally

```bash
python run.py
```

Server starts at `http://localhost:5000`.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FORAGER_API_KEY` | ✅ | From Forager dashboard → Settings → API |
| `FORAGER_ACCOUNT_ID` | ✅ | From Forager dashboard → Settings → API |
| `HUBSPOT_ACCESS_TOKEN` | ✅ | HubSpot Private App token |
| `HUBSPOT_PORTAL_ID` | ✅ | Your HubSpot portal/account ID |
| `SECRET_KEY` | ✅ | Flask secret key (any random string) |
| `DEFAULT_JOB_TITLES` | optional | Comma-separated titles for contact discovery |
| `MAX_CONTACTS_PER_COMPANY` | optional | Cap on discovered contacts (default: 5) |
| `WEBHOOK_SECRET` | optional | Shared secret for `X-API-KEY` header auth |
| `DEBUG` | optional | Set `true` for development |

---

## API Reference

All `/api/*` routes accept `Content-Type: application/json` and optionally
`X-API-KEY: <your WEBHOOK_SECRET>` if you have that env var set.

### Health check

```
GET /webhooks/health
GET /api/status
```

### Enrich a company

```bash
# By HubSpot company ID
curl -X POST https://<your-url>/api/enrich/company \
  -H "Content-Type: application/json" \
  -H "X-API-KEY: <secret>" \
  -d '{"company_id": "12345"}'

# By domain
curl -X POST https://<your-url>/api/enrich/company \
  -H "Content-Type: application/json" \
  -d '{"domain": "openai.com"}'
```

### Enrich a contact

```bash
curl -X POST https://<your-url>/api/enrich/contact \
  -H "Content-Type: application/json" \
  -d '{"contact_id": "67890"}'
```

### Find & create contacts at a company

```bash
curl -X POST https://<your-url>/api/find-contacts \
  -H "Content-Type: application/json" \
  -d '{
    "company_domain": "openai.com",
    "company_id": "12345",
    "job_titles": ["CTO", "VP Engineering", "Software Engineer"],
    "max_results": 5
  }'
```

### Run the OpenAI demo

```bash
curl -X POST https://<your-url>/api/demo/openai \
  -H "Content-Type: application/json" \
  -d '{"company_id": "<openai_hs_company_id>"}'
```

### Simulate a company webhook

```bash
curl -X POST https://<your-url>/api/trigger/company-webhook \
  -H "Content-Type: application/json" \
  -d '{"company_id": "12345"}'
```

---

## HubSpot Setup

### 1. Create a Private App

1. Go to **Settings → Integrations → Private Apps**
2. Click **Create a private app**
3. Required scopes:
   - `crm.objects.contacts.read`
   - `crm.objects.contacts.write`
   - `crm.objects.companies.read`
   - `crm.objects.companies.write`
   - `crm.associations.read`
   - `crm.associations.write`
4. Copy the **Access Token** → set as `HUBSPOT_ACCESS_TOKEN`

### 2. Create Custom Contact Properties

In HubSpot → Settings → Properties → Contact properties, create:

| Property Name | Internal Name | Type |
|--------------|---------------|------|
| Forager Company Domain | `forager_company_domain` | Single-line text |
| Forager Company LinkedIn | `forager_company_linkedin` | Single-line text |
| Forager Description | `forager_description` | Multi-line text |
| Forager About / Headline | `forager_about` | Single-line text |
| Forager Work Emails | `forager_work_emails` | Single-line text |
| Forager Personal Emails | `forager_personal_emails` | Single-line text |
| Forager All Phones | `forager_all_phones` | Single-line text |
| Forager Enriched | `forager_enriched` | Boolean |

### 3. Create Custom Company Properties

In HubSpot → Settings → Properties → Company properties, create:

| Property Name | Internal Name | Type |
|--------------|---------------|------|
| Forager Headcount | `forager_headcount` | Number |
| Forager Revenue | `forager_revenue` | Number |
| Forager Specialties | `forager_specialties` | Multi-line text |
| Forager Technologies | `forager_technologies` | Multi-line text |
| Forager Enriched | `forager_enriched` | Boolean |

### 4. Set Up Webhooks

1. In your Private App → **Webhooks** tab
2. Set **Target URL** to: `https://<your-railway-url>/webhooks/hubspot`
3. Add subscriptions:
   - `company.creation`
   - `contact.creation`

---

## Deploy to Railway

### Option A: GitHub (Recommended)

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → **New Project → Deploy from GitHub**
3. Select your repo
4. In **Variables** tab, add all env vars from `.env.example`
5. Railway auto-detects the `Procfile` and deploys

### Option B: Railway CLI

```bash
npm install -g @railway/cli
railway login
railway init
railway up

# Set environment variables
railway variables set FORAGER_API_KEY=xxx
railway variables set FORAGER_ACCOUNT_ID=xxx
railway variables set HUBSPOT_ACCESS_TOKEN=xxx
railway variables set HUBSPOT_PORTAL_ID=xxx
railway variables set SECRET_KEY=xxx
```

Your live URL will be: `https://<project-name>.up.railway.app`

---

## Project Structure

```
forager-hubspot/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── config.py            # All config from env vars
│   ├── forager_client.py    # Forager API wrapper
│   ├── hubspot_client.py    # HubSpot CRM wrapper
│   ├── enrichment_service.py # Business logic / orchestration
│   ├── webhooks.py          # /webhooks/* routes
│   └── api.py               # /api/* routes
├── run.py                   # Entry point
├── requirements.txt
├── Procfile                 # Railway/Heroku deploy
├── .env.example             # Safe env template
├── .gitignore
└── README.md
```

---

## Interesting Extras from Forager (for discussion)

Forager surfaces several fields beyond the required spec that may be worth adding:

| Field | Value |
|-------|-------|
| `description` | LinkedIn "About" section (person bio) |
| `about` / `headline` | Professional headline (e.g., "ML Engineer at OpenAI") |
| `skills` | List of skills from LinkedIn profile |
| `education` | Education history |
| `experience` | Full employment history (all past roles) |
| `technologies` | Tech stack used by the company |
| `specialties` | Company specialties/focus areas |

These are flagged in the `_raw` response and stored in `forager_description` / `forager_about`
custom properties. Recommend discussing which to surface as dedicated HubSpot fields.
