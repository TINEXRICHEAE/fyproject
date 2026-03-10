## Fair Cashier – PIN‑Secured Wallet Payment Service

Fair Cashier is a standalone Django 4.2 payment service that provides a **PIN‑secured wallet**, **multi‑seller checkout**, and **cashout flows** for a multivendor shopping platform.  
The shopping app talks to Fair Cashier over HTTP APIs and embeds its buyer payment page and seller dashboard inside iframes.

Fair Cashier also integrates with a separate **Strapi‑based ZKP (zero‑knowledge proof) service** to support:

- Buyer balance proofs (confirming a buyer has enough wallet funds without exposing their exact balance).
- Seller KYC proofs (confirming a seller is KYC‑verified on the shopping app without sharing raw KYC data).

---

## Key Features

- **PIN‑secured wallets**
  - Buyers and sellers register with email + 4‑digit PIN.
  - Wallet balances stored per user, with reserved (escrow‑like) balance support.
  - Strong hashing configuration (Argon2 first) for sensitive PIN data.

- **Embedded checkout (iframe)**
  - Shopping app calls Fair Cashier’s API to create a `PaymentRequest`.
  - Receives a `payment_url` that is loaded inside an iframe on the shopping checkout.
  - Two‑step flow: confirmation page → PIN‑protected payment page.

- **Multi‑seller payments**
  - A single buyer payment can be split across multiple sellers.
  - Per‑item actions: **Pay Now** vs **Deposit (reserve)**.
  - Supports mobile money top‑ups when wallet balance is insufficient.

- **Seller dashboard iframe**
  - Shopping platform can embed a seller dashboard that:
    - Shows wallet balance, total sales, and recent transactions.
    - Lets sellers request cashouts and view pending/approved payouts.
    - Exposes identity verification state via ZKP integration.

- **Disputes and refunds**
  - Shopping app can file disputes against individual order items.
  - Admin UI for resolving disputes with/without refund.
  - Webhooks to sync dispute and refund status back to the shopping app.

- **Admin & superadmin consoles**
  - Email/password‑based admin login.
  - Platform registration and management (API keys, domains, webhooks).
  - Cashout approval, transaction monitoring, CSV exports for payouts.

- **ZKP integrations**
  - Buyer liquidity proofs: “buyer has at least X” without leaking exact balance.
  - Seller KYC proofs: Fair Cashier stores commitments and verification flags, not raw KYC payloads.

For a detailed URL and template map, see `FAIRCASHIER_ROUTES_AND_TEMPLATES.md`.

---

## Project Structure (high level)

- `faircashier/`
  - `manage.py` – Django management entry point.
  - `faircashier/` – project configuration (settings, URLs, WSGI/ASGI).
  - `cashingapp/` – main application:
    - Models, views, URLs, APIs, ZKP clients, payment processor, mobile money helpers.
    - Admin and superadmin dashboard views.
    - Buyer/seller PIN flows and wallet logic.
  - `templates/` – HTML templates for buyers, sellers, admins, and embedded flows.
  - `logs/` – log files written by the configured logging setup.
- `FAIRCASHIER_ROUTES_AND_TEMPLATES.md` – in‑depth documentation of routes and templates.
- `requirements.txt` – Python dependencies.

---

## Getting Started (Local Development)

### 1. Prerequisites

- Python 3.11+ (project currently uses Django 4.2).
- PostgreSQL (recommended; `DATABASE_URL` is expected).
- `virtualenv` / `venv` for Python environments.

### 2. Clone and create virtual environment

```bash
git clone <this-repo-url>
cd fyproject

python -m venv cashierenv
source cashierenv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r faircashier/requirements.txt
```

### 4. Environment variables

Create a `.env` file in `faircashier/` (next to `faircashier/settings.py`) or configure these variables in your environment:

- **Core Django**
  - `DJANGO_SECRET_KEY` – secret key for Django.
  - `DATABASE_URL` – database connection string, e.g.  
    `postgres://USER:PASSWORD@localhost:5432/faircashier`

- **Shopping app integration**
  - `SHOPPING_APP_URL` – base URL of the shopping app (default: `http://localhost:8000`).
  - `SHOPPING_APP_INTERNAL_SECRET` – shared secret for internal calls.

- **ZKP / Strapi integration**
  - `ZKP_STRAPI_URL` – base URL for the ZKP Strapi instance (default: `http://localhost:1337`).
  - `ZKP_STRAPI_API_TOKEN` – API token used to talk to the ZKP service.

You may also need other integration‑specific env vars depending on your mobile money or payout gateways (see `cashingapp/payment_processor.py` and related helpers if present).

### 5. Apply migrations

From the `faircashier/` directory (where `manage.py` lives):

```bash
cd faircashier
python manage.py migrate
```

### 6. Create a superuser

```bash
python manage.py createsuperuser
```

### 7. Run the development server

```bash
python manage.py runserver 0.0.0.0:8001
```

By default the project is configured with:

- `DEBUG = True`
- CORS/CSRF and `X_FRAME_OPTIONS` tuned for local iframe embedding from:
  - `http://localhost:8000` / `http://127.0.0.1:8000`
  - `http://localhost:1337` / `http://127.0.0.1:1337`

Adjust these for production.

---

## Core Flows (High Level)

This is a very condensed view; consult `FAIRCASHIER_ROUTES_AND_TEMPLATES.md` for specifics.

- **Buyer setup and login**
  - `/pin-setup/` – create buyer or seller account + wallet + PIN.
  - `/pin-login/` – log in with email + PIN.

- **Wallet and dashboards**
  - `/wallet-pin/` – PIN‑gated wallet view (balance + recent transactions).
  - `/buyer-dashboard/` – buyer dashboard (PIN‑verified).
  - `/payment/seller-dashboard/` – iframe‑based seller dashboard.

- **Deposits and cashouts**
  - `/deposit-pin/` – deposit via mobile money into wallet.
  - `/cashout-pin/` – cashout from wallet.
  - `/seller-request-cashout/` – richer seller cashout request UI.

- **Payment creation and processing**
  - `POST /api/payment-request/create/` – shopping app creates a payment request and gets a `payment_url`.
  - `GET /payment/<uuid:request_id>/` – confirmation page + PIN‑based payment page (iframe‑friendly).
  - `POST /payment/<uuid:request_id>/process-items/` – per‑item payment/deposit processing.

- **APIs for the shopping app**
  - `/api/check-buyer-status/` – buyer + PIN status (choose between PIN setup vs PIN login).
  - `/api/verify-pin/` – verify email + PIN and retrieve wallet info.
  - `/api/wallet-info/` – wallet + basic user info + last transactions.
  - `/api/check-sellers/` – check seller registration and wallet/PIN status.

- **ZKP endpoints**
  - Seller KYC verification: `/seller/zkp-verify/` and internal status endpoints.
  - Buyer balance proofs: `/internal/order-created/`, `/internal/balance-proof/refresh/`, `/internal/balance-proof/`.

- **Disputes**
  - `/api/dispute/create-from-shopping/` – receive disputes from the shopping app.
  - `/dispute/<int:dispute_id>/resolve-with-sync/` – apply resolution and sync back.

---

## Admin & Operations

- **Admin login & registration**
  - `/login/` – admin/superadmin email/password login.
  - `/register/` – register a new platform admin.

- **Dashboards**
  - `/admin-dashboard/` – per‑platform admin dashboard.
  - `/superadmin-dashboard/` – global system overview.

- **Platform and user management**
  - `/register-platform/` – register new shopping platforms.
  - `/platform/<int:platform_id>/` – platform details and activity.
  - `/users/`, `/user/<int:user_id>/` – global user management (superadmin).

- **Transactions and cashouts**
  - `/transactions/` – transaction list and filters.
  - `/admin-cashout-requests/` – review cashout requests.
  - `/admin-cashout-export/` – CSV exports for payouts.
  - `/admin-cashout-bulk-approve/`, `/admin-cashout-disburse/` – bulk and final disbursement flows.

---

## Logging

The project is configured with multiple rotating log files under `faircashier/logs`:

- `faircashier.log` – general application logging.
- `transactions.log` – transaction‑level events.
- `duplicates.log` – duplicate detection events.
- `errors.log` – error‑level logs.

You can adjust log levels, handlers, and formats in `faircashier/settings.py`.

---

## Deployment Notes

- Set `DEBUG = False`, `ALLOWED_HOSTS`, and secure cookie settings (`CSRF_COOKIE_SECURE`, `SESSION_COOKIE_SECURE`) appropriately.
- Use a production‑grade cache (e.g. Redis or Memcached) instead of the default in‑memory cache.
- Terminate TLS at a reverse proxy (e.g. Nginx) and proxy through to the Django app (Gunicorn/Uvicorn).
- Lock down CORS, CSRF, and `CSP_FRAME_ANCESTORS` to your real shopping app and ZKP domains.

---

## Further Documentation

- **Routes and templates**: `FAIRCASHIER_ROUTES_AND_TEMPLATES.md`
- **Settings**: `faircashier/faircashier/settings.py`
- **Main app code**: `faircashier/cashingapp/`

If you are integrating a shopping app or ZKP service, start by reading the public API sections in `FAIRCASHIER_ROUTES_AND_TEMPLATES.md`, then configure your environment variables and test the flows end‑to‑end locally.

