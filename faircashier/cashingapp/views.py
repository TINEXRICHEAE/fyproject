# cashingapp/views.py (FIXED - Better token handling)

from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.clickjacking import xframe_options_exempt
from django.http import JsonResponse
from django.conf import settings
from .models import PaymentRequest, Users, Wallet
import hashlib
import time
import logging

logger = logging.getLogger(__name__)


# ============= CORE HELPER FUNCTIONS =============

def get_client_ip(request):
    """Get client IP address"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def generate_confirmation_token(request_id, email):
    """
    Generate a secure confirmation token
    
    Format: {hash}:{timestamp}
    Example: "a1b2c3d4e5f6g7h8:1738875234"
    """
    secret = settings.SECRET_KEY
    timestamp = str(int(time.time()))  # Current Unix timestamp
    
    # Create unique string to hash
    string = f"{request_id}:{email}:{timestamp}:{secret}"
    
    # Generate cryptographic hash (first 16 characters for brevity)
    token = hashlib.sha256(string.encode()).hexdigest()[:16]
    
    token_string = f"{token}:{timestamp}"
    
    logger.info(f"✅ Generated token for {email} - Timestamp: {timestamp}")
    
    return token_string


def verify_confirmation_token(request_id, email, token_string, max_age=600):
    """
    Verify confirmation token (valid for 10 minutes by default)
    
    Returns:
        bool: True if token is valid, False otherwise
    """
    # ✅ FIX: Handle empty/missing token without error log
    if not token_string or not token_string.strip():
        logger.debug(f"🔍 No token provided for {email} - showing confirmation page")
        return False
    
    try:
        logger.info(f"🔍 Verifying token for {email}: {token_string}")
        
        # Split token and timestamp
        parts = token_string.split(':')
        if len(parts) != 2:
            logger.warning(f"⚠️ Invalid token format for {email}: {token_string}")
            return False
            
        token, timestamp = parts
        
        # Validate timestamp
        try:
            token_time = int(timestamp)
        except ValueError:
            logger.warning(f"⚠️ Invalid timestamp in token for {email}")
            return False
        
        # Check if token has expired
        current_time = int(time.time())
        age = current_time - token_time
        
        logger.info(f"⏰ Token age: {age} seconds (max: {max_age})")
        
        if age > max_age:
            logger.warning(f"⚠️ Token expired for {email} (age: {age}s)")
            return False
        
        if age < -60:  # Token from future
            logger.warning(f"⚠️ Token from future for {email}")
            return False
        
        # Regenerate expected token
        secret = settings.SECRET_KEY
        string = f"{request_id}:{email}:{timestamp}:{secret}"
        expected_token = hashlib.sha256(string.encode()).hexdigest()[:16]
        
        # Compare tokens
        is_valid = token == expected_token
        
        if is_valid:
            logger.info(f"✅ Token verified successfully for {email}")
        else:
            logger.warning(f"⚠️ Token mismatch for {email}")
        
        return is_valid
        
    except Exception as e:
        logger.error(f"❌ Token verification error: {str(e)}", exc_info=True)
        return False


# ============= HOME/ROUTING =============

def home(request):
    """Home page - routes based on user role or shows landing"""
    if request.user.is_authenticated:
        if request.user.role == 'buyer':
            return redirect('buyer_dashboard')
        elif request.user.role == 'seller':
            return redirect('seller_dashboard')
        elif request.user.role == 'admin':
            return redirect('admin_dashboard')
        elif request.user.role == 'superadmin':
            return redirect('superadmin_dashboard')
    
    return render(request, 'home.html')


# ============= PAYMENT REQUEST CREATION API =============

@csrf_exempt
def create_payment_request(request):
    """API endpoint for e-commerce platforms to create payment requests."""
    import json
    from decimal import Decimal
    from django.db import transaction as db_transaction
    from .models import Platform, PaymentRequestItem

    try:
        data = json.loads(request.body)
        api_key      = data.get('api_key')
        buyer_email  = data.get('buyer_email')
        items        = data.get('items', [])
        metadata     = data.get('metadata', {})

        # Validate API key
        try:
            platform = Platform.objects.get(api_key=api_key, is_active=True)
        except Platform.DoesNotExist:
            return JsonResponse({'error': 'Invalid API key'}, status=401)

        total_amount = sum(Decimal(item['amount']) for item in items)

        with db_transaction.atomic():
            payment_request = PaymentRequest.objects.create(
                platform=platform,
                buyer_email=buyer_email,
                total_amount=total_amount,
                status='initiated',
                metadata=metadata,
            )

            for item in items:
                # Support both the NEW singular key and the OLD list key
                shopping_id = item.get('shopping_order_item_id')
                if shopping_id is None:
                    # Fallback: old format sent a list; take the first element
                    id_list = item.get('shopping_order_item_ids', [])
                    shopping_id = id_list[0] if id_list else None

                PaymentRequestItem.objects.create(
                    payment_request=payment_request,
                    seller_email=item['seller_email'],
                    amount=item['amount'],
                    currency=item.get('currency', 'UGX'),
                    product_description=item.get('description', ''),
                    shopping_order_item_id=shopping_id,   # 1:1 with OrderItem
                )

        logger.info(f"✅ Payment request created: {payment_request.request_id}")

        return JsonResponse({
            'message':     'Payment request created',
            'request_id':  str(payment_request.request_id),
            'payment_url': f'/payment/{payment_request.request_id}/',
            'total_amount': str(total_amount),
        }, status=201)

    except Exception as e:
        logger.error(f"❌ Payment request error: {str(e)}")
        return JsonResponse({'error': 'Failed to create payment request'}, status=500)


# ============= PAYMENT PAGE (CONFIRMATION/PIN FLOW) =============

@xframe_options_exempt
def payment_page(request, request_id):
    """
    Payment page with PIN-based authentication
    Shows confirmation first, then routes to PIN login/setup
    """
    try:
        payment_request = PaymentRequest.objects.get(request_id=request_id)
        
        # ✅ FIX: Check if payment has been confirmed via URL token
        confirmed_token = request.GET.get('confirmed', '').strip()
        
        # Only verify if token is present
        is_confirmed = False
        if confirmed_token:
            is_confirmed = verify_confirmation_token(
                str(request_id), 
                payment_request.buyer_email, 
                confirmed_token
            )
        else:
            # No token provided - this is the initial visit
            logger.debug(f"📄 Initial payment page access for {payment_request.buyer_email}")
        
        if not is_confirmed:
            # Show confirmation page first
            items = payment_request.items.all()
            return render(request, 'payment_confirm.html', {
                'payment_request': payment_request,
                'items': items,
            })
        
        # Token is valid - get user and show payment page
        try:
            user = Users.objects.get(email=payment_request.buyer_email, role='buyer')
            wallet = user.wallet
        except Users.DoesNotExist:
            # User doesn't exist - redirect to PIN setup
            logger.info(f"🆕 New buyer {payment_request.buyer_email} - redirecting to PIN setup")
            return redirect(f'/pin-setup/?email={payment_request.buyer_email}&return=/payment/{request_id}/')
        
        # Show PIN-protected payment page
        items = payment_request.items.all()
        
        context = {
            'payment_request': payment_request,
            'items': items,
            'wallet': wallet,
            'user': user,
        }
        
        logger.info(f"💳 Showing payment page for {user.email}")
        
        return render(request, 'payment_page_pin.html', context)
        
    except PaymentRequest.DoesNotExist:
        logger.error(f"❌ Payment request not found: {request_id}")
        return render(request, 'error.html', {
            'message': 'Payment request not found'
        })


# ============= WEBHOOK SIMULATION =============

@csrf_exempt
def simulate_webhook_completion(request, transaction_id):
    """Simulates webhook callback to complete deposit"""
    try:
        from .payment_processor import complete_pending_deposit
        
        result = complete_pending_deposit(
            transaction_id=transaction_id,
            external_reference=str(transaction_id)
        )
        
        return JsonResponse(result)
        
    except Exception as e:
        logger.error(f"❌ Webhook simulation error: {str(e)}")
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)