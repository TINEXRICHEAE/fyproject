# cashingapp/urls.py 

from django.urls import path
from . import views
from . import api_views
from . import buyer_seller_views
from . import admin_views
from . import dispute_api_views
from cashingapp.seller_proxy_urls import seller_proxy_urlpatterns
from .urls_zkp import zkp_urlpatterns
from .urls_balance_proof import balance_proof_urlpatterns


urlpatterns = [
    # ============= HOME =============
    path('', views.home, name='home'),
    
    
    # ============= BUYER/SELLER ROUTES (PIN-BASED) =============
    
    # PIN Authentication
    path('pin-setup/', buyer_seller_views.pin_setup, name='pin_setup'),
    path('pin-login/', buyer_seller_views.pin_login, name='pin_login'),
    
    # Dashboards (PIN-protected)
    path('buyer-dashboard/', buyer_seller_views.buyer_dashboard, name='buyer_dashboard'),
    
    
    # Wallet Operations (PIN-protected)
    path('wallet-pin/', buyer_seller_views.wallet_view_pin, name='wallet_pin'),
    path('deposit-pin/', buyer_seller_views.deposit_pin, name='deposit_pin'),
    path('cashout-pin/', buyer_seller_views.cashout_pin, name='cashout_pin'),
    path('seller-request-cashout/', buyer_seller_views.seller_request_cashout, name='seller_request_cashout'),
    
    # Payment Processing (PIN-based)
    path('payment/<uuid:request_id>/', views.payment_page, name='payment_page'),
    path('payment/<uuid:request_id>/process-pin/', buyer_seller_views.process_payment_with_pin, name='process_payment_pin'),
    path('payment/<uuid:request_id>/deposit-and-pay/', buyer_seller_views.deposit_and_pay, name='deposit_and_pay'),
    
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

    path('payment/<uuid:request_id>/process-items/',
         buyer_seller_views.process_payment_items,
         name='process_payment_items'),
    
    path('payment/<uuid:request_id>/complete-deposit/shopping-item/<int:shopping_order_item_id>/',
         buyer_seller_views.complete_deposit_by_order_item,
         name='complete_deposit_by_order_item'),

    path('payment/<uuid:request_id>/cancel-deposit/shopping-item/<int:shopping_order_item_id>/',
         buyer_seller_views.cancel_deposit_by_order_item,
         name='cancel_deposit_by_order_item'),

    # Release seller's escrowed funds after buyer confirms delivery
    # or admin resolves dispute without refund
    path('payment/<uuid:request_id>/release-seller-funds/shopping-item/<int:shopping_order_item_id>/',
         buyer_seller_views.release_seller_funds,
         name='release_seller_funds'),
    
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

    # Dispute Integration (from shopping app)
    path('api/dispute/create-from-shopping/',
         dispute_api_views.create_dispute_from_shopping,
         name='api_create_dispute_from_shopping'),

    path('dispute/<int:dispute_id>/resolve-with-sync/',
         dispute_api_views.resolve_dispute_with_sync,
         name='resolve_dispute_with_sync'),

    # Cashout Request Management (Admin)
    path('admin-cashout-requests/', admin_views.admin_cashout_requests, name='admin_cashout_requests'),
    path('admin-cashout-review/<int:cashout_id>/', admin_views.admin_review_cashout, name='admin_review_cashout'),
    path('admin-cashout-bulk-approve/', admin_views.admin_bulk_approve_cashouts, name='admin_bulk_approve'),
    path('admin-cashout-export/', admin_views.admin_export_cashouts_csv, name='admin_export_cashouts'),
    path('admin-cashout-disburse/', admin_views.admin_disburse_cashouts, name='admin_disburse_cashouts'),
    
    # User Management (Superadmin)
    path('users/', admin_views.users_list, name='admin_users'),
    path('user/<int:user_id>/', admin_views.user_details, name='admin_user_details'),
    
    # Transactions
    path('transactions/', admin_views.transactions_list, name='admin_transactions'),

    
] + seller_proxy_urlpatterns + zkp_urlpatterns + balance_proof_urlpatterns