# cashingapp/seller_proxy_views.py
"""
Fair Cashier - Seller Access Proxy Views (CLEANED)
Handles seller authentication and authorization from external e-commerce platforms
"""

from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.clickjacking import xframe_options_exempt
from django.http import JsonResponse
from django.conf import settings
from django.utils import timezone
from django.db import transaction as db_transaction
from django.core import signing                        # ← NEW
from .models import Users, Platform, Wallet
from django.views.decorators.http import require_GET
import hashlib
import time
import json
import logging

logger = logging.getLogger(__name__)

# ── Signing config for the post-PIN dashboard token ──────────────
_DASH_TOKEN_SALT    = 'seller-dashboard-auth'
_DASH_TOKEN_MAX_AGE = 300          # 5 minutes


# ============= HELPER FUNCTIONS =============

def verify_seller_access_token(platform_api_key, seller_email, token_string, max_age=3600):
    """
    Verify seller access token (valid for 1 hour by default)
    
    Token format is hash(api_key:email:timestamp) - NO secret needed
    Both apps can generate the same hash using only shared info (API key)
    
    Args:
        platform_api_key: Platform's API key
        seller_email: Seller's email
        token_string: Token to verify (format: hash:timestamp)
        max_age: Maximum token age in seconds
    
    Returns:
        dict: {valid: bool, error: str or None}
    """
    if not token_string or not token_string.strip():
        return {'valid': False, 'error': 'No token provided'}
    
    try:
        parts = token_string.split(':')
        if len(parts) != 2:
            return {'valid': False, 'error': 'Invalid token format'}
        
        token, timestamp = parts
        
        # Validate timestamp
        try:
            token_time = int(timestamp)
        except ValueError:
            return {'valid': False, 'error': 'Invalid timestamp'}
        
        # Check expiration
        current_time = int(time.time())
        age = current_time - token_time
        
        if age > max_age:
            return {'valid': False, 'error': f'Token expired ({age}s old)'}
        
        if age < -60:
            return {'valid': False, 'error': 'Token from future'}
        
        # Regenerate expected token using SAME logic as shopping app
        string = f"{platform_api_key}:{seller_email}:{timestamp}"
        expected_token = hashlib.sha256(string.encode()).hexdigest()[:32]
        
        # Compare tokens
        if token != expected_token:
            logger.warning(f"⚠️ Token mismatch for {seller_email}")
            logger.debug(f"Received: {token}")
            logger.debug(f"Expected: {expected_token}")
            logger.debug(f"String: {string}")
            return {'valid': False, 'error': 'Token mismatch'}
        
        logger.info(f"✅ Token verified for {seller_email}")
        return {'valid': True, 'error': None}
        
    except Exception as e:
        logger.error(f"❌ Token verification error: {str(e)}", exc_info=True)
        return {'valid': False, 'error': 'Token verification failed'}


# ── NEW: verify the signed dash_token used after PIN auth ────────
def _verify_dash_token(token, expected_email):
    """
    Verify a Django-signed dashboard token (used for the post-PIN
    redirect inside cross-origin iframes where cookies are blocked).
    Returns True if the token is valid, unexpired, and matches the email.
    """
    try:
        payload = signing.loads(
            token,
            salt=_DASH_TOKEN_SALT,
            max_age=_DASH_TOKEN_MAX_AGE,
        )
        return (
            payload.get('email') == expected_email
            and payload.get('auth') is True
        )
    except (signing.BadSignature, signing.SignatureExpired):
        return False


# ── NEW: shared renderer for the authenticated dashboard ─────────
def _render_authenticated_dashboard(request, seller, seller_email):
    """
    Load wallet / transaction data and render the authenticated
    seller_dashboard_pin.html template.  Used by both the signed-token
    path and the session-cookie fallback path.
    """
    from .models import Transaction, PaymentRequestItem
    from django.db.models import Q, Sum
    from decimal import Decimal

    wallet = seller.wallet

    transactions = Transaction.objects.filter(
        Q(from_wallet=wallet) | Q(to_wallet=wallet)
    ).order_by('-created_at')[:10]

    items = PaymentRequestItem.objects.filter(
        seller_email=seller_email,
    ).select_related('payment_request').order_by('-created_at')[:10]

    total_sales = Transaction.objects.filter(
        to_wallet=wallet,
        transaction_type='transfer',
        status='completed',
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    platform_id   = request.session.get(f'seller_platform_id_{seller_email}', '')
    platform_name = request.session.get(f'seller_platform_name_{seller_email}', '')

    # Generate a signed token the template JS can attach to
    # subsequent AJAX calls (ZKP verify, etc.) so they also
    # work when third-party cookies are blocked in the iframe.
    ajax_token = signing.dumps(
        {'email': seller_email, 'auth': True},
        salt=_DASH_TOKEN_SALT,
    )

    initial_data = json.dumps({
        'wallet': {
            'balance': str(wallet.balance),
            'currency': wallet.currency,
        },
        'total_sales': str(total_sales),
        'transactions': [
            {
                'id': str(tx.transaction_id),
                'type': tx.transaction_type,
                'amount': str(tx.amount),
                'status': tx.status,
                'created_at': tx.created_at.isoformat(),
            }
            for tx in transactions
        ],
        'items': [
            {
                'id': item.item_id,
                'amount': str(item.amount),
                'description': item.product_description or 'Item',
                'created_at': item.created_at.isoformat(),
            }
            for item in items
        ],
    })

    logger.info(f"✅ Serving authenticated seller dashboard for: {seller_email}")

    return render(request, 'seller_dashboard_pin.html', {
        'email': seller_email,
        'platform_name': platform_name,
        'platform_id': platform_id,
        'pin_verified': True,
        'initial_data_json': initial_data,
        'ajax_token': ajax_token,           # ← NEW context var for JS
    })


# ============= SELLER DASHBOARD IFRAME ENDPOINT =============

@csrf_exempt
@xframe_options_exempt
def seller_dashboard_iframe(request):
    """
    Seller dashboard accessible via iframe from e-commerce platform.

    Authentication after PIN uses **signed URL tokens** so it works
    inside cross-origin iframes where third-party cookies are blocked.

    Flow:
      GET  (with platform params)  → verify_seller_access_token() → show PIN form
      POST (PIN auth or setup)     → verify/create PIN → return signed dash_token
      GET  (with dash_token)       → verify signed token → render dashboard
      GET  (session fallback)      → session cookie still works if cookies allowed
    """

    seller_email = request.GET.get('email', '').strip()

    # ============= HANDLE POST (PIN LOGIN OR ACCOUNT SETUP) =============
    if request.method == 'POST':
        from .pin_auth import PINAuthenticator
        from .models import Transaction, PaymentRequestItem
        from django.db.models import Q, Sum
        from decimal import Decimal

        email = request.POST.get('email', '').strip()
        pin = request.POST.get('pin', '')
        confirm_pin = request.POST.get('confirm_pin', '')

        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN required'}, status=400)

        is_setup = bool(confirm_pin)  # pin_setup.html always sends confirm_pin

        if is_setup:
            # ---- New seller account creation ----
            if Users.objects.filter(email=email).exists():
                return JsonResponse({'error': 'Account exists. Use PIN login.'}, status=400)

            phone_number = request.POST.get('phone_number', '')

            try:
                with db_transaction.atomic():
                    user = Users.objects.create(
                        email=email,
                        role='seller',          # always seller via this endpoint
                        phone_number=phone_number,
                        is_active=True,
                        is_staff=False,
                        is_superuser=False,
                    )
                    user.set_unusable_password()
                    user.save()

                    result = PINAuthenticator.set_pin(user, pin, confirm_pin)
                    if not result['success']:
                        user.delete()
                        return JsonResponse({'error': result['error']}, status=400)

                    Wallet.objects.create(user=user)
                    logger.info(f"✅ Seller account created via dashboard iframe: {email}")

            except Exception as e:
                logger.error(f"❌ Seller setup error: {str(e)}")
                return JsonResponse({'error': 'Registration failed'}, status=500)

        else:
            # ---- Existing seller PIN login ----
            try:
                seller = Users.objects.get(email=email, role='seller')
            except Users.DoesNotExist:
                return JsonResponse({'error': 'Seller not found'}, status=404)

            result = PINAuthenticator.verify_pin(seller, pin)
            if not result['valid']:
                response_data = {'error': result['error']}
                if result['attempts_remaining'] is not None:
                    response_data['attempts_remaining'] = result['attempts_remaining']
                return JsonResponse(response_data, status=401)

            logger.info(f"✅ PIN verified for {email}")

        # ── Auth successful ──────────────────────────────────────
        # Session fallback (works when cookies aren't blocked)
        request.session[f'seller_dashboard_auth_{email}'] = True
        logger.info(f"✅ Seller dashboard session set for: {email}")

        # ── NEW: signed token in redirect URL so the next GET works
        #    even when third-party cookies are blocked by the browser ──
        dash_token = signing.dumps(
            {'email': email, 'auth': True},
            salt=_DASH_TOKEN_SALT,
        )

        return JsonResponse({
            'success': True,
            'redirect_url': (
                f'/payment/seller-dashboard/'
                f'?email={email}'
                f'&dash_token={dash_token}'
            ),
        })

    # ============= HANDLE GET =============
    if not seller_email:
        return render(request, 'error.html', {'message': 'Missing email parameter'})

    logger.info(f"📥 Seller dashboard access: {seller_email}")

    # ── 1. NEW: Signed-token auth (iframe-safe, no cookies needed) ──
    dash_token = request.GET.get('dash_token', '').strip()
    if dash_token:
        if _verify_dash_token(dash_token, seller_email):
            try:
                seller = Users.objects.get(email=seller_email, role='seller')
                logger.info(f"✅ dash_token verified for {seller_email}")
                return _render_authenticated_dashboard(request, seller, seller_email)
            except Users.DoesNotExist:
                return render(request, 'error.html', {'message': 'Seller account not found'})
        else:
            logger.warning(f"⚠️ Invalid/expired dash_token for {seller_email}")
            return render(request, 'error.html', {
                'message': 'Session expired or invalid token. Please go back and try again.',
            })

    # ── 2. Session-cookie fallback (works when cookies are allowed) ──
    if request.session.get(f'seller_dashboard_auth_{seller_email}'):
        try:
            seller = Users.objects.get(email=seller_email, role='seller')
            logger.info(f"✅ Session auth verified for {seller_email}")
            return _render_authenticated_dashboard(request, seller, seller_email)
        except Users.DoesNotExist:
            del request.session[f'seller_dashboard_auth_{seller_email}']
            return render(request, 'error.html', {'message': 'Seller account not found'})

    # ── 3. Initial access — verify platform token → show PIN form ──
    #    Uses the EXISTING verify_seller_access_token() — unchanged.
    platform_key  = request.GET.get('platform_key', '').strip()
    access_token  = request.GET.get('token', '').strip()

    if not all([platform_key, access_token]):
        logger.warning("⚠️ Missing platform params and no active session")
        return render(request, 'error.html', {'message': 'Missing required access parameters'})

    try:
        platform = Platform.objects.get(api_key=platform_key, is_active=True)
        logger.info(f"✅ Platform verified: {platform.platform_name}")
    except Platform.DoesNotExist:
        logger.error(f"❌ Invalid platform key: {platform_key}")
        return render(request, 'error.html', {'message': 'Invalid platform credentials'})

    token_result = verify_seller_access_token(platform_key, seller_email, access_token)
    if not token_result['valid']:
        logger.warning(f"⚠️ Invalid token for {seller_email}: {token_result['error']}")
        return render(request, 'error.html', {'message': f'Access denied: {token_result["error"]}'})

    logger.info(f"✅ Access token verified for {seller_email}")

    # Cache platform info so it survives the PIN redirect
    request.session[f'seller_platform_id_{seller_email}']   = str(platform.platform_id)
    request.session[f'seller_platform_name_{seller_email}'] = platform.platform_name

    # Check if seller already has an account
    try:
        Users.objects.get(email=seller_email, role='seller')
        has_account = True
        logger.info(f"✅ Existing seller: {seller_email} — showing PIN login")
    except Users.DoesNotExist:
        has_account = False
        logger.info(f"🆕 New seller from {platform.platform_name}: {seller_email} — showing PIN setup")

    if has_account:
        return render(request, 'pin_login.html', {
            'prefill_email': seller_email,
            'return_url': '',
        })
    else:
        return render(request, 'pin_setup.html', {
            'prefill_email': seller_email,
            'prefill_role': 'seller',
            'return_url': '',
        })



