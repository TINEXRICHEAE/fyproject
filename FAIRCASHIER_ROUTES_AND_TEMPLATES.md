## Fair Cashier – Routes and Templates Guide

### 1. Project overview

**Fair Cashier** is a standalone payment app designed to be embedded into a multivendor e‑commerce shopping app via an iframe. The shopping app creates payment requests through Fair Cashier’s API and then loads a Fair Cashier payment page in an iframe on its checkout. Buyers pay using a PIN‑secured wallet, and sellers later withdraw funds via mobile money or bank transfer.

Both the **shopping app** and the **payment app** integrate with a separate **Strapi ZKP (zero‑knowledge proof) service** to support privacy‑preserving verification flows:

- **1. Balance check (buyer liquidity proof)**  
  - Goal: Let the shopping app confirm that a buyer has enough funds in their Fair Cashier wallet **without revealing the actual wallet balance** to the shopping app.  
  - Roles:  
    - **Payment app**: prover (constructs balance proof from wallet state).  
    - **Shopping app**: verifier (checks the proof against what it needs to know for the order).  
  - Effect: Shopping app can safely decide to show “wallet payment available / allowed” without ever seeing the buyer’s raw balance.

- **2. Seller verification (KYC proof)**  
  - Goal: Let Fair Cashier confirm that a seller has passed KYC on the shopping app **without the shopping app sharing raw KYC documents or PII** with the payment app.  
  - Roles:  
    - **Shopping app**: prover (commits to KYC data and generates a ZKP showing the seller is KYC‑approved).  
    - **Payment app**: verifier (checks the proof and stores only cryptographic commitments/verification status).  
  - Effect: Fair Cashier can enforce “only KYC‑verified sellers may receive payouts” without holding or touching the original sensitive KYC payload.

High‑level flow:

1. Shopping app creates a payment request via Fair Cashier’s API.
2. Buyer is redirected (or an iframe is loaded) to Fair Cashier’s PIN‑based payment UI.
3. Fair Cashier may use the ZKP services to:
   - Prove buyer has enough funds (balance proof), and/or
   - Verify that the seller is KYC‑verified.
4. On successful payment, Fair Cashier transfers funds between internal wallets and notifies the shopping app via webhooks.
5. Sellers see their balance and request cashouts via a seller dashboard, also embeddable in the shopping app via iframe.

---

### 2. URL configuration overview

#### 2.1 Project root URLs – `faircashier/faircashier/urls.py`

- **`/admin/`**  
  - Django admin site (for developers/maintenance, separate from the custom admin UI).
- **`/`**  
  - Includes `cashingapp.urls` → all application routes live under the root path.

---

### 3. Core buyer/seller flows (PIN‑based)

Defined in `cashingapp/urls.py` and implemented mainly in `views.py` and `buyer_seller_views.py`.

#### 3.1 Entry point and landing

- **`GET /` → `views.home` → `home.html`**  
  - If a Django user session exists, redirects based on `user.role`:
    - `buyer` → `buyer_dashboard`
    - `seller` → `seller_dashboard` (seller PIN dashboard)
    - `admin` → `admin_dashboard`
    - `superadmin` → `superadmin_dashboard`
  - Otherwise renders a **dark Tailwind “Fair Cashier – Secure Payment Gateway” landing** (`home.html`) with:
    - Explanation cards for Buyers/Sellers/Platforms.
    - CTA to **set up PIN** (`/pin-setup/`).
    - CTA to **platform Admin Login** (`/login/`).

Templates involved:
- `home.html` – marketing/landing page for buyers, sellers, and platforms.  
  - Extends `buyer_seller_base.html`.
  - Shows core value props and CTAs.
- `buyer_seller_base.html` – minimal dark layout used by PIN‑centric flows (no top nav).

#### 3.2 PIN setup and login (buyers & sellers)

- **`GET/POST /pin-setup/` → `buyer_seller_views.pin_setup` → `pin_setup.html`**  
  - **GET**: Shows PIN setup form; accepts:
    - `email` (optional, prefilled and read‑only if provided – typically from payment link or seller iframe).
    - `return` (URL to go back to; if it is a payment URL, the view wraps it with a confirmation token).
    - Optional `role` (`buyer` or `seller`), prefilled for seller dashboard onboarding.
  - **POST**:
    - Validates email and ensures no existing user.
    - Creates a `Users` row with `role` (buyer/seller) and `Wallet`.
    - Sets a 4‑digit PIN via `PINAuthenticator`.
    - Returns JSON `{ success, redirect_url }`, where `redirect_url` is built by `_build_redirect_url` and, for payment flows, includes a **signed confirmation token**.
  - Used both by:
    - Buyers starting from the payment page flow.
    - Sellers onboarding from the seller dashboard iframe.

  Template:
  - `pin_setup.html` – PIN creation UI:
    - Email + optional phone.
    - Role selector (buyer/seller) unless prefilled.
    - Two PIN rows (PIN + Confirm PIN) with 4 separate digit boxes.
    - Inline JS:
      - Restricts to digits, manages focus.
      - Submits via `fetch` and redirects using `data.redirect_url`.
      - Notifies parent window (via `postMessage`) when inside an iframe + payment flow.

- **`GET/POST /pin-login/` → `buyer_seller_views.pin_login` → `pin_login.html`**  
  - **GET**: Shows PIN login form; accepts `email` + `return`.
  - **POST**:
    - Validates email + PIN.
    - Authenticates using `PINAuthenticator.verify_pin`.
    - Returns JSON `{ success, redirect_url }` built via `_build_redirect_url`, with confirmation token if redirecting back to a payment.
  - Used when:
    - Existing buyers return to complete a payment.
    - Existing sellers log into the seller dashboard iframe.

  Template:
  - `pin_login.html` – PIN login UI with:
    - Email (prefilled and optional read‑only).
    - 4‑digit PIN inputs.
    - JS that auto‑submits once 4 digits are entered, uses `fetch`, and redirects.
    - `postMessage` integration to notify parent when running inside a checkout iframe.

#### 3.3 Payment creation and confirmation flow

- **`POST /api/payment-request/create/` → `views.create_payment_request`**  
  - JSON API used by the shopping app to initiate a payment.
  - Request:
    - `api_key` (platform API key, used to authenticate platform).
    - `buyer_email`.
    - `items`: list of `{ seller_email, amount, currency?, description?, shopping_order_item_id? or shopping_order_item_ids? }`.
    - `metadata` (arbitrary JSON, often contains order IDs).
  - Creates:
    - `PaymentRequest` (with `request_id` UUID, `status='initiated'`).
    - `PaymentRequestItem` rows for each seller item (linked 1:1 to shopping side order item by ID).
  - Response:
    - `request_id` (UUID).
    - `payment_url` – `/payment/<request_id>/`.
    - `total_amount`.
  - This URL is what the shopping app loads in an **iframe** on its checkout page.

- **`GET /payment/<uuid:request_id>/` → `views.payment_page`**  
  - Decorated with `@xframe_options_exempt` so it can be embedded as an iframe.
  - Flow:
    1. Looks up `PaymentRequest`.
    2. Checks for `GET ?confirmed=<token>`; if missing or invalid, shows a **confirmation page** instead of the PIN page:
       - Renders `payment_confirm.html` (confirmation/summary step).
       - Lists sellers and items, shows total, and prompts the buyer to confirm.
    3. If token is valid:
       - Looks up buyer user + wallet by `buyer_email`.  
       - If buyer user does not exist, redirects to `/pin-setup/?email=...&return=/payment/<id>/` to create wallet & PIN.
       - Otherwise, renders the multi‑item, PIN‑protected payment UI `payment_page_pin.html`.

Templates:

- `payment_confirm.html` (via `payment_page` when not yet confirmed)  
  - Extends `base.html` (nav + standard layout).  
  - Shows:
    - Platform name and “Secure Payment powered by Fair Cashier”.
    - All `PaymentRequestItem`s (seller email, description, amount).
    - Total amount.
    - Wallet balance (if available in context).
    - “Confirm payment” button which is expected to redirect into the PIN flow with a confirmation token.

- `payment_page_pin.html` (via `payment_page` after confirmation)  
  - Extends `buyer_seller_base.html`.  
  - Core **multi‑seller payment UI** used inside the iframe:
    - Shows wallet balance, free and reserved portions.
    - For each item, lets buyer pick **Pay Now** vs **Deposit (reserve)**:
      - “Pay” → immediate transfer to seller.
      - “Deposit” → keeps funds reserved in wallet (escrow‑like), not yet paid out.
    - Calculates when a **mobile money top‑up** is required and collects a phone number.
    - Collects a 4‑digit PIN.
    - Submits to `/payment/<request_id>/process-items/` (see below) via `fetch`.
    - On success:
      - Updates wallet balances and visual badges per item.
      - Posts a summary message back to the parent iframe window so the shopping app can update order UI.

#### 3.4 Payment processing endpoints

- **`POST /payment/<uuid:request_id>/process-pin/` → `buyer_seller_views.process_payment_with_pin`**  
  - Stateless PIN‑check + full payment:
    - Validates email and PIN.
    - Checks buyer has enough wallet balance for the entire `PaymentRequest.total_amount`.
    - Transfers funds per item from buyer wallet to each seller wallet (creates `Transaction` rows).
    - Updates `PaymentRequest.status = 'paid'`.
  - Returns JSON describing success or relevant errors (insufficient balance, wrong PIN, etc.).

- **`POST /payment/<uuid:request_id>/deposit-and-pay/` → `buyer_seller_views.deposit_and_pay`**  
  - Flow for **“pay even if my balance is too low”**:
    1. Validates email, PIN and phone number.
    2. If wallet balance is less than `total_amount`, computes **shortfall**.
    3. Calls mobile money deposit helper to top up only the shortfall, simulating the webhook completion internally.
    4. Once wallet is funded, transfers all item amounts to sellers as in `process_payment_with_pin`.

- **`POST /payment/<uuid:request_id>/process-items/` → `buyer_seller_views.process_payment_items`**  
  - Backing endpoint for `payment_page_pin.html` per‑item UI:
    - Inputs:
      - `email`, `pin`.
      - `item_actions` JSON: list of `{ item_id, action: "pay"|"deposit" }`.
      - Optional `phone_number` (required if a top‑up is needed).
    - Steps:
      1. Reconstructs `PaymentRequest` and ensures email matches buyer.
      2. Verifies buyer PIN.
      3. Computes a unified **shortfall** = total of all item actions – wallet free balance.
         - If shortfall > 0, runs one mobile money deposit and auto‑completes it.
      4. For each item:
         - `pay` → immediate buyer→seller transfer; marks item as paid.
         - `deposit` → increases `wallet.reserved_balance` and marks item as deposited (escrow).
      5. Sets `PaymentRequest.status` to `paid`, `deposited`, `partial`, or `failed`.
      6. Notifies the shopping app via `_notify_shopping_app` webhook (per‑item statuses).
    - Returns JSON with detailed item results and updated wallet balances.

  - **`_notify_shopping_app`** webhook helper (inside `buyer_seller_views.py`):
    - Configured via `SHOPPING_APP_WEBHOOK_URL` env var (defaults to `http://localhost:8000/api/webhook/payment-status/`).
    - Sends:
      - `request_id`.
      - `item_updates`: `[ { shopping_order_item_id, status, amount }, ... ]`.
      - `overall_status`: `paid | deposited | partial | failed`.

- **`POST /payment/<uuid:request_id>/complete-deposit/shopping-item/<int:shopping_order_item_id>/` → `buyer_seller_views.complete_deposit_by_order_item`**  
  - **Shopping‑app‑facing API** to turn a deposited item into a completed payment:
    - Inputs: email + PIN (either form‑data or JSON).
    - Uses `shopping_order_item_id` to locate a `PaymentRequestItem` that is in a deposited state.
    - Moves reserved funds from buyer wallet to seller wallet; updates balances and item status.
    - Notifies the shopping app via webhook (marking this item as `paid`).

- **`POST /payment/<uuid:request_id>/cancel-deposit/shopping-item/<int:shopping_order_item_id>/` → `buyer_seller_views.cancel_deposit_by_order_item`**  
  - **Shopping‑app‑facing API** to cancel a reserved deposit:
    - Inputs: email + PIN.
    - Frees up `reserved_balance` for that specific item; updates item to a pending state.
    - Calls `_notify_shopping_app` with `status='partial'` for that item.

#### 3.5 Wallet/dashboard and cash operations (PIN‑protected)

- **`GET/POST /wallet-pin/` → `buyer_seller_views.wallet_view_pin` → `wallet_pin.html`**  
  - **GET**: Renders `wallet_pin.html` with buyer/seller email.
  - **POST**:
    - Validates email and PIN.
    - Returns JSON with wallet balance/currency and recent transactions.
  - Template `wallet_pin.html`:
    - PIN entry UI.
    - Shows wallet balance and currency.
    - Shows “Deposit” and “Cashout” buttons linking to `deposit_pin` and `cashout_pin`.
    - Shows recent transactions once PIN is verified.

- **`GET/POST /buyer-dashboard/` → `buyer_seller_views.buyer_dashboard` → `buyer_dashboard_pin.html`**  
  - **GET**: Renders `buyer_dashboard_pin.html` for a buyer.
  - **POST**:
    - Verifies buyer PIN.
    - Returns JSON summarizing:
      - Wallet balance.
      - Recent transactions.
      - Recent payment requests by that buyer.
  - Template likely provides a PIN gate and displays high‑level buyer wallet/payment history.

- **`GET/POST /deposit-pin/` → `buyer_seller_views.deposit_pin` → `deposit_pin.html`**  
  - **GET**: Shows deposit form:
    - Email.
    - Mobile money phone number.
    - Amount and platform (from `Platform` list).
  - **POST**:
    - Verifies user (buyer or seller) via PIN.
    - Enforces minimum deposit amount.
    - Uses `process_deposit(...)` to initiate mobile money deposit and manage duplicate protection.
    - Returns JSON with transaction info or error.
  - Template `deposit_pin.html` is a dark, PIN‑gated deposit form.

- **`GET/POST /cashout-pin/` → `buyer_seller_views.cashout_pin` → `cashout_pin.html`**  
  - Symmetric to `deposit_pin` but for withdrawing funds:
    - PIN‑protected.
    - Validates minimum cashout and sufficient funds.
    - Uses `process_cashout(...)` helper for mobile money; also deduplicates repeated submit.
  - Template `cashout_pin.html` is a PIN‑protected cashout form.

- **`GET/POST /seller-request-cashout/` → `buyer_seller_views.seller_request_cashout` → `seller_request_cashout.html`**  
  - GET params: `email`, `platform_id`.
  - **GET**:
    - Shows a larger **cashout request form** where seller picks:
      - Payment method (MTN/Airtel mobile money or Bank).
      - Numbers/account details and recipient name.
      - Amount and optional notes.
    - Also shows recent pending/approved cashout requests + wallet balance.
  - **POST**:
    - Verifies seller via PIN.
    - Validates method‑specific fields.
    - Creates a `CashoutRequest` row marked `pending` and logs activity.
    - Returns JSON describing the new pending request.

Templates involved:
- `deposit_pin.html` – deposit UI for buyers and sellers.
- `cashout_pin.html` – basic PIN‑protected cashout.
- `seller_request_cashout.html` – richer cashout request flow (with method‑specific fields and list of recent requests).

---

### 4. Public APIs for the shopping app

Defined in `cashingapp/urls.py` under “API ENDPOINTS” and in `api_views.py`.

#### 4.1 Buyer and PIN status

- **`GET/POST /api/check-buyer-status/` → `api_views.check_buyer_status`**  
  - Given an email, tells the shopping app:
    - Whether a buyer exists.
    - Whether they have:
      - A wallet.
      - A PIN.
      - Any PIN lock status.
    - The recommended **next action**: `pin_login` vs `pin_setup`.

- **`POST /api/verify-pin/` → `api_views.verify_pin_api`**  
  - Validates email + PIN (buyer or seller).
  - Returns:
    - `valid` flag, role, and wallet balance (when valid), or
    - Error + remaining attempts (when invalid).

- **`POST /api/wallet-info/` → `api_views.get_wallet_info`**  
  - Requires email + PIN.
  - Returns:
    - Wallet balance and currency.
    - Basic user metadata (role, phone).
    - Last 5 transactions.

- **`POST /api/update-pin/` → `api_views.update_pin`**  
  - For buyers/sellers to change their PIN:
    - Validates old PIN.
    - Sets new PIN via `PINAuthenticator.set_pin`.

#### 4.2 Seller registration checks (non‑ZKP level)

- **`POST /api/check-sellers/` → `api_views.check_sellers`**  
  - Shopping app sends `api_key` and list of `seller_emails`.
  - Fair Cashier responds with:
    - `registered` flag.
    - Whether each seller has a wallet and PIN.
  - Useful for the shopping app to pre‑screen which vendors can accept wallet payments.

#### 4.3 Payment webhooks and simulation

- **`POST /api/webhook/complete/<uuid:transaction_id>/` → `views.simulate_webhook_completion`**  
  - Developer/test hook to simulate external mobile money webhook completion:
    - Marks internal transactions as completed.
    - Used during local testing of deposit flows.

---

### 5. Seller dashboard iframe (external platform access)

Defined in `cashingapp/seller_proxy_urls.py` and implemented in `seller_proxy_views.py`.

- **`GET/POST /payment/seller-dashboard/` → `seller_proxy_views.seller_dashboard_iframe`**  
  - Single endpoint for:
    - **Initial access from the shopping app**.
    - **PIN setup or login** for sellers.
    - **Serving the authenticated seller dashboard** once verified.
  - Three main phases:

  1. **Initial access (GET with platform token)**  
     - Shopping app calls `/payment/seller-dashboard/?email=...&platform_key=...&token=...` inside an iframe.  
     - `verify_seller_access_token` checks a shared HMAC‑like token built from platform API key, seller email, and timestamp.  
     - If platform + token are valid:
       - If seller exists → renders `pin_login.html` with prefilled email (seller must enter PIN).
       - If seller does not exist → renders `pin_setup.html` with role prefilled as `seller`.

  2. **PIN setup / login (POST)**  
     - Sellers POST email + PIN (and confirm PIN for setup).  
     - New sellers: user + wallet are created, PIN is stored.  
     - Existing sellers: PIN is verified.  
     - On success:
       - Sets a session flag.
       - Generates a **signed `dash_token`** (`django.core.signing`) that encodes `{ email, auth: True }`.
       - Returns JSON `{ success, redirect_url: "/payment/seller-dashboard/?email=...&dash_token=..." }`.  
       - This pattern avoids relying on third‑party cookies inside iframes.

  3. **Authenticated dashboard (GET with `dash_token` or session)**  
     - If called with `dash_token`, `_verify_dash_token` checks the signed token.
     - If valid, `_render_authenticated_dashboard(...)` gathers:
       - Seller wallet, last transactions, and recent `PaymentRequestItem`s.
       - Computes `total_sales`.
       - Embeds a JSON blob `initial_data_json` and a new `ajax_token` for JS.  
     - Renders `seller_dashboard_pin.html` with `pin_verified=True`.

Template:

- `seller_dashboard_pin.html`  
  - Extends `buyer_seller_base.html`.  
  - Sections:
    - Wallet balance + total sales widgets.
    - Quick actions:
      - “Request Cashout” → `/seller-request-cashout/?email=...&platform_id=...`.
      - “Direct Cashout” → `/cashout-pin/?email=...`.
      - “Wallet” → `/wallet-pin/?email=...`.
    - **ZKP identity verification block**:
      - Uses `AJAX_TOKEN` to call `/seller/zkp-verify/` and sub‑routes.
      - Shows states: Not Registered / Registered but Unverified / Verifying / Verified / Failed.
      - When verified, displays truncated commitment hash and Merkle root from ZKP system, plus verification timestamp.
      - “Verify Identity” button triggers POST to `/seller/zkp-verify/`, which talks to the ZKP service (seller‑KYC proof).
    - Recent sales list and most recent items.
  - When initially loaded with `pin_verified=False`, it first shows a PIN prompt and then redirects to the `dash_token` URL on success.

---

### 6. ZKP‑related URL patterns

Defined in `cashingapp/urls_zkp.py` and `cashingapp/urls_balance_proof.py`.

#### 6.1 Seller KYC verification via ZKP – `urls_zkp.py`

- **`GET/POST /seller/zkp-verify/` → `views_zkp.seller_zkp_verify`**  
  - Entry point used by `seller_dashboard_pin.html` JS:
    - **GET**: returns JSON with current ZKP status for the seller:
      - Whether there is an existing commitment / proof.
      - Whether seller is already verified.
      - Relevant hashes (commitment, KYC Merkle root) and timestamps.
    - **POST**: triggers a fresh verification with the Strapi ZKP service:
      - Sends a request to the ZKP app asking it to prove that:
        - This seller has KYC data stored and approved.
      - Receives a ZKP proof and verification result.
      - Updates local KYC/ZKP state (commitment hash, Merkle root, verified flag, timestamps).
    - All requests carry the seller email and `dash_token` in order to authenticate from within the iframe.

- **`GET /internal/seller-zkp-status/<str:seller_email>/` → `views_zkp.internal_seller_zkp_status`**  
  - Internal view (primarily for admins or backend diagnostics) to inspect current seller ZKP status.

- **`GET /api/internal/seller-zkp-status/<str:seller_email>/` → `views_zkp.api_internal_seller_zkp_status`**  
  - JSON API variant of the above, for backend integrations or admin dashboards.

#### 6.2 Buyer balance proof (wallet liquidity ZKP) – `urls_balance_proof.py`

- **`POST /internal/order-created/` → `views_balance_proof.internal_order_created`**  
  - Called by the shopping app whenever it creates an order that expects wallet payment.
  - Registers baseline data with the ZKP app so that:
    - A balance proof can be generated for this order.
    - The ZKP service has a consistent view of requested amount vs. buyer wallet state.

- **`POST /internal/balance-proof/refresh/` → `views_balance_proof.internal_balance_proof_refresh`**  
  - Internal endpoint for refreshing or re‑requesting a buyer’s balance proof from the ZKP app.
  - Use case: order amount changes, items are added/removed, or wallet balance changes and a new proof is needed.

- **`GET /internal/balance-proof/` → `views_balance_proof.internal_balance_proof_fetch`**  
  - Returns stored / latest balance proof data (for a given order or buyer) to internal consumers:
    - Can be called by Fair Cashier itself or by the shopping app (via secure backchannel).
    - Encapsulates the “buyer has at least X UGX” statement without exposing the actual balance.

These URLs embody the **“payment app is prover; shopping app is verifier”** pattern: Fair Cashier cooperates with the ZKP app to produce proofs that the shopping app then verifies before allowing wallet payment as a method.

---

### 7. Dispute and refund integration with the shopping app

Defined in `cashingapp/urls.py` and `cashingapp/dispute_api_views.py`, plus parts of `admin_views.py`.

#### 7.1 Receiving disputes from the shopping app

- **`POST /api/dispute/create-from-shopping/` → `dispute_api_views.create_dispute_from_shopping`**  
  - Shopping app calls this when a buyer files a dispute on an online‑paid item.  
  - Request includes:
    - `api_key` (platform auth), IDs and contacts for buyer + seller, order number.
    - Item description, amount, reason + human‑readable reason, free‑text description.
    - `metadata` including shopping app `order_id` and `order_item_id`.
  - Flow:
    1. Validates `api_key` → finds platform.
    2. Ensures buyer exists (creates placeholder + wallet if needed).
    3. Ensures seller exists (required).
    4. Finds corresponding `PaymentRequestItem` (primarily by `order_id`, fallback by email + amount).
    5. Maps shopping app dispute reason to Fair Cashier’s internal reason taxonomy.
    6. Creates a `Dispute` row tying together buyer, seller, payment item, and disputed amount.
    7. Marks the item as “held” (not withdrawable) until resolution.
    8. Logs activity and returns Fair Cashier dispute ID + status.

#### 7.2 Resolving disputes and syncing back

- **`POST /dispute/<int:dispute_id>/resolve-with-sync/` → `dispute_api_views.resolve_dispute_with_sync`**  
  - Called from admin resolution UI (or to replace/augment existing `admin_views.resolve_dispute`).
  - Does two things atomically:
    1. Applies chosen resolution in the payment app:
       - `await_review` → status set to `under_review`.
       - `resolve_with_refund` → moves funds from seller wallet back to buyer wallet as refund.
       - Reject → releases the held funds back to seller (marks item cleared).
    2. Calls `_sync_dispute_to_shopping_app(...)` to update the shopping app with:
       - Payment dispute ID and original shopping dispute ID.
       - New dispute status.
       - Payment status (`Refunded`, `Not Refunded`, etc.).
       - Optional `refund_amount` and `admin_notes`.

- **Helper**: **`_sync_dispute_to_shopping_app(dispute, payment_status, refund_amount, admin_notes)`**  
  - Builds payload and POSTs to `shopping_app_domain/api/webhook/dispute-status/`.  
  - Resolves `shopping_dispute_id` from admin_notes or logs.

#### 7.3 Admin dispute views and templates

- **`GET /disputes/` → `admin_views.disputes_list` → `admin_disputes.html`**  
  - Lists disputes for the platforms administered by the logged‑in admin (or all for superadmin).
  - Template `admin_disputes.html` shows filterable list and links into resolution screens.

- **`GET/POST /dispute/<int:dispute_id>/resolve/` → `admin_views.resolve_dispute` → `admin_resolve_dispute.html`**  
  - Classic admin UI for marking a dispute as:
    - Under review.
    - Resolved with refund.
    - Resolved without refund.
  - On POST:
    - Executes or logs appropriate refund transaction.
    - Can be combined with `resolve_dispute_with_sync` to propagate the outcome.
  - Template `admin_resolve_dispute.html` shows dispute details and resolution form.

Additional templates:
- `resolve_dispute.html` – may be a buyer/seller facing view of dispute status (depending on how it is wired in the shopping app).

---

### 8. Admin & superadmin console routes

Defined in `cashingapp/urls.py` under “ADMIN/SUPERADMIN ROUTES (PASSWORD‑BASED)” and implemented in `admin_views.py`.

#### 8.1 Authentication

- **`GET/POST /login/` → `admin_views.admin_login` → `admin_login.html`**  
  - Email/password based login for `admin` and `superadmin` users.
  - On success returns JSON with redirect URL to the correct dashboard.

- **`GET/POST /register/` → `admin_views.admin_register` → `admin_register.html`**  
  - Creates a new platform admin account, its wallet, logs activity, and auto‑logs in.

- **`GET /logout/` → `admin_views.admin_logout`**  
  - Logs out current user and redirects to admin login.

Templates:
- `admin_login.html` – admin login screen.
- `admin_register.html` – admin registration form.

#### 8.2 Dashboards

- **`GET /admin-dashboard/` → `admin_views.admin_dashboard` → `admin_dashboard.html`**  
  - For `admin` role only:
    - Lists platforms owned by the admin.
    - Shows metrics:
      - Total transactions + completed volume.
      - Recent payment requests.
      - Count of pending disputes plus latest disputes.

- **`GET /superadmin-dashboard/` → `admin_views.superadmin_dashboard` → `superadmin_dashboard.html`**  
  - For `superadmin` only:
    - Global summaries of:
      - Users (total buyers, sellers, admins).
      - Platforms count.
      - All transactions and total completed volume.
      - Pending disputes across the whole system.
      - Latest users, platforms, and transactions.

Templates:
- `admin_dashboard.html` – per‑platform admin view.
- `superadmin_dashboard.html` – global overview for system operator.

#### 8.3 Platform management

- **`GET/POST /register-platform/` → `admin_views.register_platform` → `admin_register_platform.html`**  
  - For admins to register new e‑commerce platforms:
    - Name, domain, `return_url`, webhook `callback_url`, and mobile money credentials.
  - On POST, returns JSON with `platform_id` and API key for server‑side integration.

- **`GET /platform/<int:platform_id>/` → `admin_views.platform_details` → `admin_platform_details.html`**  
  - Shows:
    - Platform metadata.
    - Recent transactions and payment requests associated with the platform.

Templates:
- `admin_register_platform.html` – form for platform registration.
- `admin_platform_details.html` – detail screen with activity tables.

#### 8.4 User management (superadmin)

- **`GET /users/` → `admin_views.users_list` → `superadmin_users.html`**  
  - Lists all users in the system with pagination/summary.

- **`GET /user/<int:user_id>/` → `admin_views.user_details` → `superadmin_user_details.html`**  
  - Shows:
    - Selected user profile.
    - Linked wallet and recent transactions.

Templates:
- `superadmin_users.html` – global users table.
- `superadmin_user_details.html` – user details, wallet, activity.

#### 8.5 Transactions & cashouts

- **`GET /transactions/` → `admin_views.transactions_list` → `admin_transactions.html`**  
  - Admin: transactions for own platforms.  
  - Superadmin: all transactions (capped in template).

- **`GET /admin-cashout-requests/` → `admin_views.admin_cashout_requests` → `admin_cashout_requests.html`**  
  - Lists cashout requests with filtering by status and payment method.
  - Includes summary stats (pending/approved/disbursed counts and amounts).

- **`GET/POST /admin-cashout-review/<int:cashout_id>/` → `admin_views.admin_review_cashout`**  
  - JSON view for retrieving details and POST endpoint for approving/rejecting a single cashout.

- **`POST /admin-cashout-bulk-approve/` → `admin_views.admin_bulk_approve_cashouts`**  
  - Bulk approval of multiple pending cashout requests.

- **`GET /admin-cashout-export/` → `admin_views.admin_export_cashouts_csv`**  
  - Generates CSV exports tailored for:
    - MTN Mobile Money.
    - Airtel Money.
    - Bank transfers.
    - Or a unified CSV for all methods.

- **`POST /admin-cashout-disburse/` → `admin_views.admin_disburse_cashouts`**  
  - Marks approved cashouts as disbursed:
    - Deducts funds from seller wallets.
    - Creates `cashout` transactions.
    - Records gateway reference IDs if provided.

Templates:
- `admin_transactions.html` – transaction list UI.
- `admin_cashout_requests.html` – cashout review dashboard.

---

### 9. Other templates (user‑facing, non‑admin)

These templates provide supporting UI pieces around the flows described above.

- **`base.html`**  
  - Generic layout with top nav (Fair Cashier brand, login/register, profile, logout) and footer.  
  - Used by non‑PIN pages such as `payment_confirm.html`, public auth, and profile pages.

- **`buyer_seller_base.html`**  
  - Minimal, dark, PIN‑centric base template for embedded flows (payment page, PIN setup/login, seller dashboard, etc.).

- **`pin_login.html` / `pin_setup.html` / `wallet_pin.html` / `buyer_dashboard_pin.html` / `seller_dashboard_pin.html`**  
  - PIN entry and dashboard UIs as detailed above.

- **`register_user.html` / `login_user.html`**  
  - General web login/registration views using the non‑PIN authentication path (where enabled).

- **`user_profile.html`**  
  - Profile page for logged‑in users (via `base.html` nav).

- **`platform_details.html` / `register_platform.html`**  
  - Legacy or simplified variants of platform management templates (used where mapped).

- **`error.html`**  
  - Simple error page used by multiple views when invalid params, tokens, or missing resources occur (e.g. invalid seller token, missing payment request).

- **`admin_login.html` / `admin_register.html` / `admin_dashboard.html` / `admin_disputes.html` / `admin_resolve_dispute.html` / `admin_cashout_requests.html` / `admin_transactions.html` / `admin_register_platform.html` / `admin_platform_details.html`**  
  - Covered under the admin/superadmin sections above.

- **`superadmin_dashboard.html` / `superadmin_users.html` / `superadmin_user_details.html`**  
  - Covered above; superadmin views.

- **`payment_confirm.html` / `payment_page.html` / `payment_page_pin.html`**  
  - Payment confirmation + processing UIs (see section 3.3).

- **`deposit_processing.html`**  
  - Likely used as an intermediate “Processing deposit” screen after initiating mobile money top‑up (depending on how `payment_processor` is wired).

- **`file_dispute.html` / `resolve_dispute.html`**  
  - Dispute filing/summary templates for web‑based flows (complementing API‑based dispute handling from the shopping app).

---

### 10. How everything fits together

- **Shopping app ↔ Fair Cashier (payment app)**  
  - Shopping app uses:
    - `/api/payment-request/create/` to initiate orders.
    - `/api/check-buyer-status/`, `/api/check-sellers/` and ZKP endpoints to decide whether to show wallet payment and which sellers are eligible.
    - Dispute APIs and webhooks to propagate dispute lifecycle.
  - It embeds:
    - `/payment/<request_id>/` inside an iframe on checkout (buy‑side).
    - `/payment/seller-dashboard/` inside an iframe on the seller portal (sell‑side).

- **Fair Cashier ↔ ZKP Strapi app**  
  - For **balance proofs** (buyer liquidity):
    - Uses `internal/order-created`, `internal/balance-proof/refresh`, and `internal/balance-proof/` endpoints to coordinate with the ZKP app so the shopping app can verify “buyer has enough funds” without seeing the actual balance.
  - For **seller KYC verification**:
    - Uses `/seller/zkp-verify/` (GET/POST) to talk to the ZKP app about whether a particular seller has passed KYC on the shopping platform.
    - Stores only cryptographic commitments and verification status, never raw KYC data.

- **Admins / operators** use:
  - The admin and superadmin dashboards, platform registration UIs, disputes UI, and cashout management routes to manage platforms, monitor transactions, resolve disputes, and orchestrate payouts to sellers.

This markdown file should give you a **URL‑by‑URL and template‑by‑template map** of the Fair Cashier payment app, and how it plugs into both the shopping app and the Strapi‑based ZKP verification service.

