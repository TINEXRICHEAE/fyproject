"""
cashingapp/pesapal_views.py
===========================
PesaPal-specific views. No mock code.

Views
-----
pesapal_ipn                — receives PesaPal IPN notifications
pesapal_callback           — buyer lands here after PesaPal payment page
pesapal_iframe             — renders PesaPal iframe for a pending deposit
simulate_pesapal_callback  — sandbox helper (DEBUG=True only)

URL patterns exported as pesapal_urlpatterns — import and append to
cashingapp/urls.py (see urls.py in this delivery).
"""

import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import path
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


# ─── IPN endpoint ─────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET', 'POST'])
def pesapal_ipn(request):
    """
    POST (or GET) /pesapal/ipn/

    PesaPal calls this URL whenever a payment status changes.
    We must respond with a specific JSON body to acknowledge receipt.

    PesaPal sends:
        OrderTrackingId, OrderNotificationType, OrderMerchantReference

    We call handle_ipn_notification() which polls GetTransactionStatus
    and credits the wallet if the payment is SUCCESSFUL.
    """
    try:
        if request.method == 'POST':
            try:
                body = json.loads(request.body)
            except (json.JSONDecodeError, TypeError):
                body = request.POST.dict()
        else:
            body = request.GET.dict()

        order_tracking_id  = body.get('OrderTrackingId', '')
        merchant_reference = body.get('OrderMerchantReference', '')
        notification_type  = body.get('OrderNotificationType', '')

        logger.info(
            'PesaPal IPN received: tracking=%s ref=%s type=%s',
            order_tracking_id, merchant_reference, notification_type,
        )

        if not order_tracking_id:
            return JsonResponse(
                {
                    'orderNotificationType':  notification_type,
                    'orderTrackingId':        order_tracking_id,
                    'orderMerchantReference': merchant_reference,
                    'status': 500,
                },
                status=400,
            )

        from .payment_processor import handle_ipn_notification
        handle_ipn_notification(
            order_tracking_id=order_tracking_id,
            merchant_reference=merchant_reference,
            notification_type=notification_type,
        )

        # PesaPal requires this exact response shape to mark the IPN as acknowledged
        return JsonResponse({
            'orderNotificationType':  notification_type,
            'orderTrackingId':        order_tracking_id,
            'orderMerchantReference': merchant_reference,
            'status': 200,
        })

    except Exception as exc:
        logger.error('IPN handler exception: %s', exc, exc_info=True)
        return JsonResponse({'status': 500, 'message': str(exc)}, status=500)


# ─── Callback ─────────────────────────────────────────────────────────────────

@xframe_options_exempt
def pesapal_callback(request):
    """
    GET /pesapal/callback/

    PesaPal redirects the buyer here after they complete (or abandon)
    payment on the PesaPal page.

    Query params: OrderTrackingId, OrderMerchantReference,
                  OrderNotificationType=CALLBACKURL

    We poll GetTransactionStatus, update PesapalTransaction, credit the
    wallet on success, then render pesapal_callback.html which postMessages
    the result to the parent iframe.
    """
    order_tracking_id  = request.GET.get('OrderTrackingId', '')
    merchant_reference = request.GET.get('OrderMerchantReference', '')

    logger.info(
        'PesaPal callback: tracking=%s ref=%s',
        order_tracking_id, merchant_reference,
    )

    if not order_tracking_id:
        return render(request, 'pesapal_callback.html', {
            'success': False,
            'message': 'Invalid callback — missing OrderTrackingId.',
        })

    try:
        from .payment_processor import PaymentProcessor, complete_pending_deposit
        from .models import Transaction, PesapalTransaction

        processor      = PaymentProcessor()
        status_data    = processor.check_transaction_status(order_tracking_id)
        pesapal_status = status_data.get('status', 'UNKNOWN')

        # Update PesapalTransaction record
        try:
            pt = PesapalTransaction.objects.get(order_tracking_id=order_tracking_id)
            pt.pesapal_status    = pesapal_status
            pt.confirmation_code = status_data.get('confirmation_code', '')
            pt.payment_method    = status_data.get('payment_method', '')
            pt.raw_ipn_response  = status_data.get('raw', {})
            pt.save()
        except PesapalTransaction.DoesNotExist:
            logger.warning('No PesapalTransaction for order_tracking_id=%s', order_tracking_id)

        success = (pesapal_status == 'SUCCESSFUL')

        if success:
            try:
                txn = Transaction.objects.get(
                    mobile_money_reference=merchant_reference,
                    status__in=['pending', 'processing'],
                )
                complete_pending_deposit(
                    transaction_id=str(txn.transaction_id),
                    external_reference=order_tracking_id,
                )
            except Transaction.DoesNotExist:
                logger.warning(
                    'Callback: no pending txn for merchant_ref=%s', merchant_reference
                )

        return render(request, 'pesapal_callback.html', {
            'success':            success,
            'pesapal_status':     pesapal_status,
            'order_tracking_id':  order_tracking_id,
            'merchant_reference': merchant_reference,
            'confirmation_code':  status_data.get('confirmation_code', ''),
            'payment_method':     status_data.get('payment_method', ''),
            'amount':             status_data.get('amount', ''),
            'currency':           status_data.get('currency', 'UGX'),
            'message': (
                'Payment received! Your wallet has been credited.'
                if success else
                f'Payment status: {pesapal_status}. Contact support if you believe this is an error.'
            ),
        })

    except Exception as exc:
        logger.error('Callback processing error: %s', exc, exc_info=True)
        return render(request, 'pesapal_callback.html', {
            'success': False,
            'message': 'An error occurred processing your payment. Please contact support.',
        })


# ─── Iframe renderer ──────────────────────────────────────────────────────────

@xframe_options_exempt
def pesapal_iframe(request, merchant_reference):
    """
    GET /pesapal/iframe/<merchant_reference>/

    Renders the PesaPal payment iframe for a pending deposit.
    The deposit_pin view creates the PesapalTransaction record and
    returns this URL; the payment template loads it in an <iframe>.
    """
    from .models import PesapalTransaction

    try:
        pt = PesapalTransaction.objects.get(merchant_reference=merchant_reference)
    except PesapalTransaction.DoesNotExist:
        return render(request, 'pesapal_iframe.html', {
            'error':   True,
            'message': 'Payment session not found. Please start a new deposit.',
        })

    return render(request, 'pesapal_iframe.html', {
        'redirect_url':       pt.redirect_url,
        'merchant_reference': merchant_reference,
        'amount':             pt.amount,
        'currency':           pt.currency,
        'error':              False,
    })


# ─── Sandbox simulate (DEBUG only) ────────────────────────────────────────────

@csrf_exempt
def simulate_pesapal_callback(request, merchant_reference):
    """
    POST /pesapal/simulate/<merchant_reference>/

    Simulates PesaPal confirming a payment — for sandbox testing only.
    Marks the PesapalTransaction as SUCCESSFUL and credits the wallet,
    bypassing the real PesaPal payment step.

    Only available when DEBUG=True. Returns 403 in production.
    """
    if not settings.DEBUG:
        return JsonResponse({'error': 'Not available in production'}, status=403)

    from .payment_processor import complete_pending_deposit
    from .models import Transaction, PesapalTransaction

    try:
        try:
            pt = PesapalTransaction.objects.get(merchant_reference=merchant_reference)
            pt.pesapal_status    = 'SUCCESSFUL'
            pt.confirmation_code = f'SANDBOX-{merchant_reference[:8].upper()}'
            pt.payment_method    = 'Sandbox'
            pt.save()
            order_tracking_id = pt.order_tracking_id or merchant_reference
        except PesapalTransaction.DoesNotExist:
            order_tracking_id = merchant_reference

        try:
            txn = Transaction.objects.get(
                mobile_money_reference=merchant_reference,
                status__in=['pending', 'processing'],
            )
            result = complete_pending_deposit(
                transaction_id=str(txn.transaction_id),
                external_reference=order_tracking_id,
            )
            logger.info('Sandbox simulate: %s', result)
            return JsonResponse({'success': True, 'result': result})

        except Transaction.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': f'No pending transaction for merchant_reference={merchant_reference}',
            }, status=404)

    except Exception as exc:
        logger.error('simulate_pesapal_callback error: %s', exc)
        return JsonResponse({'error': str(exc)}, status=500)


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
