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
import hmac as _hmac
import math
import uuid
from django.conf import settings
from .models import (
    Users, Wallet, PaymentRequest, Transaction, 
    Platform, ActivityLog, PaymentRequestItem, MobileMoneyTransaction, CashoutRequest
)
from .pin_auth import PINAuthenticator
from .views import generate_confirmation_token

logger = logging.getLogger(__name__)


# ── tuneable constants ────────────────────────────────────────────────────────
_IDEMPOTENCY_WINDOW_SECONDS = 30   # duplicate-request guard window
_IDEMPOTENCY_TTL_SECONDS    = 60   # cache-key TTL
_DEPOSIT_MIN_UGX  = Decimal("1000")
_CASHOUT_MIN_UGX  = Decimal("5000")
# ─────────────────────────────────────────────────────────────────────────────
 

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
 


# ─── Helper: does this deposit response need a PesaPal redirect? ──────────────

def _use_pesapal_redirect(api_response: dict) -> bool:
    """
    True when PesaPal returned a redirect_url that the buyer must visit
    to complete payment (always true for a live PesaPal collection).
    """
    return bool(api_response.get('redirect_url'))


# ─── Helper: unified top-up for deposit_and_pay / process_payment_items ───────

def _handle_pesapal_topup(buyer, platform, shortfall, phone_number) -> dict:
    """
    Attempt a wallet top-up of `shortfall` UGX via PesaPal.

    Returns one of:
      {'ok': True}
          — never returned for live PesaPal (payment is async)

      {'ok': False, 'error': '<message>'}
          — PesaPal API rejected the order

      {'ok': False, 'requires_redirect': True,
       'redirect_url': '…', 'iframe_url': '…',
       'merchant_reference': '…', 'order_tracking_id': '…',
       'transaction_id': '…', 'message': '…'}
          — buyer must approve on PesaPal; caller must return HTTP 202
            so the front-end can display the PesaPal iframe before
            proceeding to the escrow/pay step.
    """
    from .payment_processor import process_deposit

    dep_result = process_deposit(
        user=buyer,
        platform=platform,
        amount=shortfall,
        phone_number=phone_number,
    )

    if dep_result['status'] != 'success':
        return {'ok': False, 'error': dep_result.get('message', 'Top-up failed')}

    merchant_ref = dep_result.get('reference_id', '')
    return {
        'ok':                False,
        'requires_redirect': True,
        'redirect_url':      dep_result.get('redirect_url', ''),
        'iframe_url':        f'/pesapal/iframe/{merchant_ref}/',
        'merchant_reference': merchant_ref,
        'order_tracking_id':  dep_result.get('order_tracking_id', ''),
        'transaction_id':     dep_result.get('transaction_id', ''),
        'message':            'Complete payment on PesaPal to fund your wallet',
    }


# ─── deposit_pin ──────────────────────────────────────────

@csrf_exempt
@xframe_options_exempt
def deposit_pin(request):
    """
    POST /deposit-pin/

    Initiates a PesaPal collection for the requested amount.
    Always returns a redirect / iframe URL — wallet is credited later
    by the IPN handler or pesapal_callback view after PesaPal confirms.

    Success response (HTTP 200):
    {
        success:             true,
        requires_redirect:   true,
        redirect_url:        "<PesaPal payment page>",
        iframe_url:          "/pesapal/iframe/<merchant_ref>/",
        merchant_reference:  "<uuid>",
        order_tracking_id:   "<pesapal uuid>",
        transaction_id:      "<internal uuid>",
        message:             "Complete payment on PesaPal to fund your wallet",
        next_action:         "…"
    }

    Error responses (HTTP 4xx):
        { error: "<message>" }
    """
    if request.method == 'GET':
        return render(request, 'deposit_pin.html', {
            'email':     request.GET.get('email', ''),
            'platforms': Platform.objects.filter(is_active=True),
        })

    email       = (request.POST.get('email')       or '').strip()
    pin         = (request.POST.get('pin')          or '').strip()
    phone       = (request.POST.get('phone_number') or '').strip()
    platform_id = (request.POST.get('platform_id')  or '').strip()

    try:
        amount = Decimal(str(request.POST.get('amount', '')).strip())
    except (InvalidOperation, TypeError, ValueError):
        amount = None

    if not all([email, pin, phone, platform_id]):
        return JsonResponse(
            {'error': 'email, pin, phone_number, and platform_id are required'},
            status=400,
        )
    if not amount or amount <= 0:
        return JsonResponse({'error': 'Invalid amount'}, status=400)
    if amount < _DEPOSIT_MIN_UGX:
        return JsonResponse(
            {'error': f'Minimum deposit is {int(_DEPOSIT_MIN_UGX):,} UGX'},
            status=400,
        )

    try:
        user = Users.objects.get(email=email, role__in=['buyer', 'seller'])
    except Users.DoesNotExist:
        return JsonResponse({'error': 'Account not found'}, status=404)

    # PIN verification
    pin_result = PINAuthenticator.verify_pin(user, pin)
    if not pin_result['valid']:
        resp = {'error': pin_result['error']}
        if pin_result.get('attempts_remaining') is not None:
            resp['attempts_remaining'] = pin_result['attempts_remaining']
        return JsonResponse(resp, status=401)

    # Idempotency guard — prevent duplicate submissions in the same 30-second window
    bucket  = math.floor(timezone.now().timestamp() / _IDEMPOTENCY_WINDOW_SECONDS)
    raw_key = f'deposit:{user.id}:{amount}:{phone}:{platform_id}:{bucket}'
    idem_key = 'idem:deposit:' + _hmac.new(
        b'fc-idempotency-v2', raw_key.encode(), hashlib.sha256
    ).hexdigest()
    cached = cache.get(idem_key)
    if cached:
        logger.info('Duplicate deposit suppressed — user=%s', email)
        return JsonResponse(cached, status=200)

    try:
        platform = Platform.objects.get(platform_id=platform_id, is_active=True)
    except Platform.DoesNotExist:
        return JsonResponse({'error': 'Invalid or inactive platform'}, status=400)

    try:
        from .payment_processor import PaymentProcessor

        processor    = PaymentProcessor(api_key=platform.mobile_money_api_key)
        api_response = processor.request_collection(
            phone_number=phone,
            amount=float(amount),
            description='Deposit to Fair Cashier',
        )

        if api_response['status'] != 'success':
            logger.warning('PesaPal rejected deposit for %s: %s', email, api_response.get('message'))
            return JsonResponse(
                {'error': api_response.get('message', 'Deposit failed')},
                status=400,
            )

        # PesaPal accepted the order — create internal records
        with db_transaction.atomic():
            txn = Transaction.objects.create(
                platform=platform,
                to_wallet=user.wallet,
                amount=amount,
                transaction_type='deposit',
                status='processing',
                mobile_money_reference=api_response.get('reference_id'),
                description=f'Deposit via PesaPal from {phone}',
            )
            MobileMoneyTransaction.objects.create(
                platform=platform,
                transaction=txn,
                operation_type='collection',
                phone_number=phone,
                amount=amount,
                external_reference=api_response.get('reference_id', str(txn.transaction_id)),
                api_response=api_response,
                status='pending',
            )

        merchant_ref = api_response.get('reference_id', '')
        response_payload = {
            'success':            True,
            'requires_redirect':  True,
            'redirect_url':       api_response.get('redirect_url', ''),
            'iframe_url':         f'/pesapal/iframe/{merchant_ref}/',
            'merchant_reference': merchant_ref,
            'order_tracking_id':  api_response.get('order_tracking_id', ''),
            'transaction_id':     str(txn.transaction_id),
            'message':            'Complete payment on PesaPal to fund your wallet',
            'next_action':        api_response.get('next_action', ''),
        }

        # Cache so a duplicate POST in the idempotency window returns the same payload
        cache.set(idem_key, response_payload, _IDEMPOTENCY_TTL_SECONDS)

        logger.info('Deposit order submitted for %s — ref=%s', email, merchant_ref)
        return JsonResponse(response_payload, status=200)

    except Exception as exc:
        logger.exception('deposit_pin unexpected error — user=%s', email)
        return JsonResponse(
            {'error': 'An unexpected error occurred. Please try again.'},
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
 


# ─────────────────────────────────────────────────────────────────────────────
# deposit_and_pay
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@xframe_options_exempt
def deposit_and_pay(request, request_id):
    """
    Seamless deposit + escrow in one step.

    When the buyer has insufficient free balance this view initiates a
    PesaPal collection for the shortfall and returns a redirect/iframe URL
    (HTTP 202) so the front-end can display the PesaPal payment page.

    Once the buyer completes payment on PesaPal the IPN / callback handler
    credits the wallet.  The front-end then re-submits this endpoint (or
    the buyer's wallet will already be funded and the escrow step proceeds
    immediately on the next call).

    POST body (form-data):
        email        — buyer email
        pin          — 4-digit wallet PIN
        phone_number — mobile money number (required when top-up is needed)

    Responses
    ---------
    200  { success, message, request_id, amount, deposited }
         All items escrowed successfully.

    202  { requires_redirect, redirect_url, iframe_url,
           merchant_reference, message }
         Wallet top-up required — buyer must approve on PesaPal before
         the escrow step can run.  Front-end should display the iframe
         then re-submit once deposit_complete is received.

    400  { error, deposit_failed? }
    401  { error, attempts_remaining? }
    403  { error }
    404  { error }
    500  { error }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=400)

    try:
        email        = request.POST.get('email', '').strip()
        pin          = request.POST.get('pin', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()

        if not all([email, pin, phone_number]):
            return JsonResponse(
                {'error': 'Email, PIN, and phone number are required'},
                status=400,
            )

        # ── Resolve payment request ───────────────────────────────────────
        try:
            payment_request = PaymentRequest.objects.get(request_id=request_id)
        except PaymentRequest.DoesNotExist:
            return JsonResponse({'error': 'Payment request not found'}, status=404)

        if email != payment_request.buyer_email:
            return JsonResponse({'error': 'Unauthorized'}, status=403)

        # ── Resolve buyer ─────────────────────────────────────────────────
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

        # ── Shortfall check ───────────────────────────────────────────────
        buyer_wallet = buyer.wallet
        buyer_wallet.refresh_from_db()
        shortfall = payment_request.total_amount - buyer_wallet.free_balance

        if shortfall > Decimal('0'):
            platform = payment_request.platform
            if not platform:
                return JsonResponse(
                    {'error': 'No payment platform configured'},
                    status=400,
                )

            logger.info(
                '💰 deposit_and_pay: initiating top-up of %s UGX for %s '
                '(free=%s, needed=%s)',
                shortfall, email, buyer_wallet.free_balance,
                payment_request.total_amount,
            )

            topup = _handle_pesapal_topup(buyer, platform, shortfall, phone_number)

            if not topup['ok']:
                if topup.get('requires_redirect'):
                    # Buyer must complete PesaPal payment before escrow can run
                    return JsonResponse(
                        {
                            'requires_redirect':  True,
                            'redirect_url':       topup['redirect_url'],
                            'iframe_url':         topup['iframe_url'],
                            'merchant_reference': topup['merchant_reference'],
                            'message':            topup['message'],
                        },
                        status=202,
                    )
                logger.error('❌ Top-up failed for %s: %s', email, topup.get('error'))
                return JsonResponse(
                    {'error': topup.get('error', 'Deposit failed. Please try again.'),
                     'deposit_failed': True},
                    status=400,
                )

            # topup['ok'] is True only in the (future) synchronous path.
            # For live PesaPal this branch is never reached — the redirect
            # branch above always fires.  Kept for completeness.
            buyer_wallet.refresh_from_db()

        # ── Final balance guard ───────────────────────────────────────────
        if buyer_wallet.free_balance < payment_request.total_amount:
            return JsonResponse(
                {
                    'error':     'Insufficient balance',
                    'required':  str(payment_request.total_amount),
                    'available': str(buyer_wallet.free_balance),
                },
                status=400,
            )

        # ── Escrow: buyer wallet → seller reserved balance ────────────────
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

                item.transaction     = txn
                item.is_escrowed     = True
                item.escrowed_amount = item.amount
                item.escrowed_at     = timezone.now()
                item.save(update_fields=[
                    'transaction', 'is_escrowed',
                    'escrowed_amount', 'escrowed_at', 'updated_at',
                ])

            payment_request.status = 'escrowed'
            payment_request.save()

        logger.info('✅ deposit_and_pay escrowed for %s: request=%s', email, request_id)

        return JsonResponse(
            {
                'success':   True,
                'message':   'Payment successful — funds held until delivery confirmed',
                'request_id': str(request_id),
                'amount':    str(payment_request.total_amount),
                'deposited': str(max(shortfall, Decimal('0'))),
            },
            status=200,
        )

    except PaymentRequest.DoesNotExist:
        return JsonResponse({'error': 'Payment request not found'}, status=404)
    except Exception as exc:
        logger.error('❌ deposit_and_pay error: %s', exc, exc_info=True)
        return JsonResponse({'error': 'Payment failed. Please try again.'}, status=500)


# ─────────────────────────────────────────────────────────────────────────────
# process_payment_items
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@xframe_options_exempt
def process_payment_items(request, request_id):
    """
    Per-item payment processing.

    Actions
    -------
    'pay'     → buyer wallet → seller wallet (escrow: funds held in
                seller's reserved_balance until delivery confirmed)
    'deposit' → funds stay in buyer wallet; buyer's reserved_balance
                grows by item.amount so those funds cannot be spent
                elsewhere; seller can see the commitment is secured

    Flow
    ----
    1. total_needed = sum of ALL item amounts
    2. shortfall    = max(0, total_needed - wallet.free_balance)
    3. If shortfall > 0:
           → _handle_pesapal_topup() → PesaPal SubmitOrderRequest
           → returns HTTP 202 { requires_redirect, iframe_url, … }
             so the front-end can display the PesaPal iframe
           → on deposit_complete postMessage the front-end re-submits
             (wallet is now funded; shortfall = 0 on the second call)
    4. For each item inside a single atomic block:
           'pay'     → buyer.balance -= amount
                       seller.balance += amount
                       seller.reserved_balance += amount
           'deposit' → buyer.reserved_balance += amount
                       (balance unchanged)

    POST (multipart/form-data)
    --------------------------
    email        — buyer email
    pin          — 4-digit wallet PIN
    item_actions — JSON array: [{"item_id": 1, "action": "pay"|"deposit"}, …]
    phone_number — required only when a top-up is needed

    Responses
    ---------
    200  { success, message, item_results, overall_status,
           wallet_balance, wallet_reserved, wallet_free }

    202  { requires_redirect, redirect_url, iframe_url,
           merchant_reference, message, needs_topup }
         Wallet top-up required — display PesaPal iframe, re-submit on
         deposit_complete.

    400 / 401 / 403 / 404 / 500  { error, … }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=400)

    try:
        email            = request.POST.get('email', '').strip()
        pin              = request.POST.get('pin', '').strip()
        item_actions_raw = request.POST.get('item_actions', '[]')
        phone_number     = request.POST.get('phone_number', '').strip()

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

        # ── Build action map & validate ───────────────────────────────────
        action_map    = {int(ia['item_id']): ia['action'] for ia in item_actions}
        request_items = {item.item_id: item for item in payment_request.items.all()}
        valid_actions = {'pay', 'deposit'}

        for item_id, action in action_map.items():
            if action not in valid_actions:
                return JsonResponse(
                    {'error': f"Invalid action '{action}' for item {item_id}. Use 'pay' or 'deposit'."},
                    status=400,
                )
            if item_id not in request_items:
                return JsonResponse(
                    {'error': f'Item {item_id} not found in this payment request'},
                    status=404,
                )

        # ── STEP 1: Shortfall calculation ─────────────────────────────────
        total_all = sum(request_items[iid].amount for iid in action_map)

        buyer_wallet = buyer.wallet
        buyer_wallet.refresh_from_db()
        free_balance = buyer_wallet.free_balance  # balance - reserved_balance

        shortfall = max(Decimal('0'), total_all - free_balance)

        logger.info(
            '💳 process_payment_items: %s | total=%s | free=%s | shortfall=%s',
            email, total_all, free_balance, shortfall,
        )

        # ── STEP 2: Top-up via PesaPal if shortfall > 0 ──────────────────
        if shortfall > Decimal('0'):
            if not phone_number:
                return JsonResponse(
                    {
                        'error':       'Mobile money number required for wallet top-up',
                        'shortfall':   str(shortfall),
                        'needs_topup': True,
                    },
                    status=400,
                )

            platform = payment_request.platform
            if not platform:
                return JsonResponse({'error': 'No payment platform configured'}, status=400)

            logger.info('💰 process_payment_items: topping up %s UGX for %s', shortfall, email)

            topup = _handle_pesapal_topup(buyer, platform, shortfall, phone_number)

            if not topup['ok']:
                if topup.get('requires_redirect'):
                    # Return 202 so the front-end shows the PesaPal iframe.
                    # Once the buyer approves and deposit_complete fires,
                    # the JS re-submits this endpoint; by then the wallet is
                    # funded and shortfall = 0.
                    return JsonResponse(
                        {
                            'requires_redirect':  True,
                            'redirect_url':       topup['redirect_url'],
                            'iframe_url':         topup['iframe_url'],
                            'merchant_reference': topup['merchant_reference'],
                            'message':            topup['message'],
                            'needs_topup':        True,
                        },
                        status=202,
                    )
                logger.error('❌ Top-up failed for %s: %s', email, topup.get('error'))
                return JsonResponse(
                    {'error': topup.get('error', 'Top-up failed. Please try again.'),
                     'deposit_failed': True},
                    status=400,
                )

            # Synchronous path (future): top-up already completed.
            buyer_wallet.refresh_from_db()

        # ── STEP 3: Process each item atomically ──────────────────────────
        item_results = []

        with db_transaction.atomic():
            buyer_wl = Wallet.objects.select_for_update().get(pk=buyer_wallet.pk)

            for item_id, action in action_map.items():
                item = request_items[item_id]
                try:
                    if action == 'pay':
                        # Escrow: buyer pays → seller receives but funds are
                        # held in seller.reserved_balance until delivery
                        # confirmed (release_seller_funds) or dispute resolved.
                        if buyer_wl.free_balance < item.amount:
                            raise ValueError(
                                f'Insufficient free balance for item {item_id}. '
                                f'Required: {item.amount}, Free: {buyer_wl.free_balance}'
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

                        buyer_wl.balance           -= item.amount
                        seller_wl.balance          += item.amount
                        seller_wl.reserved_balance += item.amount
                        buyer_wl.save(update_fields=['balance', 'updated_at'])
                        seller_wl.save(update_fields=['balance', 'reserved_balance', 'updated_at'])

                        item.transaction     = txn
                        item.is_escrowed     = True
                        item.escrowed_amount = item.amount
                        item.escrowed_at     = timezone.now()
                        item.save(update_fields=[
                            'transaction', 'is_escrowed',
                            'escrowed_amount', 'escrowed_at', 'updated_at',
                        ])

                        item_results.append({
                            'item_id': item_id,
                            'status':  'escrowed',
                            'amount':  str(item.amount),
                        })
                        logger.info('  Item %s → escrowed (%s UGX)', item_id, item.amount)

                    elif action == 'deposit':
                        # Reserve in buyer wallet: balance unchanged,
                        # reserved_balance grows → free_balance shrinks.
                        if buyer_wl.free_balance < item.amount:
                            raise ValueError(
                                f'Insufficient free balance to reserve item {item_id}. '
                                f'Required: {item.amount}, Free: {buyer_wl.free_balance}'
                            )

                        buyer_wl.reserved_balance += item.amount
                        buyer_wl.save(update_fields=['reserved_balance', 'updated_at'])

                        item.is_deposited     = True
                        item.deposited_amount = item.amount
                        item.deposited_at     = timezone.now()
                        item.save(update_fields=[
                            'is_deposited', 'deposited_amount', 'deposited_at', 'updated_at',
                        ])

                        item_results.append({
                            'item_id': item_id,
                            'status':  'deposited',
                            'amount':  str(item.amount),
                        })
                        logger.info('  Item %s → deposited/reserved (%s UGX)', item_id, item.amount)

                except Exception as err:
                    logger.error('❌ Item %s failed: %s', item_id, err, exc_info=True)
                    item_results.append({
                        'item_id': item_id,
                        'status':  'failed',
                        'amount':  str(item.amount),
                        'error':   str(err),
                    })

            # ── Update PaymentRequest status ──────────────────────────────
            unique = set(r['status'] for r in item_results)

            if unique == {'escrowed'}:
                overall_status         = 'escrowed'
                payment_request.status = 'escrowed'
            elif unique == {'deposited'}:
                overall_status         = 'deposited'
                payment_request.status = 'awaiting_payment'
            elif unique == {'failed'}:
                overall_status         = 'failed'
                payment_request.status = 'failed'
            elif 'failed' not in unique and unique <= {'escrowed', 'deposited'}:
                overall_status         = 'partial'
                payment_request.status = 'escrowed'
            else:
                overall_status         = 'partial'
                payment_request.status = 'awaiting_payment'

            payment_request.save(update_fields=['status', 'updated_at'])
            buyer_wl.refresh_from_db()

        # ── STEP 4: Notify shopping app ───────────────────────────────────
        _notify_shopping_app(payment_request, item_results, overall_status)

        # ── Build summary message ─────────────────────────────────────────
        esc_count  = sum(1 for r in item_results if r['status'] == 'escrowed')
        dep_count  = sum(1 for r in item_results if r['status'] == 'deposited')
        paid_count = sum(1 for r in item_results if r['status'] == 'paid')
        fail_count = sum(1 for r in item_results if r['status'] == 'failed')
        parts = []
        if esc_count:  parts.append(f'{esc_count} item(s) paid (held until delivery confirmed)')
        if dep_count:  parts.append(f'{dep_count} item(s) reserved in your wallet')
        if paid_count: parts.append(f'{paid_count} item(s) fully settled')
        if fail_count: parts.append(f'{fail_count} item(s) failed')

        logger.info('✅ process_payment_items complete for %s: %s', email, overall_status)

        return JsonResponse(
            {
                'success':         True,
                'message':         '. '.join(parts) + '.' if parts else 'Done.',
                'item_results':    item_results,
                'overall_status':  overall_status,
                'wallet_balance':  str(buyer_wl.balance),
                'wallet_reserved': str(buyer_wl.reserved_balance),
                'wallet_free':     str(buyer_wl.free_balance),
            },
            status=200,
        )

    except Exception as exc:
        logger.error('❌ process_payment_items error: %s', exc, exc_info=True)
        return JsonResponse(
            {'error': 'Payment processing failed. Please try again.'},
            status=500,
        )


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




        