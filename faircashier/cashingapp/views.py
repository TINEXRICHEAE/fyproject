from django.db.models import Q, Sum
from django.utils import timezone
from .models import (
    Users, Group, Platform, Wallet, Transaction, PaymentRequest,
    PaymentRequestItem, Dispute, MobileMoneyTransaction, ActivityLog
)
import json
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import logging
from django.http import JsonResponse
from decimal import Decimal
import uuid
from django.db import transaction as db_transaction

from django.core.cache import cache
import hashlib


logger = logging.getLogger(__name__)


# ============= AUTHENTICATION VIEWS =============

def home(request):
    """Home page - shows different dashboard based on user role"""
    if request.user.is_authenticated:
        if request.user.role == 'buyer':
            return redirect('buyer-dashboard')
        elif request.user.role == 'seller':
            return redirect('seller-dashboard')
        elif request.user.role == 'admin':
            return redirect('admin-dashboard')
        elif request.user.role == 'superadmin':
            return redirect('superadmin-dashboard')
    return render(request, 'home.html')


def register_user(request):
    """Register a new user"""
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        phone_number = request.POST.get('phone_number')
        role = request.POST.get('role', 'buyer')
        register_as_admin = request.POST.get('register_as_admin') == 'on'
        
        role = 'admin' if register_as_admin else role

        if not email or not password:
            return JsonResponse({'error': 'Email and password are required'}, status=400)

        if Users.objects.filter(email=email).exists():
            return JsonResponse({'error': 'Email already registered'}, status=400)

        try:
            with db_transaction.atomic():
                user = Users.objects.create_user(
                    email=email,
                    password=password,
                    role=role,
                    phone_number=phone_number,
                    is_staff=(role == 'admin' or role == 'superadmin'),
                    is_superuser=(role == 'superadmin'),
                )
                
                # Create wallet for the user
                Wallet.objects.create(user=user)
                
                # Log activity
                ActivityLog.objects.create(
                    user=user,
                    action='register',
                    description=f'User registered with role: {role}',
                    ip_address=get_client_ip(request)
                )
            
            return JsonResponse({
                'message': 'User registered successfully',
                'user_id': user.id,
                'email': user.email,
                'role': user.role
            }, status=201)
        except Exception as e:
            logger.error(f"Registration error: {str(e)}")
            return JsonResponse({'error': 'Registration failed'}, status=500)

    return render(request, 'register_user.html')


def login_user(request):
    """Login user"""
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')

        if not email or not password:
            return JsonResponse({'error': 'Email and password are required'}, status=400)

        user = authenticate(request, email=email, password=password)
        if user is not None:
            login(request, user)
            
            # Log activity
            ActivityLog.objects.create(
                user=user,
                action='login',
                description='User logged in',
                ip_address=get_client_ip(request)
            )
            
            return JsonResponse({
                'message': 'Login successful',
                'user_id': user.id,
                'email': user.email,
                'role': user.role,
                'redirect_url': get_dashboard_url(user.role)
            }, status=200)
        else:
            return JsonResponse({'error': 'Invalid email or password'}, status=401)

    return render(request, 'login_user.html')


def logout_user(request):
    """Logout user"""
    if request.method == 'POST':
        if request.user.is_authenticated:
            ActivityLog.objects.create(
                user=request.user,
                action='logout',
                description='User logged out',
                ip_address=get_client_ip(request)
            )
        logout(request)
        return JsonResponse({'message': 'Logout successful'}, status=200)
    return JsonResponse({'error': 'Invalid request method'}, status=400)


def check_auth(request):
    """Check if user is authenticated"""
    return JsonResponse({
        'is_authenticated': request.user.is_authenticated,
        'user': {
            'email': request.user.email,
            'role': request.user.role
        } if request.user.is_authenticated else None
    })


# ============= DASHBOARD VIEWS =============

@login_required
def buyer_dashboard(request):
    """Buyer dashboard"""
    if request.user.role != 'buyer':
        return redirect('home')
    
    wallet = request.user.wallet
    recent_transactions = Transaction.objects.filter(
        Q(from_wallet=wallet) | Q(to_wallet=wallet)
    )[:10]
    
    payment_requests = PaymentRequest.objects.filter(
        buyer_email=request.user.email
    )[:10]
    
    disputes = Dispute.objects.filter(buyer=request.user)[:10]
    
    context = {
        'wallet': wallet,
        'recent_transactions': recent_transactions,
        'payment_requests': payment_requests,
        'disputes': disputes,
    }
    return render(request, 'buyer_dashboard.html', context)


@login_required
def seller_dashboard(request):
    """Seller dashboard"""
    if request.user.role != 'seller':
        return redirect('home')
    
    wallet = request.user.wallet
    
    # Get incoming transactions (sales)
    incoming_transactions = Transaction.objects.filter(
        to_wallet=wallet,
        transaction_type='transfer',
        status='completed'
    )
    
    # Calculate total sales
    total_sales = incoming_transactions.aggregate(
        total=Sum('amount')
    )['total'] or Decimal('0.00')
    
    # Get payment items for this seller
    payment_items = PaymentRequestItem.objects.filter(
        seller_email=request.user.email
    ).select_related('payment_request')[:10]
    
    # Get disputes
    disputes = Dispute.objects.filter(seller=request.user)[:10]
    
    context = {
        'wallet': wallet,
        'total_sales': total_sales,
        'recent_transactions': incoming_transactions[:10],
        'payment_items': payment_items,
        'disputes': disputes,
    }
    return render(request, 'seller_dashboard.html', context)


@login_required
def admin_dashboard(request):
    """Admin dashboard for platform owners"""
    if request.user.role != 'admin':
        return redirect('home')
    
    platforms = Platform.objects.filter(admin=request.user)
    
    # Get statistics
    total_transactions = Transaction.objects.filter(
        platform__in=platforms
    ).count()
    
    total_volume = Transaction.objects.filter(
        platform__in=platforms,
        status='completed'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    recent_payments = PaymentRequest.objects.filter(
        platform__in=platforms
    )[:10]
    
    disputes = Dispute.objects.filter(
        payment_request_item__payment_request__platform__in=platforms,
        status__in=['open', 'escalated']
    )
    
    context = {
        'platforms': platforms,
        'total_transactions': total_transactions,
        'total_volume': total_volume,
        'recent_payments': recent_payments,
        'disputes': disputes,
    }
    return render(request, 'admin_dashboard.html', context)


@login_required
def superadmin_dashboard(request):
    """Super admin dashboard"""
    if request.user.role != 'superadmin':
        return redirect('home')
    
    # System-wide statistics
    total_users = Users.objects.count()
    total_platforms = Platform.objects.count()
    total_wallets = Wallet.objects.count()
    total_transactions = Transaction.objects.count()
    
    total_volume = Transaction.objects.filter(
        status='completed'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    recent_platforms = Platform.objects.all()[:10]
    recent_users = Users.objects.all()[:10]
    recent_disputes = Dispute.objects.all()[:10]
    
    context = {
        'total_users': total_users,
        'total_platforms': total_platforms,
        'total_wallets': total_wallets,
        'total_transactions': total_transactions,
        'total_volume': total_volume,
        'recent_platforms': recent_platforms,
        'recent_users': recent_users,
        'recent_disputes': recent_disputes,
    }
    return render(request, 'superadmin_dashboard.html', context)


# ============= WALLET VIEWS =============

@login_required
def wallet_view(request):
    """View wallet details"""
    wallet = request.user.wallet
    transactions = Transaction.objects.filter(
        Q(from_wallet=wallet) | Q(to_wallet=wallet)
    )
    
    context = {
        'wallet': wallet,
        'transactions': transactions,
    }
    return render(request, 'wallet.html', context)



@login_required
def deposit(request):
    """
    Initiate deposit from mobile money with hybrid duplicate protection
    
    Protection layers:
    1. Cache-based check (prevents rapid double-clicks)
    2. Database check (catches duplicates if cache was cleared)
    3. Request fingerprinting (unique identification)
    """
    if request.method == 'POST':
        amount = Decimal(request.POST.get('amount'))
        phone_number = request.POST.get('phone_number')
        platform_id = request.POST.get('platform_id')
        
        # Create unique fingerprint for this specific deposit attempt
        request_fingerprint = hashlib.md5(
            f"{request.user.id}:{amount}:{phone_number}:{platform_id}".encode()
        ).hexdigest()
        
        # === LAYER 1: Fast cache-based check (prevents rapid double-clicks) ===
        cache_key = f"deposit_request:{request_fingerprint}"
        if cache.get(cache_key):
            logger.warning(
                f"[CACHE DUPLICATE] Deposit blocked: User={request.user.email}, "
                f"Amount={amount} UGX, Phone={phone_number}, "
                f"Fingerprint={request_fingerprint[:8]}..."
            )
            return JsonResponse({
                'error': 'Please wait before submitting another deposit.',
                'error_code': 'RATE_LIMIT',
                'retry_after': 10  # seconds
            }, status=429)
        
        # Validation
        if amount <= 0:
            logger.info(f"Deposit validation failed: Invalid amount for {request.user.email}")
            return JsonResponse({'error': 'Invalid amount'}, status=400)
        
        if amount < 1000:
            logger.info(f"Deposit validation failed: Amount below minimum for {request.user.email}")
            return JsonResponse({'error': 'Minimum deposit is 1,000 UGX'}, status=400)
        
        try:
            platform = Platform.objects.get(platform_id=platform_id)
            wallet = request.user.wallet
            
            # === LAYER 2: Database check (catches duplicates if cache was cleared) ===
            recent_cutoff = timezone.now() - timezone.timedelta(seconds=60)
            duplicate_transaction = Transaction.objects.filter(
                to_wallet=wallet,
                amount=amount,
                transaction_type='deposit',
                status__in=['pending', 'processing'],
                created_at__gte=recent_cutoff
            ).first()
            
            if duplicate_transaction:
                logger.warning(
                    f"[DB DUPLICATE] Duplicate transaction detected: "
                    f"User={request.user.email}, Amount={amount} UGX, "
                    f"Existing TxID={duplicate_transaction.transaction_id}, "
                    f"Status={duplicate_transaction.status}"
                )
                return JsonResponse({
                    'error': 'A similar deposit is already being processed. Please wait.',
                    'error_code': 'DUPLICATE_TRANSACTION',
                    'existing_transaction_id': str(duplicate_transaction.transaction_id),
                    'transaction_status': duplicate_transaction.status
                }, status=429)
            
            # === LAYER 3: Set cache before processing ===
            cache.set(cache_key, True, 10)
            logger.info(
                f"[DEPOSIT START] User={request.user.email}, Amount={amount} UGX, "
                f"Phone={phone_number}, Platform={platform.platform_name}, "
                f"Fingerprint={request_fingerprint}"
            )
            
            # Process deposit
            from .payment_processor import process_deposit
            result = process_deposit(
                user=request.user,
                platform=platform,
                amount=amount,
                phone_number=phone_number
            )
            
            # Log activity
            ActivityLog.objects.create(
                user=request.user,
                platform=platform,
                action='deposit',
                description=f'Deposit initiated: {amount} UGX via {platform.get_mobile_money_provider_display()}',
                ip_address=get_client_ip(request),
                metadata={
                    'fingerprint': request_fingerprint,
                    'phone_number': phone_number,
                    'amount': str(amount),
                    'platform_id': platform_id
                }
            )
            
            if result['status'] == 'success':
                # Extend cache on success to prevent retries during processing
                cache.set(cache_key, True, 30)
                
                logger.info(
                    f"[DEPOSIT SUCCESS] User={request.user.email}, "
                    f"TxID={result['transaction_id']}, "
                    f"RefID={result['reference_id']}, "
                    f"Provider={result['provider']}"
                )
                
                # Store deposit data in session for processing page
                request.session['deposit_data'] = {
                    'amount': str(amount),
                    'phone_number': phone_number,
                    'transaction_id': result['transaction_id'],
                    'reference_id': result['reference_id'],
                    'provider': result['provider'],
                    'next_action': result['next_action']
                }
                
                return JsonResponse({
                    'status': 'success',
                    'redirect_url': '/deposit/processing/'
                }, status=200)
            else:
                # Clear cache on failure so user can retry immediately
                cache.delete(cache_key)
                
                logger.error(
                    f"[DEPOSIT FAILED] User={request.user.email}, "
                    f"Error={result['message']}, "
                    f"ErrorCode={result.get('error_code')}"
                )
                
                return JsonResponse({
                    'status': 'error',
                    'error': result['message'],
                    'error_code': result.get('error_code')
                }, status=400)
            
        except Platform.DoesNotExist:
            cache.delete(cache_key)
            logger.error(f"[DEPOSIT ERROR] Invalid platform_id={platform_id} for user {request.user.email}")
            return JsonResponse({'error': 'Invalid platform'}, status=400)
        except Exception as e:
            cache.delete(cache_key)
            logger.error(
                f"[DEPOSIT EXCEPTION] User={request.user.email}, "
                f"Error={str(e)}, Type={type(e).__name__}",
                exc_info=True
            )
            return JsonResponse({'error': 'Deposit failed: ' + str(e)}, status=500)
    
    # GET request - show deposit form
    platforms = Platform.objects.filter(is_active=True)
    wallet = request.user.wallet
    
    logger.debug(f"[DEPOSIT FORM] Loaded for user {request.user.email}")
    
    return render(request, 'deposit.html', {'platforms': platforms, 'wallet': wallet})



@login_required
def deposit_processing(request):
    """Display deposit processing page"""
    deposit_data = request.session.get('deposit_data')
    
    if not deposit_data:
        messages.error(request, 'No deposit in progress')
        return redirect('deposit')
    
    # Clear session data after retrieving
    del request.session['deposit_data']
    
    context = {
        'amount': deposit_data['amount'],
        'phone_number': deposit_data['phone_number'],
        'transaction_id': deposit_data['transaction_id'],
        'reference_id': deposit_data['reference_id'],
        'provider': deposit_data['provider'],
        'next_action': deposit_data['next_action']
    }
    
    return render(request, 'deposit_processing.html', context)


@login_required
def cashout(request):
    """
    Initiate cashout to mobile money with hybrid duplicate protection
    
    Protection layers:
    1. Cache-based check (prevents rapid double-clicks)
    2. Database check (catches duplicates if cache was cleared)
    3. Balance verification (prevents overdraft)
    """
    if request.method == 'POST':
        amount = Decimal(request.POST.get('amount'))
        phone_number = request.POST.get('phone_number')
        platform_id = request.POST.get('platform_id')
        
        wallet = request.user.wallet
        
        # Create unique fingerprint for this specific cashout attempt
        request_fingerprint = hashlib.md5(
            f"cashout:{request.user.id}:{amount}:{phone_number}:{platform_id}".encode()
        ).hexdigest()
        
        # === LAYER 1: Fast cache-based check (prevents rapid double-clicks) ===
        cache_key = f"cashout_request:{request_fingerprint}"
        if cache.get(cache_key):
            logger.warning(
                f"[CACHE DUPLICATE] Cashout blocked: User={request.user.email}, "
                f"Amount={amount} UGX, Phone={phone_number}, "
                f"Fingerprint={request_fingerprint[:8]}..."
            )
            return JsonResponse({
                'error': 'Please wait before submitting another cashout.',
                'error_code': 'RATE_LIMIT',
                'retry_after': 10  # seconds
            }, status=429)
        
        # Validation
        if amount <= 0:
            logger.info(f"Cashout validation failed: Invalid amount for {request.user.email}")
            return JsonResponse({'error': 'Invalid amount'}, status=400)
        
        if amount < 5000:
            logger.info(f"Cashout validation failed: Amount below minimum for {request.user.email}")
            return JsonResponse({'error': 'Minimum cashout is 5,000 UGX'}, status=400)
        
        if wallet.balance < amount:
            logger.warning(
                f"Cashout validation failed: Insufficient balance for {request.user.email}, "
                f"Required={amount}, Available={wallet.balance}"
            )
            return JsonResponse({
                'error': 'Insufficient balance',
                'available_balance': str(wallet.balance),
                'required_amount': str(amount)
            }, status=400)
        
        try:
            platform = Platform.objects.get(platform_id=platform_id)
            
            # === LAYER 2: Database check (catches duplicates if cache was cleared) ===
            recent_cutoff = timezone.now() - timezone.timedelta(seconds=60)
            duplicate_transaction = Transaction.objects.filter(
                from_wallet=wallet,
                amount=amount,
                transaction_type='cashout',
                status__in=['pending', 'processing'],
                created_at__gte=recent_cutoff
            ).first()
            
            if duplicate_transaction:
                logger.warning(
                    f"[DB DUPLICATE] Duplicate cashout detected: "
                    f"User={request.user.email}, Amount={amount} UGX, "
                    f"Existing TxID={duplicate_transaction.transaction_id}, "
                    f"Status={duplicate_transaction.status}"
                )
                return JsonResponse({
                    'error': 'A similar cashout is already being processed. Please wait.',
                    'error_code': 'DUPLICATE_TRANSACTION',
                    'existing_transaction_id': str(duplicate_transaction.transaction_id),
                    'transaction_status': duplicate_transaction.status
                }, status=429)
            
            # === LAYER 3: Set cache before processing ===
            cache.set(cache_key, True, 10)
            logger.info(
                f"[CASHOUT START] User={request.user.email}, Amount={amount} UGX, "
                f"Phone={phone_number}, Platform={platform.platform_name}, "
                f"WalletBalance={wallet.balance}, Fingerprint={request_fingerprint}"
            )
            
            # Use payment processor
            from .payment_processor import process_cashout
            result = process_cashout(
                user=request.user,
                platform=platform,
                amount=amount,
                phone_number=phone_number
            )
            
            # Log activity
            ActivityLog.objects.create(
                user=request.user,
                platform=platform,
                action='cashout',
                description=f'Cashout initiated: {amount} UGX via {platform.get_mobile_money_provider_display()}',
                ip_address=get_client_ip(request),
                metadata={
                    'fingerprint': request_fingerprint,
                    'phone_number': phone_number,
                    'amount': str(amount),
                    'platform_id': platform_id,
                    'wallet_balance_before': str(wallet.balance)
                }
            )
            
            if result['status'] == 'success':
                # Extend cache on success to prevent retries
                cache.set(cache_key, True, 30)
                
                logger.info(
                    f"[CASHOUT SUCCESS] User={request.user.email}, "
                    f"TxID={result['transaction_id']}, "
                    f"RefID={result['reference_id']}, "
                    f"NewBalance={result['new_balance']}, "
                    f"Provider={result['provider']}"
                )
                
                return JsonResponse({
                    'status': 'success',
                    'message': result['message'],
                    'transaction_id': result['transaction_id'],
                    'reference_id': result['reference_id'],
                    'confirmation': result.get('confirmation'),
                    'new_balance': result['new_balance'],
                    'provider': result['provider']
                }, status=200)
            else:
                # Clear cache on failure so user can retry immediately
                cache.delete(cache_key)
                
                logger.error(
                    f"[CASHOUT FAILED] User={request.user.email}, "
                    f"Error={result['message']}, "
                    f"ErrorCode={result.get('error_code')}"
                )
                
                return JsonResponse({
                    'status': 'error',
                    'error': result['message'],
                    'error_code': result.get('error_code')
                }, status=400)
            
        except Platform.DoesNotExist:
            cache.delete(cache_key)
            logger.error(f"[CASHOUT ERROR] Invalid platform_id={platform_id} for user {request.user.email}")
            return JsonResponse({'error': 'Invalid platform'}, status=400)
        except Exception as e:
            cache.delete(cache_key)
            logger.error(
                f"[CASHOUT EXCEPTION] User={request.user.email}, "
                f"Error={str(e)}, Type={type(e).__name__}",
                exc_info=True
            )
            return JsonResponse({'error': 'Cashout failed: ' + str(e)}, status=500)
    
    # GET request - show cashout form
    platforms = Platform.objects.filter(is_active=True)
    wallet = request.user.wallet
    
    logger.debug(f"[CASHOUT FORM] Loaded for user {request.user.email}, Balance={wallet.balance}")
    
    return render(request, 'cashout.html', {'platforms': platforms, 'wallet': wallet})


# ============= PAYMENT REQUEST VIEWS =============

@csrf_exempt
@require_http_methods(["POST"])
def create_payment_request(request):
    """API endpoint for RP platforms to create payment requests"""
    try:
        data = json.loads(request.body)
        api_key = data.get('api_key')
        buyer_email = data.get('buyer_email')
        items = data.get('items', [])
        metadata = data.get('metadata', {})
        
        # Validate API key
        try:
            platform = Platform.objects.get(api_key=api_key, is_active=True)
        except Platform.DoesNotExist:
            return JsonResponse({'error': 'Invalid API key'}, status=401)
        
        # Validate buyer
        try:
            buyer = Users.objects.get(email=buyer_email, role='buyer')
        except Users.DoesNotExist:
            return JsonResponse({'error': 'Buyer not found'}, status=404)
        
        # Calculate total
        total_amount = sum(Decimal(item['amount']) for item in items)
        
        with db_transaction.atomic():
            # Create payment request
            payment_request = PaymentRequest.objects.create(
                platform=platform,
                buyer_email=buyer_email,
                total_amount=total_amount,
                status='initiated',
                metadata=metadata
            )
            
            # Create payment request items
            for item in items:
                PaymentRequestItem.objects.create(
                    payment_request=payment_request,
                    seller_email=item['seller_email'],
                    amount=Decimal(item['amount']),
                    product_description=item.get('description', '')
                )
        
        return JsonResponse({
            'message': 'Payment request created',
            'request_id': str(payment_request.request_id),
            'payment_url': f'/payment/{payment_request.request_id}/',
            'total_amount': str(total_amount)
        }, status=201)
        
    except Exception as e:
        logger.error(f"Payment request error: {str(e)}")
        return JsonResponse({'error': 'Failed to create payment request'}, status=500)


@login_required
def payment_page(request, request_id):
    """Payment page shown in iframe"""
    try:
        payment_request = PaymentRequest.objects.get(request_id=request_id)
        
        if request.user.email != payment_request.buyer_email:
            return JsonResponse({'error': 'Unauthorized'}, status=403)
        
        items = payment_request.items.all()
        wallet = request.user.wallet
        
        context = {
            'payment_request': payment_request,
            'items': items,
            'wallet': wallet,
        }
        return render(request, 'payment_page.html', context)
        
    except PaymentRequest.DoesNotExist:
        return JsonResponse({'error': 'Payment request not found'}, status=404)


@login_required
def process_payment(request, request_id):
    """Process payment from buyer to sellers"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=400)
    
    try:
        payment_request = PaymentRequest.objects.get(request_id=request_id)
        
        if request.user.email != payment_request.buyer_email:
            return JsonResponse({'error': 'Unauthorized'}, status=403)
        
        buyer_wallet = request.user.wallet
        
        if buyer_wallet.balance < payment_request.total_amount:
            return JsonResponse({'error': 'Insufficient balance'}, status=400)
        
        with db_transaction.atomic():
            # Process each item
            for item in payment_request.items.all():
                # Get seller
                seller = Users.objects.get(email=item.seller_email, role='seller')
                seller_wallet = seller.wallet
                
                # Create transfer transaction
                transaction = Transaction.objects.create(
                    platform=payment_request.platform,
                    from_wallet=buyer_wallet,
                    to_wallet=seller_wallet,
                    amount=item.amount,
                    transaction_type='transfer',
                    status='completed',
                    description=f'Payment for: {item.product_description}'
                )
                
                # Update wallets
                buyer_wallet.balance -= item.amount
                seller_wallet.balance += item.amount
                
                buyer_wallet.save()
                seller_wallet.save()
                
                # Link transaction to item
                item.transaction = transaction
                item.save()
            
            # Update payment request status
            payment_request.status = 'paid'
            payment_request.save()
            
            ActivityLog.objects.create(
                user=request.user,
                platform=payment_request.platform,
                action='transfer',
                description=f'Payment processed: {payment_request.total_amount} UGX',
                ip_address=get_client_ip(request)
            )
        
        return JsonResponse({
            'message': 'Payment successful',
            'request_id': str(payment_request.request_id),
            'return_url': payment_request.platform.return_url
        }, status=200)
        
    except Exception as e:
        logger.error(f"Payment processing error: {str(e)}")
        return JsonResponse({'error': 'Payment failed'}, status=500)


@login_required
def clear_payment_item(request, item_id):
    """Buyer confirms delivery and clears seller to own funds"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=400)
    
    try:
        item = PaymentRequestItem.objects.get(item_id=item_id)
        
        if request.user.email != item.payment_request.buyer_email:
            return JsonResponse({'error': 'Unauthorized'}, status=403)
        
        if item.is_cleared:
            return JsonResponse({'error': 'Already cleared'}, status=400)
        
        item.is_cleared = True
        item.cleared_at = timezone.now()
        item.save()
        
        # Check if all items are cleared
        all_cleared = all(
            i.is_cleared for i in item.payment_request.items.all()
        )
        
        if all_cleared:
            item.payment_request.status = 'cleared'
            item.payment_request.save()
        
        return JsonResponse({'message': 'Payment item cleared'}, status=200)
        
    except PaymentRequestItem.DoesNotExist:
        return JsonResponse({'error': 'Item not found'}, status=404)
    except Exception as e:
        logger.error(f"Clear payment error: {str(e)}")
        return JsonResponse({'error': 'Failed to clear payment'}, status=500)


# ============= DISPUTE VIEWS =============

@login_required
def file_dispute(request):
    """File a dispute"""
    if request.method == 'POST':
        item_id = request.POST.get('item_id')
        reason = request.POST.get('reason')
        description = request.POST.get('description')
        
        try:
            item = PaymentRequestItem.objects.get(item_id=item_id)
            
            if request.user.email != item.payment_request.buyer_email:
                return JsonResponse({'error': 'Unauthorized'}, status=403)
            
            if item.is_cleared:
                return JsonResponse({'error': 'Cannot dispute cleared items'}, status=400)
            
            seller = Users.objects.get(email=item.seller_email)
            
            with db_transaction.atomic():
                dispute = Dispute.objects.create(
                    payment_request_item=item,
                    buyer=request.user,
                    seller=seller,
                    reason=reason,
                    description=description,
                    status='open'
                )
                
                # Auto-refund if item not cleared
                if not item.is_cleared:
                    # Create refund transaction
                    refund_transaction = Transaction.objects.create(
                        platform=item.payment_request.platform,
                        from_wallet=seller.wallet,
                        to_wallet=request.user.wallet,
                        amount=item.amount,
                        transaction_type='refund',
                        status='completed',
                        description=f'Auto-refund for dispute: {dispute.dispute_id}'
                    )
                    
                    # Update wallets
                    seller.wallet.balance -= item.amount
                    request.user.wallet.balance += item.amount
                    
                    seller.wallet.save()
                    request.user.wallet.save()
                    
                    dispute.refund_transaction = refund_transaction
                    dispute.status = 'auto_refunded'
                    dispute.save()
                else:
                    # Escalate to admin
                    dispute.status = 'escalated'
                    dispute.save()
                
                ActivityLog.objects.create(
                    user=request.user,
                    action='dispute_filed',
                    description=f'Dispute filed: {reason}',
                    ip_address=get_client_ip(request)
                )
            
            return JsonResponse({
                'message': 'Dispute filed',
                'dispute_id': dispute.dispute_id,
                'status': dispute.status
            }, status=201)
            
        except Exception as e:
            logger.error(f"Dispute filing error: {str(e)}")
            return JsonResponse({'error': 'Failed to file dispute'}, status=500)
    
    # Get payment items that can be disputed
    payment_items = PaymentRequestItem.objects.filter(
        payment_request__buyer_email=request.user.email,
        is_cleared=False
    )
    
    return render(request, 'file_dispute.html', {'payment_items': payment_items})


@login_required
def resolve_dispute(request, dispute_id):
    """Admin resolves a dispute"""
    if request.user.role not in ['admin', 'superadmin']:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    if request.method == 'POST':
        resolution = request.POST.get('resolution')
        admin_notes = request.POST.get('admin_notes')
        
        try:
            dispute = Dispute.objects.get(dispute_id=dispute_id)
            
            if dispute.status != 'escalated':
                return JsonResponse({'error': 'Dispute not in escalated status'}, status=400)
            
            with db_transaction.atomic():
                if resolution == 'approve_refund':
                    # Process refund
                    refund_transaction = Transaction.objects.create(
                        platform=dispute.payment_request_item.payment_request.platform,
                        from_wallet=dispute.seller.wallet,
                        to_wallet=dispute.buyer.wallet,
                        amount=dispute.payment_request_item.amount,
                        transaction_type='refund',
                        status='completed',
                        description=f'Admin-approved refund for dispute: {dispute.dispute_id}'
                    )
                    
                    dispute.seller.wallet.balance -= dispute.payment_request_item.amount
                    dispute.buyer.wallet.balance += dispute.payment_request_item.amount
                    
                    dispute.seller.wallet.save()
                    dispute.buyer.wallet.save()
                    
                    dispute.refund_transaction = refund_transaction
                    dispute.status = 'resolved'
                else:
                    dispute.status = 'rejected'
                
                dispute.admin_notes = admin_notes
                dispute.resolved_by = request.user
                dispute.resolved_at = timezone.now()
                dispute.save()
                
                ActivityLog.objects.create(
                    user=request.user,
                    action='dispute_resolved',
                    description=f'Dispute {dispute.dispute_id} resolved: {resolution}',
                    ip_address=get_client_ip(request)
                )
            
            return JsonResponse({'message': 'Dispute resolved'}, status=200)
            
        except Exception as e:
            logger.error(f"Dispute resolution error: {str(e)}")
            return JsonResponse({'error': 'Failed to resolve dispute'}, status=500)
    
    dispute = get_object_or_404(Dispute, dispute_id=dispute_id)
    return render(request, 'resolve_dispute.html', {'dispute': dispute})


# ============= PLATFORM MANAGEMENT VIEWS =============

@login_required
def register_platform(request):
    """Admin registers a new RP platform"""
    if request.user.role != 'admin':
        return redirect('home')
    
    if request.method == 'POST':
        platform_name = request.POST.get('platform_name')
        domain = request.POST.get('domain')
        return_url = request.POST.get('return_url')
        callback_url = request.POST.get('callback_url')
        mobile_money_api_key = request.POST.get('mobile_money_api_key')
        mobile_money_provider = request.POST.get('mobile_money_provider')
        
        try:
            with db_transaction.atomic():
                platform = Platform.objects.create(
                    admin=request.user,
                    platform_name=platform_name,
                    domain=domain,
                    return_url=return_url,
                    callback_url=callback_url,
                    mobile_money_api_key=mobile_money_api_key,
                    mobile_money_provider=mobile_money_provider
                )
                
                ActivityLog.objects.create(
                    user=request.user,
                    platform=platform,
                    action='platform_registered',
                    description=f'Platform registered: {platform_name}',
                    ip_address=get_client_ip(request)
                )
            
            return JsonResponse({
                'message': 'Platform registered successfully',
                'platform_id': platform.platform_id,
                'api_key': str(platform.api_key)
            }, status=201)
            
        except Exception as e:
            logger.error(f"Platform registration error: {str(e)}")
            return JsonResponse({'error': 'Failed to register platform'}, status=500)
    
    return render(request, 'register_platform.html')


@login_required
def platform_details(request, platform_id):
    """View platform details"""
    platform = get_object_or_404(Platform, platform_id=platform_id)
    
    if request.user.role == 'admin' and platform.admin != request.user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    transactions = Transaction.objects.filter(platform=platform)[:20]
    payment_requests = PaymentRequest.objects.filter(platform=platform)[:20]
    
    context = {
        'platform': platform,
        'transactions': transactions,
        'payment_requests': payment_requests,
    }
    return render(request, 'platform_details.html', context)


# ============= USER PROFILE VIEWS =============

@login_required
def user_profile(request):
    """View and edit user profile"""
    if request.method == 'POST':
        phone_number = request.POST.get('phone_number')
        
        request.user.phone_number = phone_number
        request.user.save()
        
        ActivityLog.objects.create(
            user=request.user,
            action='settings_updated',
            description='Profile updated',
            ip_address=get_client_ip(request)
        )
        
        return JsonResponse({'message': 'Profile updated'}, status=200)
    
    context = {
        'user': request.user,
        'wallet': request.user.wallet,
    }
    return render(request, 'user_profile.html', context)


@login_required
def delete_account(request):
    """Delete user account"""
    if request.method == 'POST':
        try:
            user = request.user
            user.delete()
            logout(request)
            
            return JsonResponse({
                'status': 'success',
                'message': 'Account deleted successfully.'
            })
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            }, status=500)
    return JsonResponse({
        'status': 'error',
        'message': 'Invalid request method.'
    }, status=405)


# ============= HELPER FUNCTIONS =============

def get_client_ip(request):
    """Get client IP address"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def get_dashboard_url(role):
    """Get dashboard URL based on role"""
    if role == 'buyer':
        return '/buyer-dashboard/'
    elif role == 'seller':
        return '/seller-dashboard/'
    elif role == 'admin':
        return '/admin-dashboard/'
    elif role == 'superadmin':
        return '/superadmin-dashboard/'
    return '/home/'


@csrf_exempt
def simulate_webhook_completion(request, transaction_id):
    """
    Simulates webhook callback to complete deposit
    This would be called by the payment gateway in production
    """
    try:
        from .payment_processor import complete_pending_deposit
        
        result = complete_pending_deposit(
            transaction_id=transaction_id,
            external_reference=str(transaction_id)
        )
        
        return JsonResponse(result)
        
    except Exception as e:
        logger.error(f"Webhook simulation error: {str(e)}")
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)
