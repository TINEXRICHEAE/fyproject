# cashingapp/api_views.py (COMPLETE)

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Q
from .models import Users, Wallet, Platform, Transaction
from .pin_auth import PINAuthenticator
import json
import logging

logger = logging.getLogger(__name__)


# ============= SELLER VERIFICATION =============

@csrf_exempt
@require_http_methods(["POST"])
def check_sellers(request):
    """Check if sellers are registered with Fair Cashier"""
    try:
        data = json.loads(request.body)
        api_key = data.get('api_key')
        seller_emails = data.get('seller_emails', [])
        
        # Validate API key
        try:
            platform = Platform.objects.get(api_key=api_key, is_active=True)
        except Platform.DoesNotExist:
            return JsonResponse({'error': 'Invalid API key'}, status=401)
        
        # Check each seller
        results = {}
        for email in seller_emails:
            try:
                user = Users.objects.get(email=email, role='seller')
                wallet = Wallet.objects.filter(user=user).first()
                
                results[email] = {
                    'registered': True,
                    'has_wallet': wallet is not None,
                    'has_pin': bool(user.pin)
                }
            except Users.DoesNotExist:
                results[email] = {
                    'registered': False,
                    'has_wallet': False,
                    'has_pin': False
                }
        
        return JsonResponse({'results': results})
        
    except Exception as e:
        logger.error(f"❌ Check sellers error: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)


# ============= BUYER STATUS CHECK =============

@csrf_exempt
@require_http_methods(["GET", "POST"])
def check_buyer_status(request):
    """Check buyer registration and PIN setup status"""
    if request.method == 'GET':
        email = request.GET.get('email')
    else:
        data = json.loads(request.body)
        email = data.get('email')
    
    if not email:
        return JsonResponse({'error': 'Email required'}, status=400)
    
    try:
        user = Users.objects.get(email=email, role='buyer')
        
        is_locked = False
        if user.pin_locked_until:
            is_locked = user.pin_locked_until > timezone.now()
        
        return JsonResponse({
            'exists': True,
            'has_pin': bool(user.pin),
            'has_wallet': Wallet.objects.filter(user=user).exists(),
            'action': 'pin_login' if user.pin else 'pin_setup',
            'is_locked': is_locked
        })
    except Users.DoesNotExist:
        return JsonResponse({
            'exists': False,
            'has_pin': False,
            'has_wallet': False,
            'action': 'pin_setup',
            'is_locked': False
        })


# ============= PIN VERIFICATION API =============

@csrf_exempt
@require_http_methods(["POST"])
def verify_pin_api(request):
    """API endpoint to verify PIN"""
    try:
        data = json.loads(request.body)
        email = data.get('email')
        pin = data.get('pin')
        
        if not email or not pin:
            return JsonResponse({'error': 'Email and PIN required'}, status=400)
        
        try:
            user = Users.objects.get(email=email, role__in=['buyer', 'seller'])
        except Users.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
        
        result = PINAuthenticator.verify_pin(user, pin)
        
        if result['valid']:
            return JsonResponse({
                'valid': True,
                'email': user.email,
                'role': user.role,
                'wallet_balance': str(user.wallet.balance) if hasattr(user, 'wallet') else '0.00'
            })
        else:
            return JsonResponse({
                'valid': False,
                'error': result['error'],
                'attempts_remaining': result.get('attempts_remaining')
            }, status=401)
            
    except Exception as e:
        logger.error(f"❌ PIN verification error: {str(e)}")
        return JsonResponse({'error': 'Verification failed'}, status=500)


# ============= WALLET INFO API =============

@csrf_exempt
@require_http_methods(["POST"])
def get_wallet_info(request):
    """Get wallet information with PIN verification"""
    try:
        data = json.loads(request.body)
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
            return JsonResponse({
                'error': result['error'],
                'attempts_remaining': result.get('attempts_remaining')
            }, status=401)
        
        wallet = user.wallet
        
        recent_transactions = Transaction.objects.filter(
            Q(from_wallet=wallet) | Q(to_wallet=wallet)
        ).order_by('-created_at')[:5]
        
        return JsonResponse({
            'success': True,
            'wallet': {
                'balance': str(wallet.balance),
                'currency': wallet.currency
            },
            'user': {
                'email': user.email,
                'role': user.role,
                'phone_number': user.phone_number or ''
            },
            'recent_transactions': [
                {
                    'id': str(t.transaction_id),
                    'type': t.transaction_type,
                    'amount': str(t.amount),
                    'status': t.status,
                    'created_at': t.created_at.isoformat()
                }
                for t in recent_transactions
            ]
        })
        
    except Exception as e:
        logger.error(f"❌ Get wallet info error: {str(e)}")
        return JsonResponse({'error': 'Failed to get wallet info'}, status=500)


# ============= UPDATE PIN API =============

@csrf_exempt
@require_http_methods(["POST"])
def update_pin(request):
    """Update user's PIN"""
    try:
        data = json.loads(request.body)
        email = data.get('email')
        old_pin = data.get('old_pin')
        new_pin = data.get('new_pin')
        confirm_pin = data.get('confirm_pin')
        
        if not all([email, old_pin, new_pin, confirm_pin]):
            return JsonResponse({'error': 'All fields required'}, status=400)
        
        try:
            user = Users.objects.get(email=email, role__in=['buyer', 'seller'])
        except Users.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
        
        # Verify old PIN
        verify_result = PINAuthenticator.verify_pin(user, old_pin)
        
        if not verify_result['valid']:
            return JsonResponse({
                'error': 'Invalid current PIN',
                'attempts_remaining': verify_result.get('attempts_remaining')
            }, status=401)
        
        # Set new PIN
        result = PINAuthenticator.set_pin(user, new_pin, confirm_pin)
        
        if result['success']:
            logger.info(f"✅ PIN updated: {email}")
            return JsonResponse({'success': True, 'message': 'PIN updated successfully'})
        else:
            return JsonResponse({'error': result['error']}, status=400)
            
    except Exception as e:
        logger.error(f"❌ Update PIN error: {str(e)}")
        return JsonResponse({'error': 'Failed to update PIN'}, status=500)