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
import csv
from django.http import HttpResponse
from .models import (
    Users, Platform, Transaction, PaymentRequest, 
    Dispute, Wallet, ActivityLog, CashoutRequest
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
        status__in=['submitted', 'under_review', 'escalated']
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
            status__in=['submitted', 'under_review', 'escalated']
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
                # Use the specific disputed item amount, not the full payment request item amount
                refund_amount = dispute.disputed_amount or dispute.payment_request_item.amount

                if resolution == 'await_review':
                    dispute.status = 'under_review'
                    payment_status_to_sync = 'To Be Decided'
                    
                elif resolution == 'resolve_with_refund':
                    # Verify seller has enough balance
                    if dispute.seller.wallet.balance < refund_amount:
                        return JsonResponse({
                            'error': f'Insufficient seller balance. '
                                     f'Available: {dispute.seller.wallet.balance}, '
                                     f'Required: {refund_amount}'
                        }, status=400)

                    # Process refund using the disputed item amount only
                    refund_transaction = Transaction.objects.create(
                        platform=dispute.payment_request_item.payment_request.platform,
                        from_wallet=dispute.seller.wallet,
                        to_wallet=dispute.buyer.wallet,
                        amount=refund_amount,
                        transaction_type='refund',
                        status='completed',
                        description=f'Admin refund: Dispute #{dispute.dispute_id}'
                    )
                    
                    dispute.seller.wallet.balance -= refund_amount
                    dispute.buyer.wallet.balance += refund_amount
                    
                    dispute.seller.wallet.save()
                    dispute.buyer.wallet.save()
                    
                    dispute.refund_transaction = refund_transaction
                    dispute.status = 'resolved_with_refund'
                    payment_status_to_sync = 'Refunded'
                else:
                    dispute.status = 'resolved_without_refund'
                    payment_status_to_sync = 'Not Refunded'
                
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
                # Sync to shopping app
                from .dispute_api_views import _sync_dispute_to_shopping_app
                _sync_dispute_to_shopping_app(
                    dispute,
                    payment_status=payment_status_to_sync,
                    refund_amount=str(refund_amount) if resolution == 'resolve_with_refund' else None,
                    admin_notes=admin_notes,
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


# ============= CASHOUT REQUEST MANAGEMENT =============

@login_required(login_url='admin_login')
def admin_cashout_requests(request):
    """
    List all cashout requests for admin review.
    Supports filtering by status and payment method.
    """
    if request.user.role not in ['admin', 'superadmin']:
        return redirect('home')

    # Get platforms for this admin (or all for superadmin)
    if request.user.role == 'admin':
        platforms = Platform.objects.filter(admin=request.user)
        cashout_requests = CashoutRequest.objects.filter(platform__in=platforms)
    else:
        cashout_requests = CashoutRequest.objects.all()
        platforms = Platform.objects.all()

    # Apply filters
    status_filter = request.GET.get('status', 'all')
    method_filter = request.GET.get('method', 'all')

    if status_filter != 'all':
        cashout_requests = cashout_requests.filter(status=status_filter)
    if method_filter != 'all':
        cashout_requests = cashout_requests.filter(payment_method=method_filter)

    cashout_requests = cashout_requests.select_related(
        'seller', 'platform', 'reviewed_by'
    ).order_by('-created_at')

    # Summary stats
    all_for_stats = CashoutRequest.objects.filter(
        platform__in=platforms
    ) if request.user.role == 'admin' else CashoutRequest.objects.all()

    stats = {
        'total_pending': all_for_stats.filter(status='pending').count(),
        'total_pending_amount': all_for_stats.filter(
            status='pending'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0'),
        'total_approved': all_for_stats.filter(status='approved').count(),
        'total_approved_amount': all_for_stats.filter(
            status='approved'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0'),
        'total_disbursed': all_for_stats.filter(status='disbursed').count(),
        'total_disbursed_amount': all_for_stats.filter(
            status='disbursed'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0'),
    }

    context = {
        'cashout_requests': cashout_requests[:100],
        'stats': stats,
        'status_filter': status_filter,
        'method_filter': method_filter,
        'user': request.user,
    }

    return render(request, 'admin_cashout_requests.html', context)


@login_required(login_url='admin_login')
@csrf_exempt
def admin_review_cashout(request, cashout_id):
    """
    Approve or reject a single cashout request.
    
    POST params:
        action: 'approve' or 'reject'
        admin_notes: optional notes
    """
    if request.user.role not in ['admin', 'superadmin']:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    cashout = get_object_or_404(CashoutRequest, cashout_id=cashout_id)

    # Verify admin owns the platform (unless superadmin)
    if request.user.role == 'admin' and cashout.platform.admin != request.user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    if request.method != 'POST':
        # Return cashout details as JSON for modal/detail view
        return JsonResponse({
            'cashout_id': cashout.cashout_id,
            'seller_email': cashout.seller.email,
            'amount': str(cashout.amount),
            'currency': cashout.currency,
            'payment_method': cashout.get_payment_method_display(),
            'payment_method_key': cashout.payment_method,
            'phone_number': cashout.phone_number or '',
            'recipient_name': cashout.recipient_name or '',
            'bank_name': cashout.bank_name or '',
            'account_number': cashout.account_number or '',
            'account_name': cashout.account_name or '',
            'seller_note': cashout.seller_note or '',
            'status': cashout.status,
            'created_at': cashout.created_at.isoformat(),
            'seller_balance': str(cashout.seller.wallet.balance),
            'platform_name': cashout.platform.platform_name,
        })

    action = request.POST.get('action')
    admin_notes = request.POST.get('admin_notes', '')

    if action not in ('approve', 'reject'):
        return JsonResponse({'error': 'Invalid action'}, status=400)

    if cashout.status != 'pending':
        return JsonResponse({
            'error': f'Cannot {action} a request that is already {cashout.status}'
        }, status=400)

    try:
        cashout.reviewed_by = request.user
        cashout.reviewed_at = timezone.now()
        cashout.admin_notes = admin_notes

        if action == 'approve':
            # Verify seller still has sufficient balance
            wallet = cashout.seller.wallet
            if wallet.balance < cashout.amount:
                return JsonResponse({
                    'error': f'Seller has insufficient balance ({wallet.balance} UGX)'
                }, status=400)

            cashout.status = 'approved'
            logger.info(
                f"✅ Cashout {cashout_id} approved by {request.user.email} "
                f"({cashout.amount} UGX for {cashout.seller.email})"
            )
        else:
            cashout.status = 'rejected'
            logger.info(
                f"❌ Cashout {cashout_id} rejected by {request.user.email}"
            )

        cashout.save()

        ActivityLog.objects.create(
            user=request.user,
            platform=cashout.platform,
            action='cashout',
            description=f'Cashout request {cashout_id} {action}d: {cashout.amount} UGX for {cashout.seller.email}',
            ip_address=get_client_ip(request)
        )

        return JsonResponse({
            'success': True,
            'message': f'Cashout request {action}d successfully',
            'cashout_id': cashout_id,
            'new_status': cashout.status
        })

    except Exception as e:
        logger.error(f"❌ Cashout review error: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Failed to process review'}, status=500)


@login_required(login_url='admin_login')
@csrf_exempt
def admin_bulk_approve_cashouts(request):
    """
    Bulk approve multiple pending cashout requests.
    
    POST params:
        cashout_ids: comma-separated list of cashout IDs
    """
    if request.user.role not in ['admin', 'superadmin']:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=400)

    cashout_ids_str = request.POST.get('cashout_ids', '')
    if not cashout_ids_str:
        return JsonResponse({'error': 'No cashout IDs provided'}, status=400)

    try:
        cashout_ids = [int(x.strip()) for x in cashout_ids_str.split(',') if x.strip()]
    except ValueError:
        return JsonResponse({'error': 'Invalid cashout IDs'}, status=400)

    approved_count = 0
    errors = []

    for cid in cashout_ids:
        try:
            cashout = CashoutRequest.objects.get(cashout_id=cid, status='pending')

            # Verify admin owns platform
            if request.user.role == 'admin' and cashout.platform.admin != request.user:
                errors.append(f"#{cid}: Unauthorized")
                continue

            # Check balance
            if cashout.seller.wallet.balance < cashout.amount:
                errors.append(f"#{cid}: Insufficient seller balance")
                continue

            cashout.status = 'approved'
            cashout.reviewed_by = request.user
            cashout.reviewed_at = timezone.now()
            cashout.save()
            approved_count += 1

        except CashoutRequest.DoesNotExist:
            errors.append(f"#{cid}: Not found or not pending")

    return JsonResponse({
        'success': True,
        'approved': approved_count,
        'errors': errors
    })


@login_required(login_url='admin_login')
def admin_export_cashouts_csv(request):
    """
    Export approved cashout requests as CSV files grouped by payment method.
    
    Generates CSV compatible with:
    - MTN Mobile Money bulk payment format
    - Airtel Money bulk payment format
    - Bank transfer bulk payment format
    
    GET params:
        method: 'mtn_mobile_money', 'airtel_mobile_money', 'bank_transfer', or 'all'
        status: 'approved' (default) or 'pending'
    """
    if request.user.role not in ['admin', 'superadmin']:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    method = request.GET.get('method', 'all')
    status = request.GET.get('status', 'approved')

    # Get cashout requests
    if request.user.role == 'admin':
        platforms = Platform.objects.filter(admin=request.user)
        cashouts = CashoutRequest.objects.filter(
            platform__in=platforms, status=status
        )
    else:
        cashouts = CashoutRequest.objects.filter(status=status)

    if method != 'all':
        cashouts = cashouts.filter(payment_method=method)

    cashouts = cashouts.select_related('seller', 'platform').order_by('payment_method', '-created_at')

    if not cashouts.exists():
        return JsonResponse({
            'error': 'No cashout requests found for export'
        }, status=404)

    # Determine which format to export
    if method == 'mtn_mobile_money':
        return _export_mtn_csv(cashouts)
    elif method == 'airtel_mobile_money':
        return _export_airtel_csv(cashouts)
    elif method == 'bank_transfer':
        return _export_bank_csv(cashouts)
    else:
        # Export all in a unified format
        return _export_unified_csv(cashouts)


def _export_mtn_csv(cashouts):
    """
    Export CSV compatible with MTN Mobile Money bulk disbursement format.
    
    MTN MoMo Bulk Payment columns:
    Amount, MSISDN, Comment, Currency, Reason, Recipient Name, Email
    """
    response = HttpResponse(content_type='text/csv')
    timestamp = timezone.now().strftime('%Y%m%d_%H%M')
    response['Content-Disposition'] = f'attachment; filename="mtn_bulk_payment_{timestamp}.csv"'

    writer = csv.writer(response)
    # Header row matching MTN bulk payment template
    writer.writerow([
        'Amount', 'MSISDN', 'Comment', 'Currency',
        'Reason', 'Recipient Name', 'Email'
    ])

    for cashout in cashouts:
        writer.writerow([
            str(cashout.amount),
            cashout.phone_number or '',
            f'Seller cashout #{cashout.cashout_id}',
            cashout.currency,
            f'Platform payout - {cashout.platform.platform_name}',
            cashout.recipient_name or cashout.seller.email,
            cashout.seller.email,
        ])

    return response


def _export_airtel_csv(cashouts):
    """
    Export CSV compatible with Airtel Money bulk disbursement format.
    
    Airtel Money Bulk Payment columns:
    Phone Number, Amount, Currency, Reference, Recipient Name, Email
    """
    response = HttpResponse(content_type='text/csv')
    timestamp = timezone.now().strftime('%Y%m%d_%H%M')
    response['Content-Disposition'] = f'attachment; filename="airtel_bulk_payment_{timestamp}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Phone Number', 'Amount', 'Currency', 'Reference',
        'Recipient Name', 'Email'
    ])

    for cashout in cashouts:
        writer.writerow([
            cashout.phone_number or '',
            str(cashout.amount),
            cashout.currency,
            f'CASHOUT-{cashout.cashout_id}',
            cashout.recipient_name or cashout.seller.email,
            cashout.seller.email,
        ])

    return response


def _export_bank_csv(cashouts):
    """
    Export CSV compatible with bank bulk transfer format.
    
    Bank Transfer columns:
    Bank Name, Account Number, Account Name, Amount, Currency, Reference, Email
    """
    response = HttpResponse(content_type='text/csv')
    timestamp = timezone.now().strftime('%Y%m%d_%H%M')
    response['Content-Disposition'] = f'attachment; filename="bank_bulk_transfer_{timestamp}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Bank Name', 'Account Number', 'Account Name',
        'Amount', 'Currency', 'Reference', 'Email'
    ])

    for cashout in cashouts:
        writer.writerow([
            cashout.bank_name or '',
            cashout.account_number or '',
            cashout.account_name or '',
            str(cashout.amount),
            cashout.currency,
            f'CASHOUT-{cashout.cashout_id}',
            cashout.seller.email,
        ])

    return response


def _export_unified_csv(cashouts):
    """
    Export all cashout requests in a unified CSV format.
    Grouped by payment method with all fields.
    """
    response = HttpResponse(content_type='text/csv')
    timestamp = timezone.now().strftime('%Y%m%d_%H%M')
    response['Content-Disposition'] = f'attachment; filename="all_cashout_requests_{timestamp}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Cashout ID', 'Payment Method', 'Amount', 'Currency',
        'Phone Number / Account Number', 'Recipient / Account Name',
        'Bank Name', 'Seller Email', 'Platform',
        'Reference', 'Status', 'Requested At', 'Note'
    ])

    for cashout in cashouts:
        if cashout.payment_method == 'bank_transfer':
            identity = cashout.account_number or ''
            name = cashout.account_name or ''
            bank = cashout.bank_name or ''
        else:
            identity = cashout.phone_number or ''
            name = cashout.recipient_name or ''
            bank = ''

        writer.writerow([
            cashout.cashout_id,
            cashout.get_payment_method_display(),
            str(cashout.amount),
            cashout.currency,
            identity,
            name,
            bank,
            cashout.seller.email,
            cashout.platform.platform_name,
            f'CASHOUT-{cashout.cashout_id}',
            cashout.status,
            cashout.created_at.strftime('%Y-%m-%d %H:%M'),
            cashout.seller_note or '',
        ])

    return response


@login_required(login_url='admin_login')
@csrf_exempt
def admin_disburse_cashouts(request):
    """
    Mark approved cashout requests as disbursed.
    
    This is called AFTER the admin has uploaded the CSV to the external
    payment gateway (MTN/Airtel/Bank) and payments have been processed.
    
    On disbursement:
    1. Deducts amount from seller's wallet
    2. Creates a 'cashout' Transaction record
    3. Updates CashoutRequest status to 'disbursed'
    
    POST params:
        cashout_ids: comma-separated list of cashout IDs to mark as disbursed
        external_reference: optional reference from the payment gateway
    """
    if request.user.role not in ['admin', 'superadmin']:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=400)

    cashout_ids_str = request.POST.get('cashout_ids', '')
    external_ref = request.POST.get('external_reference', '')

    if not cashout_ids_str:
        return JsonResponse({'error': 'No cashout IDs provided'}, status=400)

    try:
        cashout_ids = [int(x.strip()) for x in cashout_ids_str.split(',') if x.strip()]
    except ValueError:
        return JsonResponse({'error': 'Invalid cashout IDs'}, status=400)

    from django.db import transaction as db_transaction

    disbursed_count = 0
    total_disbursed = Decimal('0')
    errors = []

    for cid in cashout_ids:
        try:
            with db_transaction.atomic():
                cashout = CashoutRequest.objects.select_for_update().get(
                    cashout_id=cid, status='approved'
                )

                # Verify admin authorization
                if request.user.role == 'admin' and cashout.platform.admin != request.user:
                    errors.append(f"#{cid}: Unauthorized")
                    continue

                wallet = cashout.seller.wallet

                # Verify balance
                if wallet.balance < cashout.amount:
                    errors.append(
                        f"#{cid}: Insufficient balance "
                        f"({wallet.balance} < {cashout.amount})"
                    )
                    continue

                # Deduct from wallet
                wallet.balance -= cashout.amount
                wallet.save()

                # Create transaction record
                tx = Transaction.objects.create(
                    platform=cashout.platform,
                    from_wallet=wallet,
                    amount=cashout.amount,
                    transaction_type='cashout',
                    status='completed',
                    description=(
                        f'Cashout #{cashout.cashout_id} via '
                        f'{cashout.get_payment_method_display()} '
                        f'to {cashout.payment_destination}'
                    ),
                    mobile_money_reference=external_ref or None,
                )

                # Update cashout request
                cashout.status = 'disbursed'
                cashout.disbursed_at = timezone.now()
                cashout.transaction = tx
                cashout.external_reference = external_ref
                cashout.save()

                disbursed_count += 1
                total_disbursed += cashout.amount

                logger.info(
                    f"✅ Cashout #{cid} disbursed: {cashout.amount} UGX "
                    f"to {cashout.seller.email} via {cashout.payment_method}"
                )

        except CashoutRequest.DoesNotExist:
            errors.append(f"#{cid}: Not found or not approved")
        except Exception as e:
            errors.append(f"#{cid}: {str(e)}")
            logger.error(f"❌ Disbursement error for #{cid}: {str(e)}", exc_info=True)

    # Log activity
    ActivityLog.objects.create(
        user=request.user,
        action='cashout',
        description=(
            f'Bulk disbursement: {disbursed_count} cashouts, '
            f'total {total_disbursed} UGX'
        ),
        ip_address=get_client_ip(request)
    )

    return JsonResponse({
        'success': True,
        'disbursed': disbursed_count,
        'total_amount': str(total_disbursed),
        'errors': errors
    })