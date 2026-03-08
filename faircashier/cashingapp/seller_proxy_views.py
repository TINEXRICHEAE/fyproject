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
from .models import Users, Platform, Wallet
from django.views.decorators.http import require_GET
import hashlib
import time
import json
import logging

logger = logging.getLogger(__name__)
  

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


# ============= SELLER DASHBOARD IFRAME ENDPOINT =============

@csrf_exempt
@xframe_options_exempt
def seller_dashboard_iframe(request):
    """
    Seller dashboard accessible via iframe from e-commerce platform.

    Flow:
      GET (with platform params)  : Verify token → render pin_login.html or pin_setup.html
      POST (PIN auth or setup)    : Verify/create PIN → set session → redirect to dashboard
      GET (session-authenticated) : Load data → render authenticated dashboard
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

        # Auth successful — mark session and redirect to dashboard
        request.session[f'seller_dashboard_auth_{email}'] = True
        logger.info(f"✅ Seller dashboard session set for: {email}")

        return JsonResponse({
            'success': True,
            'redirect_url': f'/payment/seller-dashboard/?email={email}',
        })

    # ============= HANDLE GET =============
    if not seller_email:
        return render(request, 'error.html', {'message': 'Missing email parameter'})

    # ---- Session-authenticated dashboard ----
    if request.session.get(f'seller_dashboard_auth_{seller_email}'):
        try:
            seller = Users.objects.get(email=seller_email, role='seller')
        except Users.DoesNotExist:
            del request.session[f'seller_dashboard_auth_{seller_email}']
            return render(request, 'error.html', {'message': 'Seller account not found'})

        from .models import Transaction, PaymentRequestItem
        from django.db.models import Q, Sum
        from decimal import Decimal

        wallet = seller.wallet

        transactions = Transaction.objects.filter(
            Q(from_wallet=wallet) | Q(to_wallet=wallet)
        ).order_by('-created_at')[:10]

        items = PaymentRequestItem.objects.filter(
            seller_email=seller_email
        ).select_related('payment_request').order_by('-created_at')[:10]

        total_sales = Transaction.objects.filter(
            to_wallet=wallet,
            transaction_type='transfer',
            status='completed',
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        platform_id   = request.session.get(f'seller_platform_id_{seller_email}', '')
        platform_name = request.session.get(f'seller_platform_name_{seller_email}', '')

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
        })

    # ---- Initial access — verify platform token ----
    platform_key  = request.GET.get('platform_key', '').strip()
    access_token  = request.GET.get('token', '').strip()

    logger.info(f"📥 Seller dashboard access: {seller_email}")

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



