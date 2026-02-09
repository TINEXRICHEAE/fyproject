# cashingapp/urls.py 

from django.urls import path
from . import views
from . import api_views
from . import buyer_seller_views
from . import admin_views

urlpatterns = [
    # ============= HOME =============
    path('', views.home, name='home'),
    
    
    # ============= BUYER/SELLER ROUTES (PIN-BASED) =============
    
    # PIN Authentication
    path('pin-setup/', buyer_seller_views.pin_setup, name='pin_setup'),
    path('pin-login/', buyer_seller_views.pin_login, name='pin_login'),
    
    # Dashboards (PIN-protected)
    path('buyer-dashboard/', buyer_seller_views.buyer_dashboard, name='buyer_dashboard'),
    path('seller-dashboard/', buyer_seller_views.seller_dashboard, name='seller_dashboard'),
    
    # Wallet Operations (PIN-protected)
    path('wallet-pin/', buyer_seller_views.wallet_view_pin, name='wallet_pin'),
    path('deposit-pin/', buyer_seller_views.deposit_pin, name='deposit_pin'),
    path('cashout-pin/', buyer_seller_views.cashout_pin, name='cashout_pin'),
    
    # Payment Processing (PIN-based)
    path('payment/<uuid:request_id>/', views.payment_page, name='payment_page'),
    path('payment/<uuid:request_id>/process-pin/', buyer_seller_views.process_payment_with_pin, name='process_payment_pin'),
    
    
    # ============= API ENDPOINTS =============
    
    # PIN-related APIs
    path('api/check-buyer-status/', api_views.check_buyer_status, name='check_buyer_status'),
    path('api/verify-pin/', api_views.verify_pin_api, name='verify_pin_api'),
    path('api/wallet-info/', api_views.get_wallet_info, name='wallet_info_api'),
    path('api/update-pin/', api_views.update_pin, name='update_pin_api'),
    
    # Seller verification
    path('api/check-sellers/', api_views.check_sellers, name='api_check_sellers'),
    
    # Payment request creation
    path('api/payment-request/create/', views.create_payment_request, name='create_payment_request'),
    
    # Webhook simulation
    path('api/webhook/complete/<uuid:transaction_id>/', views.simulate_webhook_completion, name='simulate_webhook'),
    
    
    # ============= ADMIN/SUPERADMIN ROUTES (PASSWORD-BASED) =============
    
    # Authentication
    path('login/', admin_views.admin_login, name='admin_login'),
    path('register/', admin_views.admin_register, name='admin_register'),
    path('logout/', admin_views.admin_logout, name='admin_logout'),
    
    # Dashboards
    path('admin-dashboard/', admin_views.admin_dashboard, name='admin_dashboard'),
    path('superadmin-dashboard/', admin_views.superadmin_dashboard, name='superadmin_dashboard'),
    
    # Platform Management
    path('register-platform/', admin_views.register_platform, name='admin_register_platform'),
    path('platform/<int:platform_id>/', admin_views.platform_details, name='admin_platform_details'),
    
    # Dispute Management
    path('disputes/', admin_views.disputes_list, name='admin_disputes'),
    path('dispute/<int:dispute_id>/resolve/', admin_views.resolve_dispute, name='admin_resolve_dispute'),
    
    # User Management (Superadmin)
    path('users/', admin_views.users_list, name='admin_users'),
    path('user/<int:user_id>/', admin_views.user_details, name='admin_user_details'),
    
    # Transactions
    path('transactions/', admin_views.transactions_list, name='admin_transactions'),
]