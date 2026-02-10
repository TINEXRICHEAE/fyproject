# cashingapp/seller_proxy_views.py
"""
Fair Cashier - Seller Access Proxy Views (FIXED)
Handles seller authentication and authorization from external e-commerce platforms
"""

from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.clickjacking import xframe_options_exempt
from django.http import JsonResponse
from django.conf import settings
from django.utils import timezone
from .models import Users, Platform
import hashlib
import time
import logging

logger = logging.getLogger(__name__)


# ============= HELPER FUNCTIONS =============

def verify_seller_access_token(platform_api_key, seller_email, token_string, max_age=3600):
    """
    Verify seller access token (valid for 1 hour by default)
    
    FIXED: Token format is hash(api_key:email:timestamp) - NO secret needed
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
        
        # ✅ FIX: Regenerate expected token using SAME logic as shopping app
        # No secret needed - just API key + email + timestamp
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
    Seller dashboard accessible via iframe from e-commerce platform
    
    GET: Show PIN entry form
    POST: Verify PIN and return dashboard data
    """
    
    # ============= HANDLE POST (PIN VERIFICATION) =============
    if request.method == 'POST':
        email = request.POST.get('email')
        pin = request.POST.get('pin')
        
        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN required'}, status=400)
        
        # Verify seller exists
        try:
            seller = Users.objects.get(email=email, role='seller')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'Seller not found'}, status=404)
        
        # Verify PIN
        from .pin_auth import PINAuthenticator
        from .models import Transaction, PaymentRequestItem
        from django.db.models import Q, Sum
        from decimal import Decimal
        
        pin_result = PINAuthenticator.verify_pin(seller, pin)
        
        if not pin_result['valid']:
            response_data = {'error': pin_result['error']}
            if pin_result['attempts_remaining'] is not None:
                response_data['attempts_remaining'] = pin_result['attempts_remaining']
            return JsonResponse(response_data, status=401)
        
        # Get wallet and data
        wallet = seller.wallet
        
        transactions = Transaction.objects.filter(
            Q(from_wallet=wallet) | Q(to_wallet=wallet)
        ).order_by('-created_at')[:10]
        
        items = PaymentRequestItem.objects.filter(
            seller_email=email
        ).select_related('payment_request').order_by('-created_at')[:10]
        
        total_sales = Transaction.objects.filter(
            to_wallet=wallet,
            transaction_type='transfer',
            status='completed'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        logger.info(f"✅ Seller dashboard data retrieved for {email}")
        
        return JsonResponse({
            'success': True,
            'wallet': {
                'balance': str(wallet.balance),
                'currency': wallet.currency
            },
            'total_sales': str(total_sales),
            'transactions': [
                {
                    'id': str(tx.transaction_id),
                    'type': tx.transaction_type,
                    'amount': str(tx.amount),
                    'status': tx.status,
                    'created_at': tx.created_at.isoformat()
                }
                for tx in transactions
            ],
            'items': [
                {
                    'id': item.item_id,
                    'amount': str(item.amount),
                    'description': item.product_description or 'Item',
                    'created_at': item.created_at.isoformat()
                }
                for item in items
            ]
        })
    
    # ============= HANDLE GET (SHOW PIN FORM) =============
    seller_email = request.GET.get('email', '').strip()
    platform_key = request.GET.get('platform_key', '').strip()
    access_token = request.GET.get('token', '').strip()
    
    logger.info(f"📥 Seller dashboard access: {seller_email}")
    
    if not all([seller_email, platform_key, access_token]):
        logger.warning("⚠️ Missing required parameters for seller dashboard access")
        return render(request, 'error.html', {
            'message': 'Missing required access parameters'
        })
    
    # Verify platform
    try:
        platform = Platform.objects.get(api_key=platform_key, is_active=True)
        logger.info(f"✅ Platform verified: {platform.platform_name}")
    except Platform.DoesNotExist:
        logger.error(f"❌ Invalid platform key: {platform_key}")
        return render(request, 'error.html', {
            'message': 'Invalid platform credentials'
        })
    
    # Verify access token
    token_result = verify_seller_access_token(platform_key, seller_email, access_token)
    
    if not token_result['valid']:
        logger.warning(f"⚠️ Invalid token for {seller_email}: {token_result['error']}")
        return render(request, 'error.html', {
            'message': f'Access denied: {token_result["error"]}'
        })
    
    logger.info(f"✅ Access token verified for {seller_email}")
    
    # Check if seller exists
    try:
        seller = Users.objects.get(email=seller_email, role='seller')
        has_account = True
        logger.info(f"✅ Existing seller found: {seller_email}")
    except Users.DoesNotExist:
        has_account = False
        logger.info(f"🆕 New seller from {platform.platform_name}: {seller_email}")
    
    context = {
        'email': seller_email,
        'platform_name': platform.platform_name,
        'has_account': has_account,
    }
    
    return render(request, 'seller_dashboard_pin.html', context)

# ============= SELLER DASHBOARD API (POST with PIN) =============

@csrf_exempt
@xframe_options_exempt
def seller_dashboard_data(request):
    """
    Get seller dashboard data after PIN verification
    
    POST body:
        - email: Seller's email
        - pin: 4-digit PIN
    
    Response:
        - wallet: {balance, currency}
        - total_sales: Decimal
        - transactions: List of recent transactions
        - items: List of payment request items
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=400)
    
    try:
        email = request.POST.get('email')
        pin = request.POST.get('pin')
        
        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN required'}, status=400)
        
        # Verify session context
        platform_context = request.session.get('seller_platform_context', {})
        
        if not platform_context or platform_context.get('seller_email') != email:
            return JsonResponse({'error': 'Session expired or invalid'}, status=403)
        
        # Check session age (max 1 hour)
        verified_at = platform_context.get('verified_at', 0)
        if int(time.time()) - verified_at > 3600:
            return JsonResponse({'error': 'Session expired'}, status=403)
        
        # Verify seller exists
        try:
            seller = Users.objects.get(email=email, role='seller')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'Seller not found'}, status=404)
        
        # Verify PIN
        from .pin_auth import PINAuthenticator
        
        pin_result = PINAuthenticator.verify_pin(seller, pin)
        
        if not pin_result['valid']:
            response_data = {'error': pin_result['error']}
            if pin_result['attempts_remaining'] is not None:
                response_data['attempts_remaining'] = pin_result['attempts_remaining']
            return JsonResponse(response_data, status=401)
        
        # Get wallet
        wallet = seller.wallet
        
        # Get transactions
        from .models import Transaction
        from django.db.models import Q, Sum
        
        transactions = Transaction.objects.filter(
            Q(from_wallet=wallet) | Q(to_wallet=wallet)
        ).order_by('-created_at')[:10]
        
        transactions_data = []
        for tx in transactions:
            transactions_data.append({
                'id': str(tx.transaction_id),
                'type': tx.transaction_type,
                'amount': str(tx.amount),
                'status': tx.status,
                'created_at': tx.created_at.isoformat()
            })
        
        # Get payment request items for this seller
        from .models import PaymentRequestItem
        
        items = PaymentRequestItem.objects.filter(
            seller_email=email
        ).select_related('payment_request').order_by('-created_at')[:10]
        
        items_data = []
        for item in items:
            items_data.append({
                'id': item.item_id,
                'amount': str(item.amount),
                'description': item.product_description,
                'is_cleared': item.is_cleared,
                'created_at': item.created_at.isoformat()
            })
        
        # Calculate total sales
        from decimal import Decimal
        total_sales = Transaction.objects.filter(
            to_wallet=wallet,
            transaction_type='transfer',
            status='completed'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        logger.info(f"✅ Seller dashboard data retrieved for {email}")
        
        return JsonResponse({
            'success': True,
            'wallet': {
                'balance': str(wallet.balance),
                'currency': wallet.currency
            },
            'total_sales': str(total_sales),
            'transactions': transactions_data,
            'items': items_data
        })
        
    except Exception as e:
        logger.error(f"❌ Error retrieving seller dashboard data: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Failed to retrieve data'}, status=500)


# ============= CASHOUT REQUEST TO PLATFORM ADMIN =============

@csrf_exempt
def request_cashout_to_admin(request):
    """
    Seller requests cashout through platform admin
    
    POST body:
        - email: Seller's email
        - pin: 4-digit PIN
        - amount: Cashout amount
        - phone_number: Mobile money number
    
    This creates a cashout request that notifies the platform admin
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=400)
    
    try:
        from decimal import Decimal
        from django.db import transaction as db_transaction
        from .models import Transaction, ActivityLog, Platform
        
        email = request.POST.get('email')
        pin = request.POST.get('pin')
        amount = Decimal(request.POST.get('amount'))
        phone_number = request.POST.get('phone_number')
        
        if not all([email, pin, amount, phone_number]):
            return JsonResponse({'error': 'All fields required'}, status=400)
        
        # Verify session context
        platform_context = request.session.get('seller_platform_context', {})
        
        if not platform_context or platform_context.get('seller_email') != email:
            return JsonResponse({'error': 'Session expired'}, status=403)
        
        # Get platform
        platform_id = platform_context.get('platform_id')
        platform = Platform.objects.get(platform_id=platform_id)
        
        # Verify seller
        try:
            seller = Users.objects.get(email=email, role='seller')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'Seller not found'}, status=404)
        
        # Verify PIN
        from .pin_auth import PINAuthenticator
        
        pin_result = PINAuthenticator.verify_pin(seller, pin)
        
        if not pin_result['valid']:
            response_data = {'error': pin_result['error']}
            if pin_result['attempts_remaining'] is not None:
                response_data['attempts_remaining'] = pin_result['attempts_remaining']
            return JsonResponse(response_data, status=401)
        
        # Validate amount
        wallet = seller.wallet
        
        if amount < Decimal('5000.00'):
            return JsonResponse({'error': 'Minimum cashout is 5,000 UGX'}, status=400)
        
        if wallet.balance < amount:
            return JsonResponse({
                'error': 'Insufficient balance',
                'available': str(wallet.balance)
            }, status=400)
        
        # Create pending cashout transaction
        with db_transaction.atomic():
            transaction = Transaction.objects.create(
                platform=platform,
                from_wallet=wallet,
                amount=amount,
                transaction_type='cashout',
                status='pending',
                description=f'Cashout request to {platform.platform_name} admin - {phone_number}'
            )
            
            # Log activity
            ActivityLog.objects.create(
                user=seller,
                platform=platform,
                action='cashout',
                description=f'Cashout request: {amount} UGX to {phone_number}',
                metadata={
                    'transaction_id': str(transaction.transaction_id),
                    'amount': str(amount),
                    'phone_number': phone_number,
                    'platform': platform.platform_name
                }
            )
        
        logger.info(f"✅ Cashout request created: {transaction.transaction_id} for {email}")
        
        return JsonResponse({
            'success': True,
            'message': 'Cashout request submitted to platform admin',
            'transaction_id': str(transaction.transaction_id),
            'status': 'pending_admin_approval'
        })
        
    except Exception as e:
        logger.error(f"❌ Cashout request error: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Failed to process request'}, status=500)