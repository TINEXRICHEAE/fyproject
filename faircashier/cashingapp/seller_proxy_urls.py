# cashingapp/seller_proxy_urls.py
"""
Fair Cashier - Seller Access URLs
Routes for seller access from external e-commerce platforms
"""

from django.urls import path
from . import seller_proxy_views

seller_proxy_urlpatterns = [
    # Seller dashboard iframe (embedded in e-commerce app)
    path('payment/seller-dashboard/', 
         seller_proxy_views.seller_dashboard_iframe, 
         name='seller_dashboard_iframe'),
    
    # Seller dashboard data (POST with PIN)
    path('api/seller/dashboard-data/', 
         seller_proxy_views.seller_dashboard_data, 
         name='seller_dashboard_data_api'),
    
    # Cashout request to platform admin
    path('api/seller/request-cashout/', 
         seller_proxy_views.request_cashout_to_admin, 
         name='seller_request_cashout'),
]
