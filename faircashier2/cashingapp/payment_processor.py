"""
Fair Cashier Payment Processor — PesaPal v3
============================================

Handles all real-money movement between buyer phone/card and the
admin merchant PesaPal account (collections), and from the merchant
account to seller phone/card (disbursements).

PesaPal is a unified gateway: it routes MTN, Airtel, and Visa
internally. No provider is selected here — the buyer picks on the
PesaPal iframe.

Two-level money flow
--------------------
1. PesaPal layer  — moves real UGX: buyer ↔ admin merchant account.
2. Digital wallet — Fair Cashier internal ledger (balance /
   reserved_balance). Wallet credits/debits happen only AFTER
   PesaPal confirms via IPN or callback status poll.

Disbursements
-------------
PesaPal Uganda sandbox does not expose a P2P disbursement API.
Seller cashouts are therefore handled by the admin
approve → CSV export → bulk-portal workflow (unchanged).
When PesaPal Uganda adds a live disbursement endpoint, add it in
_pesapal_disburse() below and call it from request_disbursement().
"""

import uuid
import logging
import requests as http_requests

from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.core.cache import cache
from django.db import transaction as db_transaction

logger = logging.getLogger(__name__)


# ─── Environment ──────────────────────────────────────────────────────────────

def _is_sandbox() -> bool:
    return getattr(settings, 'PESAPAL_ENVIRONMENT', 'sandbox').lower() == 'sandbox'


def _base_url() -> str:
    if _is_sandbox():
        return 'https://cybqa.pesapal.com/pesapalv3'
    return 'https://pay.pesapal.com/v3'


# ─── Token management ─────────────────────────────────────────────────────────

_TOKEN_CACHE_KEY = 'pesapal_access_token'
_TOKEN_TTL = 270  # 4.5 min — token lives 5 min


def _get_access_token() -> str:
    """Fetch or return cached PesaPal bearer token."""
    cached = cache.get(_TOKEN_CACHE_KEY)
    if cached:
        return cached

    consumer_key    = getattr(settings, 'CONSUMER_KEY', '')
    consumer_secret = getattr(settings, 'CONSUMER_SECRET', '')

    if not consumer_key or not consumer_secret:
        raise ValueError('CONSUMER_KEY and CONSUMER_SECRET must be set in settings / .env')

    url  = f'{_base_url()}/api/Auth/RequestToken'
    resp = http_requests.post(
        url,
        json={'consumer_key': consumer_key, 'consumer_secret': consumer_secret},
        headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
        timeout=15,
    )
    resp.raise_for_status()
    data  = resp.json()
    token = data.get('token')
    if not token:
        raise RuntimeError(f'PesaPal token request failed: {data}')

    cache.set(_TOKEN_CACHE_KEY, token, _TOKEN_TTL)
    logger.info('PesaPal access token refreshed.')
    return token


def _auth_headers() -> dict:
    return {
        'Accept':        'application/json',
        'Content-Type':  'application/json',
        'Authorization': f'Bearer {_get_access_token()}',
    }


# ─── IPN registration ─────────────────────────────────────────────────────────

_IPN_ID_CACHE_KEY = 'pesapal_ipn_id'


def get_or_register_ipn() -> str:
    """
    Return the cached IPN ID, or register PESAPAL_IPN_URL with PesaPal
    and cache the resulting IPN ID for 24 hours.
    """
    cached = cache.get(_IPN_ID_CACHE_KEY)
    if cached:
        return cached

    ipn_url = getattr(settings, 'PESAPAL_IPN_URL', '')
    if not ipn_url:
        raise ValueError('PESAPAL_IPN_URL must be set in settings / .env')

    url  = f'{_base_url()}/api/URLSetup/RegisterIPN'
    resp = http_requests.post(
        url,
        json={'url': ipn_url, 'ipn_notification_type': 'POST'},
        headers=_auth_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    data   = resp.json()
    ipn_id = data.get('ipn_id')
    if not ipn_id:
        raise RuntimeError(f'IPN registration failed: {data}')

    cache.set(_IPN_ID_CACHE_KEY, ipn_id, 86400)
    logger.info('PesaPal IPN registered: %s → id=%s', ipn_url, ipn_id)
    return ipn_id


# ─── PaymentProcessor ─────────────────────────────────────────────────────────

class PaymentProcessor:
    """
    PesaPal v3 payment processor.

    Public method signatures are compatible with the old mock so that
    all existing callers (deposit_pin, cashout_pin, deposit_and_pay,
    process_payment_items, …) work without modification.

    api_key / provider parameters are accepted but ignored — PesaPal
    credentials come from settings and routing is handled by PesaPal
    internally.
    """

    def __init__(self, api_key=None, provider='pesapal'):
        self.api_key               = api_key   # kept for call-site compat
        self.provider              = provider  # kept for call-site compat
        self.transaction_reference = None

    # ── Collection ────────────────────────────────────────────────────────────

    def request_collection(self, phone_number: str, amount: float,
                           description: str = 'Deposit') -> dict:
        """
        Submit a PesaPal SubmitOrderRequest for a buyer payment
        (buyer → admin merchant account).

        Returns a normalised dict with:
          status, provider, operation, reference_id, order_tracking_id,
          redirect_url, message, details, api_response_code, timestamp,
          next_action
        """
        self.transaction_reference = str(uuid.uuid4())
        return self._pesapal_submit_order(
            merchant_ref=self.transaction_reference,
            amount=amount,
            currency='UGX',
            description=description,
            phone_number=phone_number,
            operation='collection',
        )

    # ── Disbursement ──────────────────────────────────────────────────────────

    def request_disbursement(self, phone_number: str, amount: float,
                              description: str = 'Cashout') -> dict:
        """
        Initiate a disbursement from the admin merchant account to a
        seller's mobile money number.

        PesaPal Uganda sandbox does not yet have a disbursement API.
        This method raises NotImplementedError so callers fail clearly
        rather than silently. In production, implement _pesapal_disburse()
        and call it here.

        Seller cashouts use the admin CashoutRequest workflow instead
        (approve → CSV export → upload to MTN/Airtel bulk portal).
        """
        raise NotImplementedError(
            'PesaPal Uganda does not yet support API disbursements. '
            'Use the admin cashout-request → CSV export → bulk portal workflow.'
        )

    # ── Transaction status ────────────────────────────────────────────────────

    def check_transaction_status(self, order_tracking_id: str) -> dict:
        """
        GET /api/Transactions/GetTransactionStatus

        Returns a normalised dict with:
          status (SUCCESSFUL | FAILED | INVALID | REVERSED | PENDING | UNKNOWN),
          reference_id, pesapal_status_code, payment_method, confirmation_code,
          payment_account, amount, currency, merchant_reference, raw, reason,
          timestamp
        """
        return self._pesapal_get_transaction_status(order_tracking_id)

    # ── Core API calls ────────────────────────────────────────────────────────

    def _pesapal_submit_order(self, merchant_ref: str, amount: float,
                               currency: str, description: str,
                               phone_number: str, operation: str) -> dict:
        """POST /api/Transactions/SubmitOrderRequest"""
        try:
            ipn_id       = get_or_register_ipn()
            callback_url = getattr(settings, 'PESAPAL_CALLBACK_URL', '')
            if not callback_url:
                raise ValueError('PESAPAL_CALLBACK_URL must be set in settings / .env')

            payload = {
                'id':              merchant_ref,
                'currency':        currency,
                'amount':          amount,
                'description':     description[:100],
                'callback_url':    callback_url,
                'notification_id': ipn_id,
                'branch':          getattr(settings, 'PESAPAL_BRANCH', 'Fair Cashier'),
                'billing_address': {
                    'phone_number':  phone_number,
                    'country_code':  'UG',
                    'email_address': '',
                    'first_name':    '',
                    'middle_name':   '',
                    'last_name':     '',
                    'line_1':        '',
                    'line_2':        '',
                    'city':          '',
                    'state':         '',
                    'postal_code':   '',
                    'zip_code':      '',
                },
            }

            url  = f'{_base_url()}/api/Transactions/SubmitOrderRequest'
            resp = http_requests.post(url, json=payload, headers=_auth_headers(), timeout=20)
            resp.raise_for_status()
            data = resp.json()

            if data.get('status') == '200' or data.get('error') is None:
                order_tracking_id = data.get('order_tracking_id', '')
                redirect_url      = data.get('redirect_url', '')

                self._save_pesapal_transaction(
                    merchant_ref=merchant_ref,
                    order_tracking_id=order_tracking_id,
                    redirect_url=redirect_url,
                    amount=amount,
                    currency=currency,
                    phone_number=phone_number,
                    operation=operation,
                    raw_response=data,
                )

                return {
                    'status':            'success',
                    'provider':          'PesaPal',
                    'operation':         operation,
                    'reference_id':      merchant_ref,
                    'order_tracking_id': order_tracking_id,
                    'redirect_url':      redirect_url,
                    'message':           'Order submitted — redirect buyer to payment page',
                    'details': {
                        'amount':   amount,
                        'currency': currency,
                        'payer':    {'partyIdType': 'MSISDN', 'partyId': phone_number},
                        'status':   'PENDING',
                        'reason':   None,
                    },
                    'api_response_code': '200',
                    'timestamp':         timezone.now().isoformat(),
                    'next_action':       f'Redirect or iframe: {redirect_url}',
                }

            error_obj = data.get('error') or {}
            return self._build_error(
                message=error_obj.get('message', 'Order submission failed'),
                error_code=str(error_obj.get('code', 'PESAPAL_ERROR')),
            )

        except (http_requests.RequestException, ValueError, RuntimeError) as exc:
            logger.error('PesaPal SubmitOrderRequest failed: %s', exc)
            return self._build_error(str(exc), error_code='NETWORK_ERROR')

    def _pesapal_get_transaction_status(self, order_tracking_id: str) -> dict:
        """GET /api/Transactions/GetTransactionStatus?orderTrackingId=…"""
        try:
            url  = (
                f'{_base_url()}/api/Transactions/GetTransactionStatus'
                f'?orderTrackingId={order_tracking_id}'
            )
            resp = http_requests.get(url, headers=_auth_headers(), timeout=15)
            resp.raise_for_status()
            data = resp.json()

            # status_code: 1=COMPLETED, 0=INVALID, 2=FAILED, 3=REVERSED
            try:
                status_code = int(data.get('payment_status_code') or data.get('status_code', -1))
            except (TypeError, ValueError):
                status_code = -1

            status_map = {1: 'SUCCESSFUL', 0: 'INVALID', 2: 'FAILED', 3: 'REVERSED'}
            normalised = status_map.get(status_code, 'PENDING')

            return {
                'status':              normalised,
                'reference_id':        order_tracking_id,
                'pesapal_status_code': status_code,
                'payment_method':      data.get('payment_method', ''),
                'confirmation_code':   data.get('confirmation_code', ''),
                'payment_account':     data.get('payment_account', ''),
                'amount':              data.get('amount'),
                'currency':            data.get('currency', 'UGX'),
                'merchant_reference':  data.get('merchant_reference', ''),
                'raw':                 data,
                'reason':              data.get('payment_status_description'),
                'timestamp':           timezone.now().isoformat(),
            }

        except (http_requests.RequestException, ValueError) as exc:
            logger.error('PesaPal GetTransactionStatus failed: %s', exc)
            return {
                'status':       'UNKNOWN',
                'reference_id': order_tracking_id,
                'reason':       str(exc),
                'timestamp':    timezone.now().isoformat(),
            }

    # ── DB persistence ────────────────────────────────────────────────────────

    @staticmethod
    def _save_pesapal_transaction(merchant_ref, order_tracking_id, redirect_url,
                                   amount, currency, phone_number,
                                   operation, raw_response):
        """Upsert a PesapalTransaction row. Never raises — failures are logged."""
        try:
            from .models import PesapalTransaction
            PesapalTransaction.objects.update_or_create(
                merchant_reference=merchant_ref,
                defaults={
                    'order_tracking_id':   order_tracking_id,
                    'redirect_url':        redirect_url,
                    'amount':              Decimal(str(amount)),
                    'currency':            currency,
                    'phone_number':        phone_number,
                    'operation_type':      operation,
                    'pesapal_status':      'PENDING',
                    'raw_submit_response': raw_response,
                },
            )
        except Exception as exc:
            logger.warning('Could not save PesapalTransaction: %s', exc)

    # ── Error helper ──────────────────────────────────────────────────────────

    def _build_error(self, message: str, error_code: str = 'TRANSACTION_FAILED') -> dict:
        return {
            'status':       'error',
            'provider':     'PesaPal',
            'error_code':   error_code,
            'message':      message,
            'reference_id': self.transaction_reference,
            'timestamp':    timezone.now().isoformat(),
            'details': {
                'description': _error_description(error_code),
                'resolution':  'Please try again or contact support',
            },
        }


# ─── High-level orchestration functions ───────────────────────────────────────

def process_deposit(user, platform, amount, phone_number):
    """
    Initiate a buyer wallet top-up via PesaPal.

    Flow:
      1. Submit PesaPal SubmitOrderRequest.
      2. Create Transaction (status='pending') + MobileMoneyTransaction.
      3. Return redirect_url — the view displays the PesaPal iframe.
      4. Wallet is credited later by complete_pending_deposit(), which
         is called from pesapal_callback or handle_ipn_notification().

    Return shape:
      { status, message, transaction_id, reference_id,
        order_tracking_id, redirect_url, next_action, provider }
    """
    from .models import Transaction, MobileMoneyTransaction

    try:
        processor = PaymentProcessor(
            api_key=getattr(platform, 'mobile_money_api_key', None),
        )

        with db_transaction.atomic():
            transaction = Transaction.objects.create(
                platform=platform,
                to_wallet=user.wallet,
                amount=amount,
                transaction_type='deposit',
                status='pending',
                description=f'Deposit via PesaPal from {phone_number}',
            )

            api_response = processor.request_collection(
                phone_number=phone_number,
                amount=float(amount),
                description='Deposit to Fair Cashier',
            )

            MobileMoneyTransaction.objects.create(
                platform=platform,
                transaction=transaction,
                operation_type='collection',
                phone_number=phone_number,
                amount=amount,
                external_reference=api_response.get('reference_id', str(uuid.uuid4())),
                api_response=api_response,
                status='pending' if api_response['status'] == 'success' else 'failed',
            )

            if api_response['status'] == 'success':
                transaction.status                = 'processing'
                transaction.mobile_money_reference = api_response.get('reference_id')
                transaction.save()

                logger.info('Deposit initiated: %s UGX for %s', amount, user.email)
                return {
                    'status':            'success',
                    'message':           'Deposit initiated — complete payment on PesaPal',
                    'transaction_id':    str(transaction.transaction_id),
                    'reference_id':      api_response.get('reference_id'),
                    'order_tracking_id': api_response.get('order_tracking_id', ''),
                    'redirect_url':      api_response.get('redirect_url', ''),
                    'next_action':       api_response.get('next_action', 'Complete payment on PesaPal'),
                    'provider':          'PesaPal',
                }

            transaction.status = 'failed'
            transaction.save()
            logger.error('Deposit failed for %s: %s', user.email, api_response.get('message'))
            return {
                'status':         'error',
                'message':        api_response.get('message', 'Deposit failed'),
                'error_code':     api_response.get('error_code'),
                'transaction_id': str(transaction.transaction_id),
            }

    except Exception as exc:
        logger.error('process_deposit error: %s', exc)
        return {'status': 'error', 'message': 'Failed to process deposit', 'error': str(exc)}


def process_cashout(user, platform, amount, phone_number):
    """
    Seller cashout — not handled via direct PesaPal API.

    Seller cashouts go through the admin CashoutRequest workflow:
      seller submits request → admin approves → admin exports CSV
      → uploads to MTN/Airtel/Bank bulk payment portal.

    This function is retained for import compatibility but raises
    clearly so any caller that bypasses the CashoutRequest workflow
    fails loudly.
    """
    raise NotImplementedError(
        'Direct cashouts are not supported. '
        'Use seller_request_cashout → admin review → bulk disbursement.'
    )


def complete_pending_deposit(transaction_id, external_reference):
    """
    Credit the wallet for a confirmed PesaPal payment.

    Called by:
      - handle_ipn_notification()  when PesaPal sends IPN
      - pesapal_callback view      when buyer is redirected back
      - simulate_pesapal_callback  in sandbox (DEBUG only)

    Idempotent: if the transaction is already 'completed' it returns
    success without double-crediting.
    """
    from .models import Transaction, MobileMoneyTransaction

    try:
        with db_transaction.atomic():
            transaction = Transaction.objects.select_for_update().get(
                transaction_id=transaction_id
            )

            if transaction.status == 'completed':
                return {
                    'status':  'success',
                    'message': 'Already completed',
                    'transaction_id': str(transaction.transaction_id),
                    'amount':         str(transaction.amount),
                    'new_balance':    str(transaction.to_wallet.balance),
                }

            if transaction.status not in ('processing', 'pending'):
                return {
                    'status':  'error',
                    'message': f'Transaction not in processable state: {transaction.status}',
                }

            wallet = transaction.to_wallet
            wallet.balance += transaction.amount
            wallet.save()

            transaction.status = 'completed'
            transaction.save()

            mm = transaction.mobile_money_transaction.first()
            if mm:
                mm.status = 'successful'
                mm.save()

            logger.info('Deposit completed: %s UGX (txn=%s)', transaction.amount, transaction_id)
            return {
                'status':         'success',
                'message':        'Deposit completed successfully',
                'transaction_id': str(transaction.transaction_id),
                'amount':         str(transaction.amount),
                'new_balance':    str(wallet.balance),
            }

    except Transaction.DoesNotExist:
        return {'status': 'error', 'message': 'Transaction not found'}
    except Exception as exc:
        logger.error('complete_pending_deposit error: %s', exc)
        return {'status': 'error', 'message': str(exc)}


# ─── IPN handler ──────────────────────────────────────────────────────────────

def handle_ipn_notification(order_tracking_id: str, merchant_reference: str,
                              notification_type: str) -> dict:
    """
    Process an IPN notification posted by PesaPal.

    Steps:
      1. Poll GetTransactionStatus to get the confirmed status.
      2. Update PesapalTransaction record.
      3. If SUCCESSFUL → complete_pending_deposit (credit wallet).

    Returns {status, message} for the IPN HTTP response.
    """
    from .models import Transaction, PesapalTransaction

    logger.info(
        'IPN: order_tracking_id=%s merchant_ref=%s type=%s',
        order_tracking_id, merchant_reference, notification_type,
    )

    try:
        processor   = PaymentProcessor()
        status_data = processor.check_transaction_status(order_tracking_id)

        try:
            pt = PesapalTransaction.objects.get(order_tracking_id=order_tracking_id)
            pt.pesapal_status    = status_data.get('status', 'UNKNOWN')
            pt.confirmation_code = status_data.get('confirmation_code', '')
            pt.payment_method    = status_data.get('payment_method', '')
            pt.raw_ipn_response  = status_data.get('raw', {})
            pt.save()
        except PesapalTransaction.DoesNotExist:
            logger.warning('No PesapalTransaction for order_tracking_id=%s', order_tracking_id)

        if status_data.get('status') == 'SUCCESSFUL':
            try:
                txn = Transaction.objects.get(
                    mobile_money_reference=merchant_reference,
                    status__in=['pending', 'processing'],
                )
                result = complete_pending_deposit(
                    transaction_id=str(txn.transaction_id),
                    external_reference=order_tracking_id,
                )
                logger.info('IPN wallet credit result: %s', result)
                return {'status': 'success', 'message': 'Payment confirmed and wallet credited'}
            except Transaction.DoesNotExist:
                logger.warning('No pending txn for merchant_ref=%s', merchant_reference)
                return {'status': 'ok', 'message': 'Transaction not found or already completed'}

        return {'status': 'ok', 'message': f'IPN received — status={status_data.get("status")}'}

    except Exception as exc:
        logger.error('IPN handler error: %s', exc)
        return {'status': 'error', 'message': str(exc)}


# ─── Refund ───────────────────────────────────────────────────────────────────

def request_pesapal_refund(confirmation_code: str, amount: float,
                            username: str, remarks: str) -> dict:
    """
    POST /api/Transactions/RefundRequest

    Call this from dispute resolution when the original payment was made
    via PesaPal (PesapalTransaction exists with SUCCESSFUL status).

    Limitations (from PesaPal docs):
      - Only COMPLETED payments can be refunded.
      - Mobile money: full refund only.
      - Card: full or partial refund.
      - One refund per payment.
    """
    try:
        url  = f'{_base_url()}/api/Transactions/RefundRequest'
        resp = http_requests.post(
            url,
            json={
                'confirmation_code': confirmation_code,
                'amount':            amount,
                'username':          username,
                'remarks':           remarks,
            },
            headers=_auth_headers(),
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info('PesaPal refund response: %s', data)
        return data
    except Exception as exc:
        logger.error('PesaPal refund error: %s', exc)
        return {'status': 'error', 'message': str(exc), 'error': 500}


# ─── Utilities ────────────────────────────────────────────────────────────────

def _error_description(error_code: str) -> str:
    descriptions = {
        'PAYER_NOT_FOUND':       'The mobile money account was not found',
        'NOT_ENOUGH_FUNDS':      'Insufficient funds in the account',
        'INVALID_MSISDN':        'Invalid phone number format',
        'TRANSACTION_FAILED':    'Transaction could not be completed',
        'INSUFFICIENT_BALANCE':  'Insufficient balance for this transaction',
        'TIMEOUT':               'Transaction timed out',
        'DUPLICATE_TRANSACTION': 'Duplicate transaction detected',
        'NETWORK_ERROR':         'Could not reach PesaPal servers',
        'PESAPAL_ERROR':         'PesaPal returned an error',
    }
    return descriptions.get(error_code, 'Unknown error occurred')
