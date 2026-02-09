# cashingapp/admin_views.py (FIXED)

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.db.models import Sum, Q
from django.utils import timezone
from decimal import Decimal
import logging

from .models import (
    Users, Platform, Transaction, PaymentRequest, 
    Dispute, Wallet, ActivityLog
)

logger = logging.getLogger(__name__)


# ============= ADMIN AUTHENTICATION (PASSWORD-BASED) =============

@csrf_exempt
def admin_login(request):
    """
    Admin/Superadmin login with email + password
    Separate from buyer/seller PIN system
    """
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        if not email or not password:
            return JsonResponse({'error': 'Email and password required'}, status=400)
        
        # Authenticate
        user = authenticate(request, email=email, password=password)
        
        if user is not None and user.role in ['admin', 'superadmin']:
            # ✅ FIX: Specify backend explicitly
            login(request, user, backend='cashingapp.backends.EmailBackend')
            
            # Log activity
            ActivityLog.objects.create(
                user=user,
                action='login',
                description=f'{user.role} logged in',
                ip_address=get_client_ip(request)
            )
            
            # Redirect based on role
            if user.role == 'superadmin':
                redirect_url = '/superadmin-dashboard/'
            else:
                redirect_url = '/admin-dashboard/'
            
            return JsonResponse({
                'success': True,
                'message': 'Login successful',
                'redirect_url': redirect_url
            })
        else:
            return JsonResponse({'error': 'Invalid credentials or unauthorized'}, status=401)
    
    # GET - show login form
    return render(request, 'admin_login.html')


@csrf_exempt
def admin_register(request):
    """
    Admin registration (requires approval or superadmin invite)
    """
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        phone_number = request.POST.get('phone_number', '')
        
        if not email or not password:
            return JsonResponse({'error': 'Email and password required'}, status=400)
        
        if Users.objects.filter(email=email).exists():
            return JsonResponse({'error': 'Email already registered'}, status=400)
        
        try:
            # Create admin user
            user = Users.objects.create_user(
                email=email,
                password=password,
                role='admin',
                phone_number=phone_number,
                is_staff=True,
                is_superuser=False
            )
            
            # Create wallet
            Wallet.objects.create(user=user)
            
            # ✅ FIX: Auto-login with explicit backend
            login(request, user, backend='cashingapp.backends.EmailBackend')
            
            ActivityLog.objects.create(
                user=user,
                action='register',
                description='Admin registered',
                ip_address=get_client_ip(request)
            )
            
            logger.info(f"✅ Admin registered: {email}")
            
            return JsonResponse({
                'success': True,
                'message': 'Admin account created',
                'redirect_url': '/admin-dashboard/'
            })
            
        except Exception as e:
            logger.error(f"❌ Admin registration error: {str(e)}")
            return JsonResponse({'error': 'Registration failed'}, status=500)
    
    # GET - show registration form
    return render(request, 'admin_register.html')


def admin_logout(request):
    """Logout admin/superadmin"""
    if request.user.is_authenticated:
        ActivityLog.objects.create(
            user=request.user,
            action='logout',
            description=f'{request.user.role} logged out',
            ip_address=get_client_ip(request)
        )
    
    logout(request)
    return redirect('admin_login')


# ============= ADMIN DASHBOARD =============

@login_required(login_url='admin_login')
def admin_dashboard(request):
    """Admin dashboard - platform management"""
    if request.user.role != 'admin':
        return redirect('home')
    
    platforms = Platform.objects.filter(admin=request.user)
    
    # Statistics
    total_transactions = Transaction.objects.filter(
        platform__in=platforms
    ).count()
    
    total_volume = Transaction.objects.filter(
        platform__in=platforms,
        status='completed'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    recent_payments = PaymentRequest.objects.filter(
        platform__in=platforms
    ).order_by('-created_at')[:10]
    
    pending_disputes = Dispute.objects.filter(
        payment_request_item__payment_request__platform__in=platforms,
        status__in=['open', 'escalated']
    ).count()
    
    context = {
        'user': request.user,
        'platforms': platforms,
        'total_transactions': total_transactions,
        'total_volume': total_volume,
        'recent_payments': recent_payments,
        'pending_disputes': pending_disputes,
    }
    
    return render(request, 'admin_dashboard.html', context)


# ============= SUPERADMIN DASHBOARD =============

@login_required(login_url='admin_login')
def superadmin_dashboard(request):
    """Superadmin dashboard - system overview"""
    if request.user.role != 'superadmin':
        return redirect('home')
    
    # System-wide statistics
    stats = {
        'total_users': Users.objects.count(),
        'total_buyers': Users.objects.filter(role='buyer').count(),
        'total_sellers': Users.objects.filter(role='seller').count(),
        'total_admins': Users.objects.filter(role='admin').count(),
        'total_platforms': Platform.objects.count(),
        'total_transactions': Transaction.objects.count(),
        'total_volume': Transaction.objects.filter(
            status='completed'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00'),
        'pending_disputes': Dispute.objects.filter(
            status__in=['open', 'escalated']
        ).count(),
    }
    
    recent_users = Users.objects.order_by('-created_at')[:10]
    recent_platforms = Platform.objects.order_by('-created_at')[:10]
    recent_transactions = Transaction.objects.order_by('-created_at')[:10]
    
    context = {
        'user': request.user,
        'stats': stats,
        'recent_users': recent_users,
        'recent_platforms': recent_platforms,
        'recent_transactions': recent_transactions,
    }
    
    return render(request, 'superadmin_dashboard.html', context)


# ============= PLATFORM MANAGEMENT =============

@login_required(login_url='admin_login')
def register_platform(request):
    """Register new platform"""
    if request.user.role != 'admin':
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    if request.method == 'POST':
        platform_name = request.POST.get('platform_name')
        domain = request.POST.get('domain')
        return_url = request.POST.get('return_url')
        callback_url = request.POST.get('callback_url')
        mobile_money_api_key = request.POST.get('mobile_money_api_key', '')
        mobile_money_provider = request.POST.get('mobile_money_provider', 'mtn')
        
        try:
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
                'success': True,
                'message': 'Platform registered',
                'platform_id': platform.platform_id,
                'api_key': str(platform.api_key)
            })
            
        except Exception as e:
            logger.error(f"Platform registration error: {str(e)}")
            return JsonResponse({'error': str(e)}, status=500)
    
    return render(request, 'admin_register_platform.html')


@login_required(login_url='admin_login')
def platform_details(request, platform_id):
    """View platform details"""
    platform = get_object_or_404(Platform, platform_id=platform_id)
    
    # Check authorization
    if request.user.role == 'admin' and platform.admin != request.user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    transactions = Transaction.objects.filter(platform=platform).order_by('-created_at')[:20]
    payment_requests = PaymentRequest.objects.filter(platform=platform).order_by('-created_at')[:20]
    
    context = {
        'platform': platform,
        'transactions': transactions,
        'payment_requests': payment_requests,
    }
    
    return render(request, 'admin_platform_details.html', context)


# ============= DISPUTE MANAGEMENT =============

@login_required(login_url='admin_login')
def disputes_list(request):
    """List all disputes for admin/superadmin"""
    if request.user.role == 'admin':
        # Show disputes from admin's platforms
        platforms = Platform.objects.filter(admin=request.user)
        disputes = Dispute.objects.filter(
            payment_request_item__payment_request__platform__in=platforms
        ).order_by('-created_at')
    else:
        # Superadmin sees all
        disputes = Dispute.objects.all().order_by('-created_at')
    
    context = {
        'disputes': disputes,
    }
    
    return render(request, 'admin_disputes.html', context)


@login_required(login_url='admin_login')
@csrf_exempt
def resolve_dispute(request, dispute_id):
    """Resolve a dispute"""
    if request.user.role not in ['admin', 'superadmin']:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    dispute = get_object_or_404(Dispute, dispute_id=dispute_id)
    
    if request.method == 'POST':
        resolution = request.POST.get('resolution')
        admin_notes = request.POST.get('admin_notes', '')
        
        try:
            from django.db import transaction as db_transaction
            
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
                        description=f'Admin refund: Dispute #{dispute.dispute_id}'
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
            
            return JsonResponse({'success': True, 'message': 'Dispute resolved'})
            
        except Exception as e:
            logger.error(f"Dispute resolution error: {str(e)}")
            return JsonResponse({'error': str(e)}, status=500)
    
    context = {'dispute': dispute}
    return render(request, 'admin_resolve_dispute.html', context)


# ============= USER MANAGEMENT (SUPERADMIN) =============

@login_required(login_url='admin_login')
def users_list(request):
    """List all users (superadmin only)"""
    if request.user.role != 'superadmin':
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    users = Users.objects.all().order_by('-created_at')
    
    context = {'users': users}
    return render(request, 'superadmin_users.html', context)


@login_required(login_url='admin_login')
def user_details(request, user_id):
    """View user details (superadmin only)"""
    if request.user.role != 'superadmin':
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    user = get_object_or_404(Users, id=user_id)
    
    wallet = Wallet.objects.filter(user=user).first()
    transactions = Transaction.objects.filter(
        Q(from_wallet=wallet) | Q(to_wallet=wallet)
    ).order_by('-created_at')[:20] if wallet else []
    
    context = {
        'viewed_user': user,
        'wallet': wallet,
        'transactions': transactions,
    }
    
    return render(request, 'superadmin_user_details.html', context)


# ============= TRANSACTIONS (ADMIN/SUPERADMIN) =============

@login_required(login_url='admin_login')
def transactions_list(request):
    """List transactions"""
    if request.user.role == 'admin':
        platforms = Platform.objects.filter(admin=request.user)
        transactions = Transaction.objects.filter(
            platform__in=platforms
        ).order_by('-created_at')
    else:
        transactions = Transaction.objects.all().order_by('-created_at')
    
    context = {'transactions': transactions[:100]}  # Limit to 100
    return render(request, 'admin_transactions.html', context)


# ============= HELPER FUNCTIONS =============

def get_client_ip(request):
    """Get client IP address"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip