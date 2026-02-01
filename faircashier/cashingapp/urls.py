from django.urls import path
from . import views

urlpatterns = [
    # Home and Authentication
    path('', views.home, name='home'),
    path('home/', views.home, name='home-alt'),
    path('register/', views.register_user, name='register_user'),
    path('login/', views.login_user, name='login_user'),
    path('logout/', views.logout_user, name='logout_user'),
    path('check-auth/', views.check_auth, name='check_auth'),
    
    # Dashboards
    path('buyer-dashboard/', views.buyer_dashboard, name='buyer-dashboard'),
    path('seller-dashboard/', views.seller_dashboard, name='seller-dashboard'),
    path('admin-dashboard/', views.admin_dashboard, name='admin-dashboard'),
    path('superadmin-dashboard/', views.superadmin_dashboard, name='superadmin-dashboard'),
    
    # Wallet Operations
    path('wallet/', views.wallet_view, name='wallet'),
    path('deposit/', views.deposit, name='deposit'),
    path('deposit/processing/', views.deposit_processing, name='deposit_processing'),
    path('cashout/', views.cashout, name='cashout'),
    
    # Payment Requests
    path('api/payment-request/create/', views.create_payment_request, name='create_payment_request'),
    path('payment/<uuid:request_id>/', views.payment_page, name='payment_page'),
    path('payment/<uuid:request_id>/process/', views.process_payment, name='process_payment'),
    path('payment-item/<int:item_id>/clear/', views.clear_payment_item, name='clear_payment_item'),
    
    # Disputes
    path('dispute/file/', views.file_dispute, name='file_dispute'),
    path('dispute/<int:dispute_id>/resolve/', views.resolve_dispute, name='resolve_dispute'),
    
    # Platform Management
    path('platform/register/', views.register_platform, name='register_platform'),
    path('platform/<int:platform_id>/', views.platform_details, name='platform_details'),
    
    # User Profile
    path('profile/', views.user_profile, name='user_profile'),
    path('delete-account/', views.delete_account, name='delete_account'),
    
    # Webhook simulation (for development)
    path('api/webhook/complete/<uuid:transaction_id>/', views.simulate_webhook_completion, name='simulate_webhook'),
]
