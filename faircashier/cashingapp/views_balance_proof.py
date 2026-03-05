"""
Payment App — views_balance_proof.py 

Generates Groth16 balance proofs. Returns proof+public_signals so
Shopping App can independently verify via Strapi.
Buyer wallet balance NEVER leaves this app (except to Strapi for proof).
"""

import hashlib
import json
import logging
import time
from decimal import Decimal

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import BalanceProof, Wallet
from .zkp_client import ZKPClient

logger = logging.getLogger(__name__)


def _build_order_hash(order_id, seller_email):
    raw = f"{order_id}:{seller_email}"
    h = hashlib.sha256(raw.encode()).hexdigest()
    return str(int(h, 16) % (2 ** 253))


def _binary_bracket(total, balance, min_step=Decimal('0.01')):
    if balance <= 0 or total <= 0:
        return 0
    bracket = total
    while bracket > balance and bracket > min_step:
        bracket = bracket / 2
    return int(bracket)


def _tier_items(items, balance, include_cod=False):
    eligible = []
    for item in items:
        pm = item.get('payment_method', 'online').lower()
        if pm in ('online', 'online-pending'):
            eligible.append(item)
        elif include_cod and pm in ('cash', 'cash-pending', 'cod'):
            eligible.append(item)

    if not eligible:
        return {'tier_result': 'unknown', 'items_payable': 0, 'total_items': 0,
                'binary_bracket': 0, 'item_details': []}

    eligible.sort(key=lambda x: Decimal(str(x.get('amount', 0))))
    total_amount = sum(Decimal(str(i.get('amount', 0))) for i in eligible)
    bracket = _binary_bracket(total_amount, balance)

    running = Decimal('0')
    payable = 0
    details = []
    for item in eligible:
        amt = Decimal(str(item.get('amount', 0)))
        can_pay = (running + amt) <= balance
        if can_pay:
            running += amt
            payable += 1
        details.append({'shopping_order_item_id': item.get('shopping_order_item_id'),
                        'amount': float(amt), 'payable': can_pay})

    total = len(eligible)
    tier = 'green' if payable == total else ('amber' if payable > 0 else 'red')

    return {'tier_result': tier, 'items_payable': payable, 'total_items': total,
            'binary_bracket': bracket, 'item_details': details}


def _get_buyer_balance(buyer_email):
    try:
        return Wallet.objects.get(user__email=buyer_email).free_balance
    except Wallet.DoesNotExist:
        return Decimal('0')


def _generate_and_store_proof(order_id, seller_email, buyer_email, items,
                               include_cod=False, is_refresh=False):
    balance = _get_buyer_balance(buyer_email)
    balance_int = int(balance)
    tier_info = _tier_items(items, balance, include_cod)

    order_hash = _build_order_hash(order_id, seller_email)
    timestamp = int(time.time())
    required_amount = tier_info['binary_bracket']

    client = ZKPClient()
    try:
        proof_result = client.generate_balance_proof(
            balance=balance_int, required_amount=required_amount,
            order_hash=order_hash, timestamp=timestamp)
    except Exception as e:
        logger.error(f"Balance proof gen failed: order={order_id}, seller={seller_email}: {e}")
        return {'error': 'Proof generation failed', 'tier_result': tier_info['tier_result'],
                'items_payable': tier_info['items_payable'], 'total_items': tier_info['total_items']}

    proof = proof_result.get('proof')
    public_signals = proof_result.get('publicSignals')
    if not proof or not public_signals:
        return {'error': 'Empty proof', 'tier_result': tier_info['tier_result']}

    now = timezone.now()
    expires = now + timezone.timedelta(hours=24)

    bp, _ = BalanceProof.objects.update_or_create(
        order_id=order_id, seller_email=seller_email,
        defaults={
            'buyer_email': buyer_email, 'order_hash': order_hash,
            'proof': proof, 'public_signals': public_signals, 'verified': True,
            'tier_result': tier_info['tier_result'], 'items_payable': tier_info['items_payable'],
            'total_items': tier_info['total_items'], 'binary_bracket': required_amount,
            'generated_at': now, 'expires_at': expires, 'include_cod': include_cod,
            'refresh_count': (BalanceProof.objects.filter(
                order_id=order_id, seller_email=seller_email
            ).values_list('refresh_count', flat=True).first() or 0) + (1 if is_refresh else 0),
        })

    logger.info(f"Balance proof {'refreshed' if is_refresh else 'generated'}: "
                f"order={order_id}, seller={seller_email}, tier={tier_info['tier_result']}")

    return {
        'success': True, 'proof_id': bp.id, 'order_id': order_id,
        'seller_email': seller_email, 'tier_result': tier_info['tier_result'],
        'items_payable': tier_info['items_payable'], 'total_items': tier_info['total_items'],
        'binary_bracket': required_amount, 'include_cod': include_cod,
        'generated_at': now.isoformat(), 'expires_at': expires.isoformat(),
        'proof': proof, 'public_signals': public_signals,  # ← Shopping App verifies these
    }


# ─── Internal endpoints (called by Shopping App) ───

@csrf_exempt
@require_http_methods(["POST"])
def internal_order_created(request):
    """POST /internal/order-created/ — generates one proof per seller."""
    if request.headers.get('X-Internal-Secret', '') != settings.SHOPPING_APP_INTERNAL_SECRET:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    body = json.loads(request.body)
    order_id = body.get('order_id')
    buyer_email = body.get('buyer_email', '').strip().lower()
    sellers = body.get('sellers', [])

    if not order_id or not buyer_email or not sellers:
        return JsonResponse({'error': 'order_id, buyer_email, sellers required'}, status=400)

    proofs = []
    for s in sellers:
        se = s.get('seller_email', '').strip().lower()
        items = s.get('items', [])
        if not se or not items:
            continue

        result = _generate_and_store_proof(order_id, se, buyer_email, items)
        proofs.append({
            'seller_email': se, 'proof_id': result.get('proof_id'),
            'tier_result': result.get('tier_result', 'unknown'),
            'items_payable': result.get('items_payable', 0),
            'total_items': result.get('total_items', 0),
            'binary_bracket': result.get('binary_bracket', 0),
            'generated_at': result.get('generated_at'),
            'expires_at': result.get('expires_at'),
            'proof': result.get('proof'), 'public_signals': result.get('public_signals'),
            'error': result.get('error'),
        })

    return JsonResponse({'order_id': order_id, 'proofs': proofs})


@csrf_exempt
@require_http_methods(["POST"])
def internal_balance_proof_refresh(request):
    """POST /internal/balance-proof/refresh/ — re-generate proof."""
    if request.headers.get('X-Internal-Secret', '') != settings.SHOPPING_APP_INTERNAL_SECRET:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    body = json.loads(request.body)
    order_id = body.get('order_id')
    seller_email = body.get('seller_email', '').strip().lower()
    include_cod = body.get('include_cod', False)
    items = body.get('items', [])

    if not order_id or not seller_email or not items:
        return JsonResponse({'error': 'order_id, seller_email, items required'}, status=400)

    try:
        existing = BalanceProof.objects.get(order_id=order_id, seller_email=seller_email)
        buyer_email = existing.buyer_email
    except BalanceProof.DoesNotExist:
        return JsonResponse({'error': 'No existing proof for this order+seller'}, status=404)

    result = _generate_and_store_proof(order_id, seller_email, buyer_email, items,
                                        include_cod=include_cod, is_refresh=True)

    return JsonResponse({
        'order_id': order_id, 'seller_email': seller_email,
        'proof_id': result.get('proof_id'),
        'tier_result': result.get('tier_result', 'unknown'),
        'items_payable': result.get('items_payable', 0),
        'total_items': result.get('total_items', 0),
        'refreshed_at': result.get('generated_at'),
        'expires_at': result.get('expires_at'), 'include_cod': include_cod,
        'proof': result.get('proof'), 'public_signals': result.get('public_signals'),
        'error': result.get('error'),
    })


@csrf_exempt
@require_http_methods(["GET"])
def internal_balance_proof_fetch(request):
    """GET /internal/balance-proof/?order_id=X&seller_email=Y"""
    if request.headers.get('X-Internal-Secret', '') != settings.SHOPPING_APP_INTERNAL_SECRET:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    order_id = request.GET.get('order_id', '')
    seller_email = request.GET.get('seller_email', '').strip().lower()
    if not order_id or not seller_email:
        return JsonResponse({'error': 'order_id and seller_email required'}, status=400)

    try:
        bp = BalanceProof.objects.get(order_id=order_id, seller_email=seller_email)
    except BalanceProof.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    return JsonResponse({
        'order_id': bp.order_id, 'seller_email': bp.seller_email,
        'tier_result': bp.tier_result, 'items_payable': bp.items_payable,
        'total_items': bp.total_items, 'binary_bracket': bp.binary_bracket,
        'include_cod': bp.include_cod,
        'generated_at': bp.generated_at.isoformat() if bp.generated_at else None,
        'expires_at': bp.expires_at.isoformat() if bp.expires_at else None,
        'is_expired': bp.expires_at and bp.expires_at < timezone.now(),
        'refresh_count': bp.refresh_count,
        'proof': bp.proof, 'public_signals': bp.public_signals,
        # NOTE: balance NEVER returned, buyer_email NEVER returned
    })