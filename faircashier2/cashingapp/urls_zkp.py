# cashing_app/urls_zkp.py
# ─────────────────────────────────────────────────────────────────────────────

from django.urls import path
from . import views_zkp

zkp_urlpatterns = [
    path('seller/zkp-verify/', views_zkp.seller_zkp_verify, name='seller_zkp_verify'),
    path('internal/seller-zkp-status/<str:seller_email>/', views_zkp.internal_seller_zkp_status, name='internal_seller_zkp_status'),
    path('api/internal/seller-zkp-status/<str:seller_email>/', views_zkp.api_internal_seller_zkp_status, name='api_internal_seller_zkp_status'),
]
