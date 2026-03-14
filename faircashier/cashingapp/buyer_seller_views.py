# cashingapp/buyer_seller_views.py (COMPLETE with deposit/cashout)

from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.decorators.clickjacking import xframe_options_exempt
from django.http import JsonResponse
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.core.cache import cache
from decimal import Decimal, InvalidOperation
import logging
import hashlib
import json
import hmac
import math
from .models import (
    Users, Wallet, PaymentRequest, Transaction, 
    Platform, ActivityLog, PaymentRequestItem, MobileMoneyTransaction, CashoutRequest
)
from .pin_auth import PINAuthenticator
from .views import generate_confirmation_token

logger = logging.getLogger(__name__)

import hmac
import math

# ============= HELPER: BUILD REDIRECT URL =============

def _build_redirect_url(return_url, email):
    """
    Build proper redirect URL with token if payment flow
    
    Args:
        return_url: Original return URL (e.g., /payment/<uuid>/)
        email: User email
    
    Returns:
        Properly formatted redirect URL with token
    """
    if not return_url:
        # Default: redirect to wallet
        return f'/wallet-pin/?email={email}'
    
    # Check if this is a payment flow
    if '/payment/' in return_url:
        try:
            # Extract request_id from URL
            # Handles: /payment/uuid/ or /payment/uuid
            parts = return_url.split('/payment/')
            if len(parts) > 1:
                request_id = parts[1].split('?')[0].split('/')[0]
                
                if request_id:
                    # Generate confirmation token
                    token = generate_confirmation_token(request_id, email)
                    
                    # Build proper URL with token
                    base_url = f'/payment/{request_id}/'
                    
                    logger.info(f"✅ Generated payment redirect: {base_url}?confirmed={token}")
                    
                    return f"{base_url}?confirmed={token}"
        except Exception as e:
            logger.error(f"❌ Error building payment redirect: {str(e)}")
    
    # For non-payment URLs, return as-is (might be /wallet-pin/, etc.)
    return return_url


# ============= PIN SETUP =============

@csrf_exempt
@xframe_options_exempt
def pin_setup(request):
    """PIN setup for buyers/sellers"""
    email = request.GET.get('email', '')
    return_url = request.GET.get('return', '')
    
    if request.method == 'POST':
        data = request.POST
        email = data.get('email')
        pin = data.get('pin')
        confirm_pin = data.get('confirm_pin')
        phone_number = data.get('phone_number', '')
        role = data.get('role', 'buyer')
        
        if not email:
            return JsonResponse({'error': 'Email required'}, status=400)
        
        if Users.objects.filter(email=email).exists():
            return JsonResponse({'error': 'Account exists. Use PIN login.'}, status=400)
        
        try:
            with db_transaction.atomic():
                # Create user WITHOUT password
                user = Users.objects.create(
                    email=email,
                    role=role,
                    phone_number=phone_number,
                    is_active=True,
                    is_staff=False,
                    is_superuser=False
                )
                user.set_unusable_password()
                
                # Set PIN
                result = PINAuthenticator.set_pin(user, pin, confirm_pin)
                
                if not result['success']:
                    user.delete()
                    return JsonResponse({'error': result['error']}, status=400)
                
                # Create wallet
                Wallet.objects.create(user=user)
                
                logger.info(f"✅ PIN setup: {email} ({role})")
                
                # ✅ FIX: Generate proper redirect URL
                redirect_url = _build_redirect_url(return_url, email)
                
                return JsonResponse({
                    'success': True,
                    'message': 'Account created',
                    'redirect_url': redirect_url
                }, status=201)
                
        except Exception as e:
            logger.error(f"❌ PIN setup error: {str(e)}")
            return JsonResponse({'error': 'Registration failed'}, status=500)
    
    context = {
        'prefill_email': email,
        'return_url': return_url,
    }
    return render(request, 'pin_setup.html', context)


# ============= PIN LOGIN =============

@csrf_exempt
@xframe_options_exempt
def pin_login(request):
    """PIN login for buyers/sellers"""
    email = request.GET.get('email', '')
    return_url = request.GET.get('return', '')
    
    if request.method == 'POST':
        data = request.POST
        email = data.get('email')
        pin = data.get('pin')
        
        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN required'}, status=400)
        
        try:
            user = Users.objects.get(email=email, role__in=['buyer', 'seller'])
        except Users.DoesNotExist:
            return JsonResponse({'error': 'Account not found'}, status=404)
        
        # Verify PIN
        result = PINAuthenticator.verify_pin(user, pin)
        
        if not result['valid']:
            response_data = {'error': result['error']}
            if result['attempts_remaining'] is not None:
                response_data['attempts_remaining'] = result['attempts_remaining']
            return JsonResponse(response_data, status=401)
        
        # ✅ FIX: Generate proper redirect URL
        redirect_url = _build_redirect_url(return_url, email)
        
        logger.info(f"✅ PIN login: {email}")
        
        return JsonResponse({
            'success': True,
            'message': 'Login successful',
            'redirect_url': redirect_url
        }, status=200)
    
    context = {
        'prefill_email': email,
        'return_url': return_url,
    }
    return render(request, 'pin_login.html', context)



# ============= PAYMENT PROCESSING WITH PIN =============

@csrf_exempt
@xframe_options_exempt
def process_payment_with_pin(request, request_id):
    """Process payment with PIN verification - stateless"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=400)
    
    try:
        data = request.POST
        email = data.get('email')
        pin = data.get('pin')
        
        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN required'}, status=400)
        
        payment_request = PaymentRequest.objects.get(request_id=request_id)
        
        if email != payment_request.buyer_email:
            return JsonResponse({'error': 'Unauthorized'}, status=403)
        
        try:
            buyer = Users.objects.get(email=email, role='buyer')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'Buyer not found'}, status=404)
        
        # Verify PIN
        pin_result = PINAuthenticator.verify_pin(buyer, pin)
        
        if not pin_result['valid']:
            response_data = {'error': pin_result['error']}
            if pin_result['attempts_remaining'] is not None:
                response_data['attempts_remaining'] = pin_result['attempts_remaining']
            return JsonResponse(response_data, status=401)
        
        # Process payment — escrow into seller's reserved balance
        buyer_wallet = buyer.wallet

        if buyer_wallet.free_balance < payment_request.total_amount:
            return JsonResponse({
                'error': 'Insufficient balance',
                'required':  str(payment_request.total_amount),
                'available': str(buyer_wallet.free_balance),
            }, status=400)

        with db_transaction.atomic():
            buyer_wl = Wallet.objects.select_for_update().get(pk=buyer_wallet.pk)

            for item in payment_request.items.all():
                seller    = Users.objects.get(email=item.seller_email, role='seller')
                seller_wl = Wallet.objects.select_for_update().get(pk=seller.wallet.pk)

                txn = Transaction.objects.create(
                    platform=payment_request.platform,
                    from_wallet=buyer_wl,
                    to_wallet=seller_wl,
                    amount=item.amount,
                    transaction_type='transfer',
                    status='completed',
                    description=(
                        f'Escrow hold (awaiting delivery confirmation): '
                        f'{item.product_description or item.seller_email}'
                    ),
                )

                buyer_wl.balance           -= item.amount
                seller_wl.balance          += item.amount
                seller_wl.reserved_balance += item.amount
                buyer_wl.save(update_fields=['balance', 'updated_at'])
                seller_wl.save(update_fields=['balance', 'reserved_balance', 'updated_at'])

                item.transaction      = txn
                item.is_escrowed      = True
                item.escrowed_amount  = item.amount
                item.escrowed_at      = timezone.now()
                item.save(update_fields=[
                    'transaction', 'is_escrowed', 'escrowed_amount', 'escrowed_at', 'updated_at',
                ])

            payment_request.status = 'escrowed'
            payment_request.save()

        logger.info(f"✅ Payment escrowed: {request_id}")

        return JsonResponse({
            'success': True,
            'message': 'Payment successful — funds held until delivery confirmed',
            'request_id': str(request_id),
            'amount': str(payment_request.total_amount),
        }, status=200)
        
    except PaymentRequest.DoesNotExist:
        return JsonResponse({'error': 'Payment request not found'}, status=404)
    except Exception as e:
        logger.error(f"❌ Payment error: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Payment failed'}, status=500)


# ============= WALLET VIEW (PIN-PROTECTED) =============

@csrf_exempt
@xframe_options_exempt
def wallet_view_pin(request):
    """Wallet view with PIN verification - stateless"""
    if request.method == 'POST':
        data = request.POST
        email = data.get('email')
        pin = data.get('pin')
        
        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN required'}, status=400)
        
        try:
            user = Users.objects.get(email=email, role__in=['buyer', 'seller'])
        except Users.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
        
        result = PINAuthenticator.verify_pin(user, pin)
        
        if not result['valid']:
            return JsonResponse({'error': result['error']}, status=401)
        
        wallet = user.wallet
        transactions = Transaction.objects.filter(
            Q(from_wallet=wallet) | Q(to_wallet=wallet)
        ).order_by('-created_at')[:10]
        
        return JsonResponse({
            'success': True,
            'wallet': {
                'balance': str(wallet.balance),
                'reserved_balance': str(wallet.reserved_balance),
                'free_balance':     str(wallet.free_balance),
                'currency': wallet.currency
            },
            'transactions': [
                {
                    'id': str(t.transaction_id),
                    'type': t.transaction_type,
                    'amount': str(t.amount),
                    'status': t.status,
                    'created_at': t.created_at.isoformat()
                }
                for t in transactions
            ]
        })
    
    email = request.GET.get('email', '')
    return render(request, 'wallet_pin.html', {'email': email})


# ============= BUYER DASHBOARD =============

@csrf_exempt
def buyer_dashboard(request):
    """Buyer dashboard with PIN verification"""
    if request.method == 'POST':
        email = request.POST.get('email')
        pin = request.POST.get('pin')
        
        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN required'}, status=400)
        
        try:
            user = Users.objects.get(email=email, role='buyer')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
        
        result = PINAuthenticator.verify_pin(user, pin)
        
        if not result['valid']:
            return JsonResponse({'error': result['error']}, status=401)
        
        wallet = user.wallet
        recent_transactions = Transaction.objects.filter(
            Q(from_wallet=wallet) | Q(to_wallet=wallet)
        ).order_by('-created_at')[:10]
        
        payment_requests = PaymentRequest.objects.filter(
            buyer_email=user.email
        ).order_by('-created_at')[:10]
        
        return JsonResponse({
            'success': True,
            'wallet': {'balance': str(wallet.balance), 'currency': wallet.currency},
            'transactions': [
                {
                    'id': str(t.transaction_id),
                    'type': t.transaction_type,
                    'amount': str(t.amount),
                    'created_at': t.created_at.isoformat()
                }
                for t in recent_transactions
            ],
            'payments': [
                {
                    'id': str(p.request_id),
                    'amount': str(p.total_amount),
                    'status': p.status
                }
                for p in payment_requests
            ]
        })
    
    email = request.GET.get('email', '')
    return render(request, 'buyer_dashboard_pin.html', {'email': email})



 
# ── tuneable constants ────────────────────────────────────────────────────────
_IDEMPOTENCY_WINDOW_SECONDS = 30   # duplicate-request guard window
_IDEMPOTENCY_TTL_SECONDS    = 60   # cache-key TTL
_DEPOSIT_MIN_UGX  = Decimal("1000")
_CASHOUT_MIN_UGX  = Decimal("5000")
# ─────────────────────────────────────────────────────────────────────────────
 
 
# =============================================================================
# Shared helpers
# =============================================================================
 
def _idempotency_key(prefix: str, user_id: int, amount: Decimal,
                     phone: str, platform_id: str) -> str:
    """
    Build an opaque cache key unique per (user, amount, phone, platform)
    within the current 30-second time-bucket.
 
    The key is an HMAC-SHA256 digest so it cannot be guessed or enumerated
    by inspecting cache storage.
    """
    bucket = math.floor(
        timezone.now().timestamp() / _IDEMPOTENCY_WINDOW_SECONDS
    )
    raw    = f"{prefix}:{user_id}:{amount}:{phone}:{platform_id}:{bucket}"
    digest = hmac.new(b"fc-idempotency-v2", raw.encode(), hashlib.sha256).hexdigest()
    return f"idem:{prefix}:{digest}"
 
 
def _log_activity(user, action: str, description: str,
                  request=None, metadata: dict | None = None,
                  platform=None) -> None:
    """Write one ActivityLog row and one Python logger line."""
    ip = None
    if request:
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        ip  = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")
 
    ActivityLog.objects.create(
        user=user,
        platform=platform,
        action=action,
        description=description,
        ip_address=ip,
        metadata=metadata or {},
    )
    logger.info(
        "[%s] user=%s ip=%s platform=%s | %s",
        action,
        getattr(user, "email", "?"),
        ip,
        getattr(platform, "platform_id", "?"),
        description,
    )
 
 
def _parse_decimal(raw) -> Decimal | None:
    """Return Decimal or None — never raises."""
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
 
 
# =============================================================================
# deposit_pin
# =============================================================================
 
@csrf_exempt
@xframe_options_exempt
def deposit_pin(request):
    """
    POST /deposit-pin/
    Collect money from the user's mobile-money account and credit their
    wallet's FREE balance (balance += amount; reserved_balance unchanged).
 
    Form fields
    -----------
    email        - account email
    pin          - 4-digit wallet PIN
    amount       - UGX integer, min 1 000
    phone_number - MSISDN e.g. 256700000000
    platform_id  - Platform.platform_id
 
    Success response (HTTP 200)
    ---------------------------
    {
        success:          true,
        transaction_id:   "<uuid>",
        reference_id:     "<gateway ref>",
        new_balance:      "<Decimal str>",
        new_free_balance: "<Decimal str>",
        next_action:      "<human string>"
    }
 
    Wallet update (inside select_for_update + atomic, AFTER gateway confirms)
    --------------------------------------------------------------------------
        wallet.balance          += amount
        wallet.reserved_balance  -- UNCHANGED
        wallet.free_balance      += amount   (property: balance - reserved)
    """
    # GET: render the template
    if request.method == "GET":
        return render(request, "deposit_pin.html", {
            "email":     request.GET.get("email", ""),
            "platforms": Platform.objects.filter(is_active=True),
        })
 
    # -- parse ------------------------------------------------------------
    email       = (request.POST.get("email")        or "").strip()
    pin         = (request.POST.get("pin")           or "").strip()
    phone       = (request.POST.get("phone_number")  or "").strip()
    platform_id = (request.POST.get("platform_id")   or "").strip()
    amount      = _parse_decimal(request.POST.get("amount"))
 
    # -- field validation -------------------------------------------------
    if not all([email, pin, phone, platform_id]):
        return JsonResponse(
            {"error": "email, pin, phone_number, and platform_id are required"},
            status=400,
        )
    if amount is None or amount <= 0:
        return JsonResponse({"error": "Invalid amount"}, status=400)
    if amount < _DEPOSIT_MIN_UGX:
        return JsonResponse(
            {"error": f"Minimum deposit is {int(_DEPOSIT_MIN_UGX):,} UGX"},
            status=400,
        )
 
    # -- resolve user -----------------------------------------------------
    try:
        user = Users.objects.get(email=email, role__in=["buyer", "seller"])
    except Users.DoesNotExist:
        return JsonResponse({"error": "Account not found"}, status=404)
 
    # -- 1. PIN first, always ---------------------------------------------
    pin_result = PINAuthenticator.verify_pin(user, pin)
    if not pin_result["valid"]:
        _log_activity(
            user, "deposit",
            f"PIN verification failed — deposit {amount} UGX attempted",
            request=request,
            metadata={"amount": str(amount), "phone": phone,
                      "reason": pin_result.get("error")},
        )
        resp = {"error": pin_result["error"]}
        if pin_result.get("attempts_remaining") is not None:
            resp["attempts_remaining"] = pin_result["attempts_remaining"]
        return JsonResponse(resp, status=401)
 
    # -- 2. Idempotency guard ---------------------------------------------
    idem_key = _idempotency_key("deposit", user.id, amount, phone, platform_id)
    cached   = cache.get(idem_key)
    if cached:
        logger.info("Duplicate deposit suppressed — user=%s", email)
        return JsonResponse(cached, status=200)
 
    # -- 3. Resolve platform ----------------------------------------------
    try:
        platform = Platform.objects.get(platform_id=platform_id, is_active=True)
    except Platform.DoesNotExist:
        return JsonResponse({"error": "Invalid or inactive platform"}, status=400)
 
    # -- 4. Gateway + atomic wallet credit --------------------------------
    try:
        from .payment_processor import PaymentProcessor
 
        processor    = PaymentProcessor(
            api_key=platform.mobile_money_api_key,
            provider=platform.mobile_money_provider,
        )
        api_response = processor.request_collection(
            phone_number=phone,
            amount=float(amount),       # gateway boundary — float expected
            description="Deposit to Fair Cashier",
        )
 
        if api_response["status"] != "success":
            _log_activity(
                user, "deposit",
                f"Gateway rejected deposit {amount} UGX: {api_response.get('message')}",
                request=request, platform=platform,
                metadata={
                    "amount":     str(amount), "phone": phone,
                    "error":      api_response.get("message"),
                    "error_code": api_response.get("error_code"),
                },
            )
            return JsonResponse(
                {"error": api_response.get("message", "Deposit failed")},
                status=400,
            )
 
        # Gateway approved.
        # Credit the wallet inside a single select_for_update + atomic block.
        # This serialises any concurrent deposit or cashout on this wallet row.
        # NOTE: we do NOT call complete_pending_deposit() here — that helper
        # runs its own internal atomic(), which would be a nested/separate
        # transaction and would escape the lock we hold here.
        with db_transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=user)
 
            # Credit free balance.
            # reserved_balance is NEVER touched by a plain deposit —
            # free_balance (a computed property) therefore rises by exactly
            # the deposited amount.
            wallet.balance += amount
            wallet.save(update_fields=["balance", "updated_at"])
 
            txn = Transaction.objects.create(
                platform=platform,
                to_wallet=wallet,
                amount=amount,
                transaction_type="deposit",
                status="completed",
                mobile_money_reference=api_response.get("reference_id"),
                description=(
                    f"Deposit via {platform.get_mobile_money_provider_display()} "
                    f"from {phone}"
                ),
            )
 
            MobileMoneyTransaction.objects.create(
                platform=platform,
                transaction=txn,
                operation_type="collection",
                phone_number=phone,
                amount=amount,
                external_reference=api_response.get(
                    "reference_id", str(txn.transaction_id)
                ),
                api_response=api_response,
                status="successful",
            )
 
        # Refresh outside the lock so the response reflects the committed state.
        wallet.refresh_from_db()
 
        response_payload = {
            "success":          True,
            "transaction_id":   str(txn.transaction_id),
            "reference_id":     api_response.get("reference_id"),
            "new_balance":      str(wallet.balance),
            "new_free_balance": str(wallet.free_balance),   # balance - reserved
            "next_action":      api_response.get(
                "next_action", "Funds added to your free balance"
            ),
        }
 
        # Cache so an identical re-submission in the window returns this
        # result without touching the DB.
        cache.set(idem_key, response_payload, _IDEMPOTENCY_TTL_SECONDS)
 
        _log_activity(
            user, "deposit",
            (
                f"Deposit {amount} UGX completed — "
                f"balance {wallet.balance} UGX, "
                f"free {wallet.free_balance} UGX, "
                f"reserved {wallet.reserved_balance} UGX"
            ),
            request=request, platform=platform,
            metadata={
                "amount":           str(amount),
                "phone":            phone,
                "transaction_id":   str(txn.transaction_id),
                "reference_id":     api_response.get("reference_id"),
                "new_balance":      str(wallet.balance),
                "new_free_balance": str(wallet.free_balance),
                "reserved_balance": str(wallet.reserved_balance),
            },
        )
 
        return JsonResponse(response_payload, status=200)
 
    except Exception as exc:
        logger.exception("Unexpected error in deposit_pin — user=%s", email)
        _log_activity(
            user, "deposit",
            f"Unexpected deposit error: {exc}",
            request=request,
            platform=locals().get("platform"),
            metadata={"amount": str(amount), "error": str(exc)},
        )
        return JsonResponse(
            {"error": "An unexpected error occurred. Please try again."},
            status=500,
        )
 
 
# =============================================================================
# cashout_pin
# =============================================================================
 
@csrf_exempt
@xframe_options_exempt
def cashout_pin(request):
    """
    POST /cashout-pin/
    Disburse from the user's wallet FREE balance to their mobile-money account.
 
    Form fields
    -----------
    email        - account email
    pin          - 4-digit wallet PIN
    amount       - UGX integer, min 5 000
    phone_number - MSISDN e.g. 256700000000
    platform_id  - Platform.platform_id
 
    Success response (HTTP 200)
    ---------------------------
    {
        success:          true,
        transaction_id:   "<uuid>",
        new_balance:      "<Decimal str>",
        new_free_balance: "<Decimal str>"
    }
 
    Failure — insufficient free balance (HTTP 400)
    -----------------------------------------------
    {
        error:            "Insufficient free balance",
        available:        "<free_balance>",
        total_balance:    "<balance>",
        reserved_balance: "<reserved_balance>",
        note:             "<human explanation if reserved > 0>"
    }
 
    Wallet update (inside select_for_update + atomic, AFTER gateway confirms)
    --------------------------------------------------------------------------
        wallet.balance          -= amount
        wallet.reserved_balance  -- UNCHANGED
        wallet.free_balance      -= amount   (property: balance - reserved)
    """
    # GET: render the template
    if request.method == "GET":
        return render(request, "cashout_pin.html", {
            "email":     request.GET.get("email", ""),
            "platforms": Platform.objects.filter(is_active=True),
        })
 
    # -- parse ------------------------------------------------------------
    email       = (request.POST.get("email")        or "").strip()
    pin         = (request.POST.get("pin")           or "").strip()
    phone       = (request.POST.get("phone_number")  or "").strip()
    platform_id = (request.POST.get("platform_id")   or "").strip()
    amount      = _parse_decimal(request.POST.get("amount"))
 
    # -- field validation -------------------------------------------------
    if not all([email, pin, phone, platform_id]):
        return JsonResponse(
            {"error": "email, pin, phone_number, and platform_id are required"},
            status=400,
        )
    if amount is None or amount <= 0:
        return JsonResponse({"error": "Invalid amount"}, status=400)
    if amount < _CASHOUT_MIN_UGX:
        return JsonResponse(
            {"error": f"Minimum cashout is {int(_CASHOUT_MIN_UGX):,} UGX"},
            status=400,
        )
 
    # -- resolve user -----------------------------------------------------
    try:
        user = Users.objects.get(email=email, role__in=["buyer", "seller"])
    except Users.DoesNotExist:
        return JsonResponse({"error": "Account not found"}, status=404)
 
    # -- 1. PIN first, always ---------------------------------------------
    pin_result = PINAuthenticator.verify_pin(user, pin)
    if not pin_result["valid"]:
        _log_activity(
            user, "cashout",
            f"PIN verification failed — cashout {amount} UGX attempted",
            request=request,
            metadata={"amount": str(amount), "phone": phone,
                      "reason": pin_result.get("error")},
        )
        resp = {"error": pin_result["error"]}
        if pin_result.get("attempts_remaining") is not None:
            resp["attempts_remaining"] = pin_result["attempts_remaining"]
        return JsonResponse(resp, status=401)
 
    # -- 2. Idempotency guard ---------------------------------------------
    idem_key = _idempotency_key("cashout", user.id, amount, phone, platform_id)
    cached   = cache.get(idem_key)
    if cached:
        logger.info("Duplicate cashout suppressed — user=%s", email)
        return JsonResponse(cached, status=200)
 
    # -- 3. Fast pre-flight free-balance check (no lock yet) --------------
    # Avoids a gateway round-trip for obviously insufficient requests.
    # A second locked re-check happens after the gateway call.
    snapshot = user.wallet
    if snapshot.free_balance < amount:
        _log_activity(
            user, "cashout",
            f"Pre-flight failed: free {snapshot.free_balance} < requested {amount}",
            request=request,
            metadata={
                "amount":           str(amount),
                "free_balance":     str(snapshot.free_balance),
                "total_balance":    str(snapshot.balance),
                "reserved_balance": str(snapshot.reserved_balance),
            },
        )
        return JsonResponse(
            {
                "error":            "Insufficient free balance",
                "available":        str(snapshot.free_balance),
                "total_balance":    str(snapshot.balance),
                "reserved_balance": str(snapshot.reserved_balance),
                "note": (
                    "Some funds are reserved in escrow and cannot be withdrawn yet."
                    if snapshot.reserved_balance > 0 else ""
                ),
            },
            status=400,
        )
 
    # -- 4. Resolve platform ----------------------------------------------
    try:
        platform = Platform.objects.get(platform_id=platform_id, is_active=True)
    except Platform.DoesNotExist:
        return JsonResponse({"error": "Invalid or inactive platform"}, status=400)
 
    # -- 5. Gateway call (outside lock — may be slow) ---------------------
    try:
        from .payment_processor import PaymentProcessor
 
        processor    = PaymentProcessor(
            api_key=platform.mobile_money_api_key,
            provider=platform.mobile_money_provider,
        )
        api_response = processor.request_disbursement(
            phone_number=phone,
            amount=float(amount),
            description="Withdrawal from Fair Cashier",
        )
 
        if api_response["status"] != "success":
            _log_activity(
                user, "cashout",
                f"Gateway rejected cashout {amount} UGX: {api_response.get('message')}",
                request=request, platform=platform,
                metadata={
                    "amount":     str(amount), "phone": phone,
                    "error":      api_response.get("message"),
                    "error_code": api_response.get("error_code"),
                },
            )
            return JsonResponse(
                {"error": api_response.get("message", "Cashout failed")},
                status=400,
            )
 
        # -- 6. Gateway approved — debit wallet inside lock ---------------
        # select_for_update() serialises concurrent cashout/deposit requests
        # on this wallet row.  We re-check free_balance under the lock to
        # catch the race where another request spent these funds between our
        # pre-flight check and now.
        with db_transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=user)
 
            if wallet.free_balance < amount:
                # Race condition caught.  The gateway disbursement already
                # fired — in production this needs a reversal/reconciliation
                # workflow.  We log a CRITICAL error for ops and return 400
                # so the client knows something went wrong.
                logger.error(
                    "CRITICAL reconciliation needed: gateway disbursed %s UGX "
                    "to %s for user=%s but wallet free_balance=%s at lock time.",
                    amount, phone, email, wallet.free_balance,
                )
                _log_activity(
                    user, "cashout",
                    (
                        f"RACE CONDITION: gateway disbursed {amount} UGX but "
                        f"wallet free_balance={wallet.free_balance} under lock — "
                        f"manual reconciliation required"
                    ),
                    request=request, platform=platform,
                    metadata={
                        "amount":           str(amount),
                        "free_balance":     str(wallet.free_balance),
                        "total_balance":    str(wallet.balance),
                        "reserved_balance": str(wallet.reserved_balance),
                        "phone":            phone,
                        "gateway_ref":      api_response.get("reference_id"),
                    },
                )
                return JsonResponse(
                    {
                        "error":            "Insufficient free balance",
                        "available":        str(wallet.free_balance),
                        "total_balance":    str(wallet.balance),
                        "reserved_balance": str(wallet.reserved_balance),
                    },
                    status=400,
                )
 
            # Debit balance only.  reserved_balance is NEVER touched by a
            # cashout — free_balance therefore drops by exactly amount.
            wallet.balance -= amount
            wallet.save(update_fields=["balance", "updated_at"])
 
            txn = Transaction.objects.create(
                platform=platform,
                from_wallet=wallet,
                amount=amount,
                transaction_type="cashout",
                status="completed",
                mobile_money_reference=api_response.get("reference_id"),
                description=(
                    f"Cashout via {platform.get_mobile_money_provider_display()} "
                    f"to {phone}"
                ),
            )
 
            MobileMoneyTransaction.objects.create(
                platform=platform,
                transaction=txn,
                operation_type="disbursement",
                phone_number=phone,
                amount=amount,
                external_reference=api_response.get(
                    "reference_id", str(txn.transaction_id)
                ),
                api_response=api_response,
                status="successful",
            )
 
        wallet.refresh_from_db()
 
        response_payload = {
            "success":          True,
            "transaction_id":   str(txn.transaction_id),
            "new_balance":      str(wallet.balance),
            "new_free_balance": str(wallet.free_balance),
        }
 
        cache.set(idem_key, response_payload, _IDEMPOTENCY_TTL_SECONDS)
 
        _log_activity(
            user, "cashout",
            (
                f"Cashout {amount} UGX completed to {phone} via "
                f"{platform.platform_name} — "
                f"balance {wallet.balance} UGX, "
                f"free {wallet.free_balance} UGX, "
                f"reserved {wallet.reserved_balance} UGX"
            ),
            request=request, platform=platform,
            metadata={
                "amount":           str(amount),
                "phone":            phone,
                "transaction_id":   str(txn.transaction_id),
                "reference_id":     api_response.get("reference_id"),
                "new_balance":      str(wallet.balance),
                "new_free_balance": str(wallet.free_balance),
                "reserved_balance": str(wallet.reserved_balance),
            },
        )
 
        return JsonResponse(response_payload, status=200)
 
    except Exception as exc:
        logger.exception("Unexpected error in cashout_pin — user=%s", email)
        _log_activity(
            user, "cashout",
            f"Unexpected cashout error: {exc}",
            request=request,
            platform=locals().get("platform"),
            metadata={"amount": str(amount), "error": str(exc)},
        )
        return JsonResponse(
            {"error": "An unexpected error occurred. Please try again."},
            status=500,
        )
 









# ============= DEPOSIT AND PAY (SEAMLESS FLOW) =============
# ADD this to the end of cashingapp/buyer_seller_views.py

@csrf_exempt
@xframe_options_exempt
def deposit_and_pay(request, request_id):
    """
    Seamless deposit + payment in one step.
    
    When buyer has insufficient balance:
    1. Calculates shortfall
    2. Initiates mobile money deposit for the shortfall
    3. Auto-completes deposit (simulated webhook)
    4. Processes the payment with combined balance
    5. Returns success to iframe
    
    POST body:
        - email: Buyer's email
        - pin: 4-digit PIN
        - phone_number: Mobile money number for deposit
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=400)
    
    try:
        data = request.POST
        email = data.get('email')
        pin = data.get('pin')
        phone_number = data.get('phone_number')
        
        if not all([email, pin, phone_number]):
            return JsonResponse({
                'error': 'Email, PIN, and phone number are required'
            }, status=400)
        
        # Get payment request
        payment_request = PaymentRequest.objects.get(request_id=request_id)
        
        # Verify buyer identity
        if email != payment_request.buyer_email:
            return JsonResponse({'error': 'Unauthorized'}, status=403)
        
        try:
            buyer = Users.objects.get(email=email, role='buyer')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'Buyer not found'}, status=404)
        
        # Verify PIN
        pin_result = PINAuthenticator.verify_pin(buyer, pin)
        
        if not pin_result['valid']:
            response_data = {'error': pin_result['error']}
            if pin_result['attempts_remaining'] is not None:
                response_data['attempts_remaining'] = pin_result['attempts_remaining']
            return JsonResponse(response_data, status=401)
        
        # Calculate shortfall
        buyer_wallet = buyer.wallet
        shortfall = payment_request.total_amount - buyer_wallet.balance
        
        if shortfall > Decimal('0'):
            # Need to deposit the shortfall first
            platform = payment_request.platform
            
            if not platform:
                return JsonResponse({
                    'error': 'No payment platform configured'
                }, status=400)
            
            from .payment_processor import process_deposit, complete_pending_deposit
            
            logger.info(
                f"💰 Initiating deposit of {shortfall} UGX for {email} "
                f"(balance: {buyer_wallet.balance}, needed: {payment_request.total_amount})"
            )
            
            # Step 1: Initiate deposit
            deposit_result = process_deposit(
                user=buyer,
                platform=platform,
                amount=shortfall,
                phone_number=phone_number
            )
            
            if deposit_result['status'] != 'success':
                logger.error(f"❌ Deposit failed for {email}: {deposit_result.get('message')}")
                return JsonResponse({
                    'error': deposit_result.get('message', 'Deposit failed. Please try again.'),
                    'deposit_failed': True
                }, status=400)
            
            # Step 2: Complete deposit (simulated webhook callback)
            # In production, this would be triggered by the mobile money provider's webhook
            completion_result = complete_pending_deposit(
                transaction_id=deposit_result['transaction_id'],
                external_reference=deposit_result['reference_id']
            )
            
            if completion_result['status'] != 'success':
                logger.error(f"❌ Deposit completion failed for {email}")
                return JsonResponse({
                    'error': 'Deposit could not be completed. Please try again.',
                    'deposit_failed': True
                }, status=400)
            
            logger.info(f"✅ Deposit of {shortfall} UGX completed for {email}")
            
            # Refresh wallet balance from DB
            buyer_wallet.refresh_from_db()
        
        # Final balance check
        if buyer_wallet.balance < payment_request.total_amount:
            return JsonResponse({
                'error': 'Insufficient balance even after deposit',
                'required': str(payment_request.total_amount),
                'available': str(buyer_wallet.balance)
            }, status=400)
        
        # Step 3: Process the payment — escrow into seller's reserved balance
        with db_transaction.atomic():
            buyer_wl = Wallet.objects.select_for_update().get(pk=buyer_wallet.pk)

            for item in payment_request.items.all():
                seller    = Users.objects.get(email=item.seller_email, role='seller')
                seller_wl = Wallet.objects.select_for_update().get(pk=seller.wallet.pk)

                txn = Transaction.objects.create(
                    platform=payment_request.platform,
                    from_wallet=buyer_wl,
                    to_wallet=seller_wl,
                    amount=item.amount,
                    transaction_type='transfer',
                    status='completed',
                    description=(
                        f'Escrow hold (awaiting delivery confirmation): '
                        f'{item.product_description or item.seller_email}'
                    ),
                )

                buyer_wl.balance           -= item.amount
                seller_wl.balance          += item.amount
                seller_wl.reserved_balance += item.amount
                buyer_wl.save(update_fields=['balance', 'updated_at'])
                seller_wl.save(update_fields=['balance', 'reserved_balance', 'updated_at'])

                item.transaction      = txn
                item.is_escrowed      = True
                item.escrowed_amount  = item.amount
                item.escrowed_at      = timezone.now()
                item.save(update_fields=[
                    'transaction', 'is_escrowed', 'escrowed_amount', 'escrowed_at', 'updated_at',
                ])

            payment_request.status = 'escrowed'
            payment_request.save()

        logger.info(f"✅ Deposit+Escrow completed for {email}: {request_id}")

        return JsonResponse({
            'success': True,
            'message': 'Payment successful — funds held until delivery confirmed',
            'request_id': str(request_id),
            'amount': str(payment_request.total_amount),
            'deposited': str(max(shortfall, Decimal('0'))),
        })
        
    except PaymentRequest.DoesNotExist:
        return JsonResponse({'error': 'Payment request not found'}, status=404)
    except Exception as e:
        logger.error(f"❌ Deposit+Pay error: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Payment failed. Please try again.'}, status=500)
    


# ============= SELLER CASHOUT REQUEST =============


@csrf_exempt
@xframe_options_exempt
def seller_request_cashout(request):
    """
    Seller cashout request form.
    
    Creates a CashoutRequest for the platform admin to review and disburse.
    Seller selects payment method (MTN/Airtel/Bank) and provides details.
    PIN verification required.
    
    GET params: email, platform_id
    POST: amount, payment_method, phone_number/bank details, pin
    """
    email = request.GET.get('email', '')
    platform_id = request.GET.get('platform_id', '')

    if request.method == 'POST':
        data = request.POST
        email = data.get('email')
        pin = data.get('pin')
        amount_str = data.get('amount', '0')
        payment_method = data.get('payment_method')
        phone_number = data.get('phone_number', '').strip()
        recipient_name = data.get('recipient_name', '').strip()
        bank_name = data.get('bank_name', '').strip()
        account_number = data.get('account_number', '').strip()
        account_name = data.get('account_name', '').strip()
        seller_note = data.get('seller_note', '').strip()
        platform_id = data.get('platform_id')

        if not all([email, pin, amount_str, payment_method, platform_id]):
            return JsonResponse({
                'error': 'All required fields must be filled'
            }, status=400)

        # Validate amount
        try:
            amount = Decimal(amount_str)
        except Exception:
            return JsonResponse({'error': 'Invalid amount'}, status=400)

        if amount < Decimal('5000'):
            return JsonResponse({
                'error': 'Minimum cashout amount is 5,000 UGX'
            }, status=400)

        # Verify seller
        try:
            seller = Users.objects.get(email=email, role='seller')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'Seller not found'}, status=404)

        # Verify PIN
        pin_result = PINAuthenticator.verify_pin(seller, pin)
        if not pin_result['valid']:
            response_data = {'error': pin_result['error']}
            if pin_result['attempts_remaining'] is not None:
                response_data['attempts_remaining'] = pin_result['attempts_remaining']
            return JsonResponse(response_data, status=401)

        # Verify platform
        try:
            platform = Platform.objects.get(platform_id=platform_id, is_active=True)
        except Platform.DoesNotExist:
            return JsonResponse({'error': 'Invalid platform'}, status=400)

        # Verify balance
        wallet = seller.wallet
        if wallet.free_balance < amount:
            msg = f'Insufficient free balance. Available: {wallet.free_balance:,.0f} UGX'
            if wallet.reserved_balance > 0:
                msg += f' ({wallet.reserved_balance:,.0f} UGX reserved in escrow)'
            return JsonResponse({
                'error': msg,
                'available':        str(wallet.free_balance),
                'total_balance':    str(wallet.balance),
                'reserved_balance': str(wallet.reserved_balance),
            }, status=400)

        # Validate payment method specific fields
        if payment_method in ('mtn_mobile_money', 'airtel_mobile_money'):
            if not phone_number:
                return JsonResponse({
                    'error': 'Phone number is required for mobile money'
                }, status=400)
            if not recipient_name:
                return JsonResponse({
                    'error': 'Recipient name is required'
                }, status=400)
        elif payment_method == 'bank_transfer':
            if not all([bank_name, account_number, account_name]):
                return JsonResponse({
                    'error': 'Bank name, account number, and account name are required'
                }, status=400)
        else:
            return JsonResponse({'error': 'Invalid payment method'}, status=400)

        # Check for duplicate pending requests
        existing = CashoutRequest.objects.filter(
            seller=seller,
            status='pending',
            amount=amount,
            payment_method=payment_method
        ).exists()

        if existing:
            return JsonResponse({
                'error': 'You already have a pending cashout request for this amount and method'
            }, status=400)

        try:
            cashout = CashoutRequest.objects.create(
                seller=seller,
                platform=platform,
                amount=amount,
                payment_method=payment_method,
                phone_number=phone_number if payment_method != 'bank_transfer' else None,
                recipient_name=recipient_name if payment_method != 'bank_transfer' else None,
                bank_name=bank_name if payment_method == 'bank_transfer' else None,
                account_number=account_number if payment_method == 'bank_transfer' else None,
                account_name=account_name if payment_method == 'bank_transfer' else None,
                seller_note=seller_note,
                status='pending'
            )

            logger.info(
                f"✅ Cashout request created: {cashout.cashout_id} "
                f"by {email} for {amount} UGX via {payment_method}"
            )

            return JsonResponse({
                'success': True,
                'message': 'Cashout request submitted successfully',
                'cashout_id': cashout.cashout_id,
                'amount': str(amount),
                'payment_method': cashout.get_payment_method_display(),
                'status': 'pending'
            })

        except Exception as e:
            logger.error(f"❌ Cashout request error: {str(e)}", exc_info=True)
            return JsonResponse({
                'error': 'Failed to submit cashout request'
            }, status=500)

    # GET: Show cashout request form
    # Get seller's pending cashout requests
    pending_requests = []
    try:
        seller = Users.objects.get(email=email, role='seller')
        wallet_balance    = seller.wallet.balance
        free_balance      = seller.wallet.free_balance
        reserved_balance  = seller.wallet.reserved_balance
        pending_requests  = CashoutRequest.objects.filter(
            seller=seller,
            status__in=['pending', 'approved']
        ).order_by('-created_at')[:5]
    except Users.DoesNotExist:
        wallet_balance   = Decimal('0')
        free_balance     = Decimal('0')
        reserved_balance = Decimal('0')

    context = {
        'email': email,
        'platform_id': platform_id,
        'wallet_balance':   wallet_balance,
        'free_balance':     free_balance,
        'reserved_balance': reserved_balance,
        'pending_requests': pending_requests,
    }
    return render(request, 'seller_request_cashout.html', context)



@csrf_exempt
@xframe_options_exempt
def process_payment_items(request, request_id):
    """
    Per-item payment processing.

    Actions:
      'pay'     → buyer wallet → seller wallet  (immediate transfer)
      'deposit' → funds STAY in buyer wallet, reserved_balance += item.amount
                  balance is NOT reduced; seller sees funds are secured

    Flow:
      1. total_needed = sum of ALL item amounts
      2. shortfall    = max(0, total_needed - wallet.free_balance)
      3. If shortfall > 0: one mobile-money top-up (phone_number required)
      4. For each item:
           'pay'     → deduct balance, credit seller
           'deposit' → increment reserved_balance, balance unchanged

    POST (multipart/form-data):
        email        — buyer email
        pin          — 4-digit PIN
        item_actions — JSON: [{"item_id": 1, "action": "pay"|"deposit"}, ...]
        phone_number — required only when shortfall > 0

    Returns JSON:
        success, message, item_results, overall_status,
        wallet_balance, wallet_reserved, wallet_free
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=400)

    try:
        data             = request.POST
        email            = data.get('email')
        pin              = data.get('pin')
        item_actions_raw = data.get('item_actions', '[]')
        phone_number     = data.get('phone_number', '').strip()

        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN are required'}, status=400)

        try:
            item_actions = json.loads(item_actions_raw)
        except (json.JSONDecodeError, TypeError):
            return JsonResponse({'error': 'Invalid item_actions format'}, status=400)

        if not item_actions:
            return JsonResponse({'error': 'No item actions provided'}, status=400)

        # ── Payment request ───────────────────────────────────────────────
        try:
            payment_request = PaymentRequest.objects.get(request_id=request_id)
        except PaymentRequest.DoesNotExist:
            return JsonResponse({'error': 'Payment request not found'}, status=404)

        if email != payment_request.buyer_email:
            return JsonResponse({'error': 'Unauthorised'}, status=403)

        # ── Buyer ─────────────────────────────────────────────────────────
        try:
            buyer = Users.objects.get(email=email, role='buyer')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'Buyer not found'}, status=404)

        # ── PIN verification ──────────────────────────────────────────────
        pin_result = PINAuthenticator.verify_pin(buyer, pin)
        if not pin_result['valid']:
            resp = {'error': pin_result['error']}
            if pin_result.get('attempts_remaining') is not None:
                resp['attempts_remaining'] = pin_result['attempts_remaining']
            return JsonResponse(resp, status=401)

        # ── Build action map ──────────────────────────────────────────────
        action_map = {int(ia['item_id']): ia['action'] for ia in item_actions}

        request_items = {
            item.item_id: item for item in payment_request.items.all()
        }

        # ── Validate ──────────────────────────────────────────────────────
        valid_actions = {'pay', 'deposit'}
        for item_id, action in action_map.items():
            if action not in valid_actions:
                return JsonResponse(
                    {'error': f"Invalid action '{action}' for item {item_id}. Use 'pay' or 'deposit'."},
                    status=400
                )
            if item_id not in request_items:
                return JsonResponse(
                    {'error': f"Item {item_id} not found in this payment request"},
                    status=404
                )

        # ── STEP 1: Unified shortfall ─────────────────────────────────────
        total_all = sum(request_items[iid].amount for iid in action_map)

        buyer_wallet = buyer.wallet
        buyer_wallet.refresh_from_db()
        free_balance = buyer_wallet.free_balance   # balance - reserved_balance

        shortfall = max(Decimal('0'), total_all - free_balance)

        logger.info(
            f"💳 process_payment_items: {email} | "
            f"total={total_all} | free={free_balance} | shortfall={shortfall}"
        )

        # ── STEP 2: Top up if shortfall > 0 ──────────────────────────────
        if shortfall > Decimal('0'):
            if not phone_number:
                return JsonResponse({
                    'error':       'Mobile money number required for wallet top-up',
                    'shortfall':   str(shortfall),
                    'needs_topup': True,
                }, status=400)

            platform = payment_request.platform
            if not platform:
                return JsonResponse({'error': 'No payment platform configured'}, status=400)

            from .payment_processor import process_deposit, complete_pending_deposit

            logger.info(f"💰 Topping up {shortfall} UGX for {email}")

            dep_result = process_deposit(
                user=buyer,
                platform=platform,
                amount=shortfall,
                phone_number=phone_number,
            )

            if dep_result['status'] != 'success':
                logger.error(f"❌ Top-up failed: {dep_result.get('message')}")
                return JsonResponse({
                    'error':          dep_result.get('message', 'Top-up failed. Please try again.'),
                    'deposit_failed': True,
                }, status=400)

            comp = complete_pending_deposit(
                transaction_id=dep_result['transaction_id'],
                external_reference=dep_result['reference_id'],
            )

            if comp['status'] != 'success':
                return JsonResponse({
                    'error':          'Top-up could not be completed. Please try again.',
                    'deposit_failed': True,
                }, status=400)

            logger.info(f"✅ Top-up of {shortfall} UGX completed for {email}")
            buyer_wallet.refresh_from_db()

        # ── STEP 3: Process each item atomically ──────────────────────────
        item_results = []

        with db_transaction.atomic():
            buyer_wl = Wallet.objects.select_for_update().get(pk=buyer_wallet.pk)

            for item_id, action in action_map.items():
                item = request_items[item_id]
                try:
                    if action == 'pay':
                        # ── Immediate transfer: buyer → seller ────────────
                        # ── Escrow: buyer pays, funds held in seller's reserved balance ──
                        # Buyer's balance decreases immediately.
                        # Seller's balance increases (total) but reserved_balance also
                        # increases by the same amount, so seller's FREE balance is
                        # unchanged until the buyer confirms delivery or admin resolves
                        # the dispute "without refund".
                        if buyer_wl.free_balance < item.amount:
                            raise ValueError(
                                f"Insufficient free balance for item {item_id}. "
                                f"Required: {item.amount}, Free: {buyer_wl.free_balance}"
                            )

                        seller    = Users.objects.get(email=item.seller_email, role='seller')
                        seller_wl = Wallet.objects.select_for_update().get(pk=seller.wallet.pk)

                        txn = Transaction.objects.create(
                            platform=payment_request.platform,
                            from_wallet=buyer_wl,
                            to_wallet=seller_wl,
                            amount=item.amount,
                            transaction_type='transfer',
                            status='completed',
                            description=(
                                f'Escrow hold (awaiting delivery confirmation): '
                                f'{item.product_description or item.seller_email}'
                            ),
                        )

                        buyer_wl.balance           -= item.amount     # buyer pays
                        seller_wl.balance          += item.amount     # seller receives (total)
                        seller_wl.reserved_balance += item.amount     # but it's held (reserved)
                        buyer_wl.save(update_fields=['balance', 'updated_at'])
                        seller_wl.save(update_fields=['balance', 'reserved_balance', 'updated_at'])

                        # Mark item so release_seller_funds can find it later
                        item.transaction      = txn
                        item.is_escrowed     = True
                        item.escrowed_amount = item.amount
                        item.escrowed_at     = timezone.now()
                        item.save(update_fields=[
                            'transaction', 'is_escrowed',
                            'escrowed_amount', 'escrowed_at', 'updated_at',
                        ])

                        item_results.append({'item_id': item_id, 'status': 'escrowed', 'amount': str(item.amount)})
                        logger.info(f"  Item {item_id} → escrowed (held in seller escrow) ({item.amount})")

                    elif action == 'deposit':
                        # ── Reserve in wallet (balance stays, reserved grows) ──
                        if buyer_wl.free_balance < item.amount:
                            raise ValueError(
                                f"Insufficient free balance to reserve item {item_id}. "
                                f"Required: {item.amount}, Free: {buyer_wl.free_balance}"
                            )

                        buyer_wl.reserved_balance += item.amount
                        buyer_wl.save(update_fields=['reserved_balance', 'updated_at'])
                        # balance deliberately NOT changed

                        item.is_deposited     = True
                        item.deposited_amount = item.amount
                        item.deposited_at     = timezone.now()
                        item.save(update_fields=[
                            'is_deposited', 'deposited_amount', 'deposited_at', 'updated_at'
                        ])

                        item_results.append({'item_id': item_id, 'status': 'deposited', 'amount': str(item.amount)})
                        logger.info(f"  Item {item_id} → deposited/reserved ({item.amount})")

                except Exception as err:
                    logger.error(f"❌ Item {item_id} failed: {err}", exc_info=True)
                    item_results.append({'item_id': item_id, 'status': 'failed', 'amount': str(item.amount), 'error': str(err)})

            # ── Update PaymentRequest status ──────────────────────────────
            statuses = [r['status'] for r in item_results]
            unique   = set(statuses)
            if unique == {'escrowed'}:
                # All items paid and held in seller escrow
                overall_status         = 'escrowed'
                payment_request.status = 'escrowed'
            elif unique == {'deposited'}:
                # All items reserved in buyer wallet (old flow)
                overall_status         = 'deposited'
                payment_request.status = 'awaiting_payment'
            elif unique == {'failed'}:
                overall_status         = 'failed'
                payment_request.status = 'failed'
            elif 'failed' not in unique and unique <= {'escrowed', 'deposited'}:
                # Mix of escrow + deposit — all money is committed, none failed
                overall_status         = 'partial'
                payment_request.status = 'escrowed'
            else:
                # Anything else (includes failures mixed in)
                overall_status         = 'partial'
                payment_request.status = 'awaiting_payment'
            payment_request.save(update_fields=['status', 'updated_at'])

            buyer_wl.refresh_from_db()

        # ── STEP 4: Notify shopping app ───────────────────────────────────
        _notify_shopping_app(payment_request, item_results, overall_status)

        
        
        esc_count = sum(1 for r in item_results if r['status'] == 'escrowed')
        dep_count  = sum(1 for r in item_results if r['status'] == 'deposited')
        paid_count = sum(1 for r in item_results if r['status'] == 'paid')
        fail_count = sum(1 for r in item_results if r['status'] == 'failed')
        parts = []
        if esc_count:  parts.append(f"{esc_count} item(s) paid (held until delivery confirmed)")
        if dep_count:  parts.append(f"{dep_count} item(s) reserved in your wallet")
        if paid_count: parts.append(f"{paid_count} item(s) fully settled")
        if fail_count: parts.append(f"{fail_count} item(s) failed")

        logger.info(f"✅ process_payment_items complete for {email}: {overall_status}")

        return JsonResponse({
            'success':         True,
            'message':         '. '.join(parts) + '.' if parts else 'Done.',
            'item_results':    item_results,
            'overall_status':  overall_status,
            'wallet_balance':  str(buyer_wl.balance),
            'wallet_reserved': str(buyer_wl.reserved_balance),
            'wallet_free':     str(buyer_wl.free_balance),
        })

    except Exception as e:
        logger.error(f"❌ process_payment_items error: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Payment processing failed. Please try again.'}, status=500)


@csrf_exempt
@xframe_options_exempt
def release_seller_funds(request, request_id, shopping_order_item_id):
    """
    Release the seller's reserved (escrow) funds to their free balance.

    Triggers:
      (A) Shopping app sends POST after buyer confirms delivery.
      (B) Internally by resolve_dispute_with_sync when admin selects
          "resolve_without_refund" (body contains _internal=1).

    POST body (JSON or form-data):
        api_key   — platform API key  (required for shopping-app calls)
        _internal — "1"              (skip api_key check)

    Effect (atomic):
        seller_wallet.reserved_balance -= amount
        seller_wallet.balance is UNCHANGED  (credited during 'pay' action)
        → seller free_balance (balance - reserved) rises by amount

    Returns JSON:
        { success, amount_released, seller_email,
          seller_free_balance, seller_reserved_balance }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        if request.content_type and 'application/json' in request.content_type:
            import json as _json
            body = _json.loads(request.body)
        else:
            body = request.POST

        api_key  = body.get('api_key', '')
        internal = body.get('_internal', '') == '1'

        if not internal:
            try:
                Platform.objects.get(api_key=api_key, is_active=True)
            except Platform.DoesNotExist:
                return JsonResponse({'error': 'Invalid API key'}, status=403)

        try:
            payment_request = PaymentRequest.objects.get(request_id=request_id)
        except PaymentRequest.DoesNotExist:
            return JsonResponse({'error': 'Payment request not found'}, status=404)

        try:
            item = PaymentRequestItem.objects.get(
                payment_request=payment_request,
                shopping_order_item_id=shopping_order_item_id,
                is_escrowed=True,
            )
        except PaymentRequestItem.DoesNotExist:
            return JsonResponse(
                {'error': (
                    f'No escrowed item for order item {shopping_order_item_id}. '
                    'Already released or refunded.'
                )},
                status=404,
            )

        amount = item.escrowed_amount or item.amount

        with db_transaction.atomic():
            seller    = Users.objects.get(email=item.seller_email, role='seller')
            seller_wl = Wallet.objects.select_for_update().get(pk=seller.wallet.pk)

            if seller_wl.reserved_balance < amount:
                return JsonResponse(
                    {'error': 'Seller reserved balance mismatch — cannot release'},
                    status=400,
                )

            seller_wl.reserved_balance -= amount
            seller_wl.save(update_fields=['reserved_balance', 'updated_at'])

            item.is_escrowed = False
            item.is_cleared  = True
            item.cleared_at  = timezone.now()
            item.save(update_fields=[
                'is_escrowed', 'is_cleared', 'cleared_at', 'updated_at',
            ])

            still_escrowed = payment_request.items.filter(is_escrowed=True).exists()
            if not still_escrowed:
                payment_request.status = 'cleared'
                payment_request.save(update_fields=['status', 'updated_at'])

        logger.info(
            f"✅ Seller escrow released: {amount} UGX → {seller.email} "
            f"(shopping_order_item_id={shopping_order_item_id})"
        )

        # Notify shopping app: item is now fully paid (escrow released)
        _notify_shopping_app(
            payment_request,
            [{'item_id': item.item_id, 'status': 'paid', 'amount': str(amount)}],
            'paid',
        )

        return JsonResponse({
            'success':                 True,
            'amount_released':         str(amount),
            'seller_email':            seller.email,
            'seller_free_balance':     str(seller_wl.free_balance),
            'seller_reserved_balance': str(seller_wl.reserved_balance),
        })

    except Exception as e:
        logger.error(f"❌ release_seller_funds error: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Could not release funds'}, status=500)



@csrf_exempt
@xframe_options_exempt
def complete_deposit_by_order_item(request, request_id, shopping_order_item_id):
    """
    Shopping-app-facing endpoint: complete a pending deposit identified by
    shopping_order_item_id (the OrderItem.id on the shopping side).

    Transfers reserved funds: buyer wallet → seller wallet.
      wallet.balance          -= amount
      wallet.reserved_balance -= amount
      seller.wallet.balance   += amount
      (free_balance unchanged — was already reduced at deposit time)

    POST (multipart/form-data or JSON):
        email — buyer email
        pin   — 4-digit wallet PIN
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=400)

    try:
        # Support both form-data and JSON body
        if request.content_type and 'application/json' in request.content_type:
            import json as _json
            body = _json.loads(request.body)
            email = body.get('email')
            pin   = body.get('pin')
        else:
            email = request.POST.get('email')
            pin   = request.POST.get('pin')

        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN required'}, status=400)

        # ── Locate payment request ────────────────────────────────────────
        try:
            payment_request = PaymentRequest.objects.get(request_id=request_id)
        except PaymentRequest.DoesNotExist:
            return JsonResponse({'error': 'Payment request not found'}, status=404)

        if email != payment_request.buyer_email:
            return JsonResponse({'error': 'Unauthorised'}, status=403)

        # ── Verify buyer + PIN ────────────────────────────────────────────
        try:
            buyer = Users.objects.get(email=email, role='buyer')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'Buyer not found'}, status=404)

        pin_result = PINAuthenticator.verify_pin(buyer, pin)
        if not pin_result['valid']:
            resp = {'error': pin_result['error']}
            if pin_result.get('attempts_remaining') is not None:
                resp['attempts_remaining'] = pin_result['attempts_remaining']
            return JsonResponse(resp, status=401)

        # ── Find the deposited PaymentRequestItem by shopping_order_item_id ──
        try:
            item = PaymentRequestItem.objects.get(
                payment_request=payment_request,
                shopping_order_item_id=shopping_order_item_id,
                is_deposited=True,
            )
        except PaymentRequestItem.DoesNotExist:
            return JsonResponse(
                {'error': f'No deposited item found for order item {shopping_order_item_id}'},
                status=404,
            )

        amount = item.deposited_amount or item.amount

        # ── Transfer ──────────────────────────────────────────────────────
        with db_transaction.atomic():
            buyer_wl  = Wallet.objects.select_for_update().get(pk=buyer.wallet.pk)
            seller    = Users.objects.get(email=item.seller_email, role='seller')
            seller_wl = Wallet.objects.select_for_update().get(pk=seller.wallet.pk)

            if buyer_wl.balance < amount:
                return JsonResponse({'error': 'Insufficient wallet balance'}, status=400)
            if buyer_wl.reserved_balance < amount:
                return JsonResponse({'error': 'Reserved balance mismatch'}, status=400)

            txn = Transaction.objects.create(
                platform=payment_request.platform,
                from_wallet=buyer_wl,
                to_wallet=seller_wl,
                amount=amount,
                transaction_type='transfer',
                status='completed',
                description=f'Deposit completion: {item.product_description or item.seller_email}',
            )

            buyer_wl.balance          -= amount
            buyer_wl.reserved_balance -= amount
            seller_wl.balance         += amount
            buyer_wl.save(update_fields=['balance', 'reserved_balance', 'updated_at'])
            seller_wl.save(update_fields=['balance', 'updated_at'])

            item.transaction  = txn
            item.is_deposited = False
            item.save(update_fields=['transaction', 'is_deposited', 'updated_at'])

        # ── Notify shopping app ───────────────────────────────────────────
        _notify_shopping_app(
            payment_request,
            [{'item_id': item.item_id, 'status': 'paid', 'amount': str(amount)}],
            'paid',
        )

        logger.info(
            f"✅ Deposit completed via shopping-item endpoint: "
            f"shopping_order_item_id={shopping_order_item_id} for {email}"
        )

        return JsonResponse({
            'success':                True,
            'amount':                 str(amount),
            'shopping_order_item_id': shopping_order_item_id,
            'wallet_balance':         str(buyer_wl.balance),
            'wallet_reserved':        str(buyer_wl.reserved_balance),
            'wallet_free':            str(buyer_wl.free_balance),
        })

    except Exception as e:
        logger.error(f"❌ complete_deposit_by_order_item error: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Could not complete deposit'}, status=500)


@csrf_exempt
@xframe_options_exempt
def cancel_deposit_by_order_item(request, request_id, shopping_order_item_id):
    """
    Shopping-app-facing endpoint: cancel a pending deposit identified by
    shopping_order_item_id.

    Releases the reservation — nothing leaves the wallet.
      wallet.reserved_balance -= amount
      wallet.balance unchanged
    Shopping app webhook will set OrderItem.payment_status → 'pending'.

    POST (multipart/form-data or JSON):
        email — buyer email
        pin   — 4-digit wallet PIN
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=400)

    try:
        if request.content_type and 'application/json' in request.content_type:
            import json as _json
            body = _json.loads(request.body)
            email = body.get('email')
            pin   = body.get('pin')
        else:
            email = request.POST.get('email')
            pin   = request.POST.get('pin')

        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN required'}, status=400)

        try:
            payment_request = PaymentRequest.objects.get(request_id=request_id)
        except PaymentRequest.DoesNotExist:
            return JsonResponse({'error': 'Payment request not found'}, status=404)

        if email != payment_request.buyer_email:
            return JsonResponse({'error': 'Unauthorised'}, status=403)

        try:
            buyer = Users.objects.get(email=email, role='buyer')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'Buyer not found'}, status=404)

        pin_result = PINAuthenticator.verify_pin(buyer, pin)
        if not pin_result['valid']:
            resp = {'error': pin_result['error']}
            if pin_result.get('attempts_remaining') is not None:
                resp['attempts_remaining'] = pin_result['attempts_remaining']
            return JsonResponse(resp, status=401)

        try:
            item = PaymentRequestItem.objects.get(
                payment_request=payment_request,
                shopping_order_item_id=shopping_order_item_id,
                is_deposited=True,
            )
        except PaymentRequestItem.DoesNotExist:
            return JsonResponse(
                {'error': f'No deposited item found for order item {shopping_order_item_id}'},
                status=404,
            )

        amount = item.deposited_amount or item.amount

        with db_transaction.atomic():
            buyer_wl = Wallet.objects.select_for_update().get(pk=buyer.wallet.pk)

            if buyer_wl.reserved_balance < amount:
                return JsonResponse({'error': 'Reserved balance mismatch'}, status=400)

            buyer_wl.reserved_balance -= amount
            buyer_wl.save(update_fields=['reserved_balance', 'updated_at'])

            item.is_deposited     = False
            item.deposited_amount = None
            item.deposited_at     = None
            item.save(update_fields=[
                'is_deposited', 'deposited_amount', 'deposited_at', 'updated_at'
            ])

        _notify_shopping_app(
            payment_request,
            [{'item_id': item.item_id, 'status': 'pending', 'amount': str(amount)}],
            'partial',
        )

        logger.info(
            f"✅ Deposit cancelled via shopping-item endpoint: "
            f"shopping_order_item_id={shopping_order_item_id} for {email}"
        )

        return JsonResponse({
            'success':                True,
            'amount_freed':           str(amount),
            'shopping_order_item_id': shopping_order_item_id,
            'wallet_balance':         str(buyer_wl.balance),
            'wallet_reserved':        str(buyer_wl.reserved_balance),
            'wallet_free':            str(buyer_wl.free_balance),
        })

    except Exception as e:
        logger.error(f"❌ cancel_deposit_by_order_item error: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Could not cancel deposit'}, status=500)



# ═════════════════════════════════════════════════════════════════════════════
# _notify_shopping_app  (non-blocking webhook to shopping app)
# ═════════════════════════════════════════════════════════════════════════════

def _notify_shopping_app(payment_request, item_results, overall_status):
    """Send per-item status updates to the shopping app. Failure is non-fatal."""
    import requests as http_requests, os

    try:
        webhook_url = (
            # getattr(payment_request.platform, 'callback_url', None)
            # or (payment_request.metadata or {}).get('webhook_url')
            os.getenv('SHOPPING_APP_WEBHOOK_URL', 'http://localhost:8000/api/webhook/payment-status/')
        )
    except Exception:
        webhook_url = 'http://localhost:8000/api/webhook/payment-status/'

    items_by_id = {item.item_id: item for item in payment_request.items.all()}

    item_updates = []
    for result in item_results:
        item = items_by_id.get(result['item_id'])
        if not item or not item.shopping_order_item_id:
            if item:
                logger.warning(f"⚠️ Item {item.item_id} has no shopping_order_item_id - skipping webhook")
            continue
        item_updates.append({
            'shopping_order_item_id': item.shopping_order_item_id,
            'status':  result['status'],
            'amount':  result.get('amount', str(item.amount)),
        })

    if not item_updates:
        return

    try:
        resp = http_requests.post(
            webhook_url,
            json={'request_id': str(payment_request.request_id), 'item_updates': item_updates, 'overall_status': overall_status},
            headers={'Content-Type': 'application/json'},
            timeout=10,
        )
        logger.info(f"📤 Webhook → shopping app: HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"⚠️ Could not notify shopping app: {e}")
    """
    Send per-item status update to the shopping app via webhook.
    Non-blocking — failure does NOT break the payment flow.

    Payload shape:
    {
        "request_id": "uuid",
        "item_updates": [
            {"shopping_order_item_id": 42, "status": "paid",  "amount": "5000"},
            {"shopping_order_item_id": 43, "status": "deposited", "amount": "3000"},
        ],
        "overall_status": "paid" | "deposited" | "partial" | "failed"
    }
    """
    import requests as http_requests
    import os

    # Resolve webhook URL
    try:
        shopping_app_webhook = (
            # getattr(payment_request.platform, 'callback_url', None)
            # or (payment_request.metadata or {}).get('webhook_url')
            os.getenv('SHOPPING_APP_WEBHOOK_URL',
                         'http://localhost:8000/api/webhook/payment-status/')
        )
    except Exception:
        shopping_app_webhook = 'http://localhost:8000/api/webhook/payment-status/'

    # Build a lookup: PaymentRequestItem.item_id → item object (query once)
    items_by_id = {item.item_id: item for item in payment_request.items.all()}

    item_updates = []
    for result in item_results:
        item = items_by_id.get(result['item_id'])
        if item is None:
            continue

        # Only include entries that have a linked shopping OrderItem
        if not item.shopping_order_item_id:
            logger.warning(
                f"⚠️  PaymentRequestItem {item.item_id} has no shopping_order_item_id — "
                f"skipping webhook update for this item"
            )
            continue

        item_updates.append({
            'shopping_order_item_id': item.shopping_order_item_id,
            'status':  result['status'],
            'amount':  result.get('amount', str(item.amount)),
        })

    if not item_updates:
        logger.warning("⚠️  No items with shopping_order_item_id — skipping shopping app webhook")
        return

    payload = {
        'request_id':     str(payment_request.request_id),
        'item_updates':   item_updates,
        'overall_status': overall_status,
    }

    try:
        resp = http_requests.post(
            shopping_app_webhook,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=10,
        )
        logger.info(f"📤 Webhook → shopping app: HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"⚠️  Could not notify shopping app: {e}")




        