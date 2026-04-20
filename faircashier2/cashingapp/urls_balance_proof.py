from django.urls import path
from . import views_balance_proof


balance_proof_urlpatterns = [
    path('internal/order-created/', views_balance_proof.internal_order_created, name='internal_order_created'),
    path('internal/balance-proof/refresh/', views_balance_proof.internal_balance_proof_refresh, name='internal_balance_proof_refresh'),
    path('internal/balance-proof/', views_balance_proof.internal_balance_proof_fetch, name='internal_balance_proof_fetch'),

    
]