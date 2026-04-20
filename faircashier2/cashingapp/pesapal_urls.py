
from django.urls import path
from .pesapal_views import *


# ─── URL patterns ─────────────────────────────────────────────────────────────

pesapal_urlpatterns = [
    path('pesapal/ipn/',
         pesapal_ipn,
         name='pesapal_ipn'),

    path('pesapal/callback/',
         pesapal_callback,
         name='pesapal_callback'),

    path('pesapal/iframe/<str:merchant_reference>/',
         pesapal_iframe,
         name='pesapal_iframe'),

    path('pesapal/simulate/<str:merchant_reference>/',
         simulate_pesapal_callback,
         name='simulate_pesapal_callback'),
]
