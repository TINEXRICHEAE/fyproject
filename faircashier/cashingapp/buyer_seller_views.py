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
from decimal import Decimal
import logging
import hashlib

from .models import (
    Users, Wallet, PaymentRequest, Transaction, 
    Platform, ActivityLog, PaymentRequestItem
)
from .pin_auth import PINAuthenticator
from .views import generate_confirmation_token

logger = logging.getLogger(__name__)


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
        
        # Process payment
        buyer_wallet = buyer.wallet
        
        if buyer_wallet.balance < payment_request.total_amount:
            return JsonResponse({
                'error': 'Insufficient balance',
                'required': str(payment_request.total_amount),
                'available': str(buyer_wallet.balance)
            }, status=400)
        
        with db_transaction.atomic():
            for item in payment_request.items.all():
                seller = Users.objects.get(email=item.seller_email, role='seller')
                seller_wallet = seller.wallet
                
                transaction = Transaction.objects.create(
                    platform=payment_request.platform,
                    from_wallet=buyer_wallet,
                    to_wallet=seller_wallet,
                    amount=item.amount,
                    transaction_type='transfer',
                    status='completed',
                    description=f'Payment: {item.product_description}'
                )
                
                buyer_wallet.balance -= item.amount
                seller_wallet.balance += item.amount
                
                buyer_wallet.save()
                seller_wallet.save()
                
                item.transaction = transaction
                item.save()
            
            payment_request.status = 'paid'
            payment_request.save()
        
        logger.info(f"✅ Payment processed: {request_id}")
        
        return JsonResponse({
            'success': True,
            'message': 'Payment successful',
            'request_id': str(request_id),
            'amount': str(payment_request.total_amount)
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


# ============= SELLER DASHBOARD =============

@csrf_exempt
def seller_dashboard(request):
    """Seller dashboard with PIN verification"""
    if request.method == 'POST':
        email = request.POST.get('email')
        pin = request.POST.get('pin')
        
        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN required'}, status=400)
        
        try:
            user = Users.objects.get(email=email, role='seller')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
        
        result = PINAuthenticator.verify_pin(user, pin)
        
        if not result['valid']:
            return JsonResponse({'error': result['error']}, status=401)
        
        wallet = user.wallet
        
        incoming_transactions = Transaction.objects.filter(
            to_wallet=wallet,
            transaction_type='transfer',
            status='completed'
        ).order_by('-created_at')[:10]
        
        total_sales = incoming_transactions.aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0.00')
        
        payment_items = PaymentRequestItem.objects.filter(
            seller_email=user.email
        ).select_related('payment_request').order_by('-created_at')[:10]
        
        return JsonResponse({
            'success': True,
            'wallet': {'balance': str(wallet.balance), 'currency': wallet.currency},
            'total_sales': str(total_sales),
            'transactions': [
                {
                    'id': str(t.transaction_id),
                    'amount': str(t.amount),
                    'created_at': t.created_at.isoformat()
                }
                for t in incoming_transactions
            ],
            'items': [
                {
                    'id': item.item_id,
                    'amount': str(item.amount),
                    'description': item.product_description
                }
                for item in payment_items
            ]
        })
    
    email = request.GET.get('email', '')
    return render(request, 'seller_dashboard_pin.html', {'email': email})


# ============= DEPOSIT (PIN-PROTECTED) =============

@csrf_exempt
def deposit_pin(request):
    """Deposit with PIN verification"""
    if request.method == 'POST':
        email = request.POST.get('email')
        pin = request.POST.get('pin')
        amount = Decimal(request.POST.get('amount'))
        phone_number = request.POST.get('phone_number')
        platform_id = request.POST.get('platform_id')
        
        # Verify PIN
        try:
            user = Users.objects.get(email=email, role__in=['buyer', 'seller'])
        except Users.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
        
        result = PINAuthenticator.verify_pin(user, pin)
        if not result['valid']:
            return JsonResponse({'error': result['error']}, status=401)
        
        # Duplicate protection
        request_fingerprint = hashlib.md5(
            f"{user.id}:{amount}:{phone_number}:{platform_id}".encode()
        ).hexdigest()
        
        cache_key = f"deposit_request:{request_fingerprint}"
        if cache.get(cache_key):
            return JsonResponse({
                'error': 'Please wait before submitting another deposit'
            }, status=429)
        
        # Validation
        if amount < 1000:
            return JsonResponse({'error': 'Minimum deposit is 1,000 UGX'}, status=400)
        
        try:
            platform = Platform.objects.get(platform_id=platform_id)
            cache.set(cache_key, True, 10)
            
            from .payment_processor import process_deposit
            result = process_deposit(
                user=user,
                platform=platform,
                amount=amount,
                phone_number=phone_number
            )
            
            if result['status'] == 'success':
                cache.set(cache_key, True, 30)
                logger.info(f"✅ Deposit: {email}, {amount} UGX")
                
                return JsonResponse({
                    'success': True,
                    'transaction_id': result['transaction_id'],
                    'reference_id': result['reference_id'],
                    'next_action': result['next_action']
                })
            else:
                cache.delete(cache_key)
                return JsonResponse({'error': result['message']}, status=400)
                
        except Platform.DoesNotExist:
            return JsonResponse({'error': 'Invalid platform'}, status=400)
        except Exception as e:
            cache.delete(cache_key)
            logger.error(f"❌ Deposit error: {str(e)}")
            return JsonResponse({'error': str(e)}, status=500)
    
    email = request.GET.get('email', '')
    platforms = Platform.objects.filter(is_active=True)
    return render(request, 'deposit_pin.html', {
        'email': email,
        'platforms': platforms
    })


# ============= CASHOUT (PIN-PROTECTED) =============

@csrf_exempt
def cashout_pin(request):
    """Cashout with PIN verification"""
    if request.method == 'POST':
        email = request.POST.get('email')
        pin = request.POST.get('pin')
        amount = Decimal(request.POST.get('amount'))
        phone_number = request.POST.get('phone_number')
        platform_id = request.POST.get('platform_id')
        
        # Verify PIN
        try:
            user = Users.objects.get(email=email, role__in=['buyer', 'seller'])
        except Users.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
        
        result = PINAuthenticator.verify_pin(user, pin)
        if not result['valid']:
            return JsonResponse({'error': result['error']}, status=401)
        
        wallet = user.wallet
        
        # Validation
        if amount < 5000:
            return JsonResponse({'error': 'Minimum cashout is 5,000 UGX'}, status=400)
        
        if wallet.balance < amount:
            return JsonResponse({
                'error': 'Insufficient balance',
                'available': str(wallet.balance)
            }, status=400)
        
        # Duplicate protection
        request_fingerprint = hashlib.md5(
            f"cashout:{user.id}:{amount}:{phone_number}:{platform_id}".encode()
        ).hexdigest()
        
        cache_key = f"cashout_request:{request_fingerprint}"
        if cache.get(cache_key):
            return JsonResponse({
                'error': 'Please wait before submitting another cashout'
            }, status=429)
        
        try:
            platform = Platform.objects.get(platform_id=platform_id)
            cache.set(cache_key, True, 10)
            
            from .payment_processor import process_cashout
            result = process_cashout(
                user=user,
                platform=platform,
                amount=amount,
                phone_number=phone_number
            )
            
            if result['status'] == 'success':
                cache.set(cache_key, True, 30)
                logger.info(f"✅ Cashout: {email}, {amount} UGX")
                
                return JsonResponse({
                    'success': True,
                    'transaction_id': result['transaction_id'],
                    'new_balance': result['new_balance']
                })
            else:
                cache.delete(cache_key)
                return JsonResponse({'error': result['message']}, status=400)
                
        except Platform.DoesNotExist:
            return JsonResponse({'error': 'Invalid platform'}, status=400)
        except Exception as e:
            cache.delete(cache_key)
            logger.error(f"❌ Cashout error: {str(e)}")
            return JsonResponse({'error': str(e)}, status=500)
    
    email = request.GET.get('email', '')
    platforms = Platform.objects.filter(is_active=True)
    return render(request, 'cashout_pin.html', {
        'email': email,
        'platforms': platforms
    })