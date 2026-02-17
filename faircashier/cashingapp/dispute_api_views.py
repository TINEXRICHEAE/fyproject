# File: cashingapp/dispute_api_views.py   (NEW FILE)

import json
import logging
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db import transaction as db_transaction
from django.conf import settings
import requests as http_requests

from .models import (
    Users, Platform, Wallet, Transaction,
    PaymentRequest, PaymentRequestItem, Dispute, ActivityLog,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  RECEIVE DISPUTE FROM SHOPPING APP
# ═══════════════════════════════════════════════════════════════════════════

@csrf_exempt
@require_http_methods(["POST"])
def create_dispute_from_shopping(request):
    """
    API endpoint called by the shopping app to forward a dispute to the payment app.

    When a buyer disputes an online-paid item, the shopping app sends:
    {
        "api_key": "...",
        "shopping_dispute_id": 123,
        "buyer_email": "buyer@example.com",
        "buyer_phone": "+256700000000",
        "seller_email": "seller@example.com",
        "seller_phone": "+256711111111",
        "order_number": "ORD-20260216-ABC123",
        "item_description": "Blue T-Shirt XL",
        "amount": "50000.00",
        "reason": "damaged",
        "reason_display": "Item is damaged",
        "description": "Box was crushed, item torn",
        "metadata": {
            "order_id": 45,
            "order_item_id": 78,
            "product_name": "Blue T-Shirt XL",
            "quantity": 2
        }
    }

    This endpoint:
      1. Validates the API key and finds the platform
      2. Finds or creates buyer/seller users in payment app
      3. Finds the matching PaymentRequestItem (by seller_email + order metadata)
      4. Creates a Dispute record
      5. Marks the seller's funds as held (not withdrawable)
      6. Returns the dispute ID to the shopping app
    """
    try:
        data = json.loads(request.body)

        # Validate API key → find platform
        api_key = data.get('api_key')
        try:
            platform = Platform.objects.get(api_key=api_key, is_active=True)
        except Platform.DoesNotExist:
            return JsonResponse({'error': 'Invalid API key'}, status=403)

        buyer_email = data.get('buyer_email')
        seller_email = data.get('seller_email')
        amount = Decimal(data.get('amount', '0'))
        reason = data.get('reason', 'other')
        reason_display = data.get('reason_display', reason)
        description = data.get('description', '')
        shopping_dispute_id = data.get('shopping_dispute_id')
        order_number = data.get('order_number', '')
        metadata = data.get('metadata', {})

        if not all([buyer_email, seller_email, amount]):
            return JsonResponse({'error': 'buyer_email, seller_email, and amount are required'}, status=400)

        with db_transaction.atomic():
            # Find or get buyer in payment app
            buyer = Users.objects.filter(email=buyer_email).first()
            if not buyer:
                logger.warning(f"Buyer {buyer_email} not found in payment app, creating placeholder")
                buyer = Users.objects.create_user(
                    email=buyer_email,
                    role='buyer',
                )
                # Create wallet
                Wallet.objects.create(user=buyer)

            # Update buyer phone if provided
            buyer_phone = data.get('buyer_phone')
            if buyer_phone and not buyer.phone_number:
                buyer.phone_number = buyer_phone
                buyer.save()

            # Find seller
            seller = Users.objects.filter(email=seller_email).first()
            if not seller:
                return JsonResponse({'error': f'Seller {seller_email} not found in payment app'}, status=404)

            # Update seller phone if provided
            seller_phone = data.get('seller_phone')
            if seller_phone and not seller.phone_number:
                seller.phone_number = seller_phone
                seller.save()

            # Find the matching PaymentRequestItem
            # Look for items matching seller_email and amount from the same order
            payment_item = None
            order_id = metadata.get('order_id')

            if order_id:
                # Try to find by order metadata
                payment_items = PaymentRequestItem.objects.filter(
                    payment_request__platform=platform,
                    seller_email=seller_email,
                    payment_request__metadata__order_id=order_id,
                ).order_by('-created_at')

                if payment_items.exists():
                    payment_item = payment_items.first()

            if not payment_item:
                # Fallback: find by seller + buyer + approximate amount
                payment_items = PaymentRequestItem.objects.filter(
                    payment_request__platform=platform,
                    seller_email=seller_email,
                    payment_request__buyer_email=buyer_email,
                    amount__gte=amount - Decimal('1'),
                    amount__lte=amount + Decimal('1'),
                ).order_by('-created_at')

                if payment_items.exists():
                    payment_item = payment_items.first()

            if not payment_item:
                logger.error(
                    f"No PaymentRequestItem found for seller={seller_email}, "
                    f"buyer={buyer_email}, amount={amount}"
                )
                return JsonResponse({
                    'error': 'No matching payment record found',
                    'details': 'Could not find the corresponding payment request item'
                }, status=404)

            # Map shopping app reason to payment app reason
            reason_mapping = {
                'not_as_ordered': 'wrong_item',
                'damaged': 'damaged_item',
                'wrong_details': 'wrong_item',
                'wrong_quantity': 'incomplete_delivery',
                'inconsistent_payment': 'other',
                'suspicious_seller': 'other',
                'counterfeit': 'wrong_item',
                'missing_parts': 'incomplete_delivery',
                'wrong_color_size': 'wrong_item',
                'expired_product': 'damaged_item',
                'other': 'other',
            }
            mapped_reason = reason_mapping.get(reason, 'other')

            # Create the dispute
            dispute = Dispute.objects.create(
                payment_request_item=payment_item,
                buyer=buyer,
                seller=seller,
                reason=mapped_reason,
                disputed_amount=amount,
                description=(
                    f"[From Shopping App - {reason_display}]\n"
                    f"Order: {order_number}\n"
                    f"Item: {metadata.get('product_name', 'N/A')} x{metadata.get('quantity', 1)}\n"
                    f"Buyer says: {description}"
                ),
                status='Submitted',
                admin_notes=f"Shopping App Dispute #{shopping_dispute_id}. Auto-forwarded from e-commerce platform.",
            )

            # Hold seller's funds (mark as not withdrawable)
            # We do this by creating a 'hold' marker on the payment item
            payment_item.is_cleared = False
            payment_item.save()

            # Log activity
            ActivityLog.objects.create(
                user=buyer,
                platform=platform,
                action='dispute_filed',
                description=(
                    f"Dispute #{dispute.dispute_id} filed by {buyer_email} "
                    f"against {seller_email} for {amount} UGX. "
                    f"Forwarded from shopping app dispute #{shopping_dispute_id}."
                ),
                metadata={
                    'shopping_dispute_id': shopping_dispute_id,
                    'order_number': order_number,
                    'amount': str(amount),
                    'reason': reason,
                }
            )

            logger.info(
                f"✅ Dispute #{dispute.dispute_id} created from shopping app "
                f"(shopping #{shopping_dispute_id})"
            )

            return JsonResponse({
                'success': True,
                'dispute_id': dispute.dispute_id,
                'status': dispute.status,
                'message': 'Dispute created and funds held',
            }, status=201)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error creating dispute from shopping app: {str(e)}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)


# ═══════════════════════════════════════════════════════════════════════════
#  ENHANCED RESOLVE DISPUTE — WITH SHOPPING APP SYNC
# ═══════════════════════════════════════════════════════════════════════════

@csrf_exempt
@require_http_methods(["POST"])
def resolve_dispute_with_sync(request, dispute_id):
    """
    Enhanced dispute resolution that syncs status back to the shopping app.

    Replaces or supplements the existing resolve_dispute in admin_views.py.
    Call this from admin_views.resolve_dispute after the resolution logic.

    POST body (from admin form):
    {
        "resolution": "approve_refund" | "reject",
        "admin_notes": "..."
    }
    """
    try:
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        if request.user.role not in ['admin', 'superadmin']:
            return JsonResponse({'error': 'Unauthorized'}, status=403)

        dispute = get_object_or_404(Dispute, dispute_id=dispute_id)

        data = request.POST if request.POST else json.loads(request.body)
        resolution = data.get('resolution')
        admin_notes = data.get('admin_notes', '')

        with db_transaction.atomic():
            if resolution == 'await_review':
                # Just update notes and keep status as 'under_review'
                dispute.status = 'under_review'
                payment_status_to_sync = 'To Be Decided'
                refund_amount_str = None
            elif resolution == 'resolve_with_refund':
                # Process refund: transfer from seller wallet to buyer wallet
                seller_wallet = dispute.seller.wallet
                buyer_wallet = dispute.buyer.wallet

                refund_amount = dispute.payment_request_item.amount

                if seller_wallet.balance < refund_amount:
                    return JsonResponse({
                        'error': f'Insufficient seller balance. '
                                 f'Available: {seller_wallet.balance}, Required: {refund_amount}'
                    }, status=400)

                refund_tx = Transaction.objects.create(
                    platform=dispute.payment_request_item.payment_request.platform,
                    from_wallet=seller_wallet,
                    to_wallet=buyer_wallet,
                    amount=refund_amount,
                    transaction_type='refund',
                    status='completed',
                    description=(
                        f'Refund for dispute #{dispute.dispute_id}. '
                        f'{admin_notes}'
                    ),
                )

                seller_wallet.balance -= refund_amount
                buyer_wallet.balance += refund_amount
                seller_wallet.save()
                buyer_wallet.save()

                dispute.refund_transaction = refund_tx
                dispute.status = 'resolved_with_refund'
                payment_status_to_sync = 'Refunded'
                refund_amount_str = str(refund_amount)

            else:
                # Reject dispute — release held funds
                dispute.status = 'resolved_without_refund'
                payment_status_to_sync = 'Not Refunded'
                refund_amount_str = None

                # Mark payment item as cleared (release funds)
                payment_item = dispute.payment_request_item
                payment_item.is_cleared = True
                payment_item.cleared_at = timezone.now()
                payment_item.save()

            dispute.admin_notes = admin_notes
            dispute.resolved_by = request.user
            dispute.resolved_at = timezone.now()
            dispute.save()

            ActivityLog.objects.create(
                user=request.user,
                action='dispute_resolved',
                description=f'Dispute #{dispute.dispute_id} resolved: {resolution}. {admin_notes}',
            )

        # Sync back to shopping app
        _sync_dispute_to_shopping_app(
            dispute,
            payment_status=payment_status_to_sync,
            refund_amount=refund_amount_str,
            admin_notes=admin_notes,
        )

        return JsonResponse({
            'success': True,
            'message': f'Dispute resolved: {resolution}',
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error resolving dispute with sync: {str(e)}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)


# ═══════════════════════════════════════════════════════════════════════════
#  HELPER: SYNC DISPUTE STATUS BACK TO SHOPPING APP
# ═══════════════════════════════════════════════════════════════════════════

def _sync_dispute_to_shopping_app(dispute, payment_status, refund_amount=None, admin_notes=''):
    """
    Send dispute status update to the shopping app's webhook.

    The shopping app has an endpoint:
      POST /api/webhook/dispute-status/

    This function is called whenever a dispute status changes in the payment app.
    """
    try:
        # Get shopping_dispute_id from admin_notes or metadata
        shopping_dispute_id = None

        # Parse from admin_notes (format: "Shopping App Dispute #123. ...")
        if dispute.admin_notes and 'Shopping App Dispute #' in (dispute.admin_notes or ''):
            try:
                part = dispute.admin_notes.split('Shopping App Dispute #')[1]
                shopping_dispute_id = int(part.split('.')[0].split(' ')[0])
            except (ValueError, IndexError):
                pass

        if not shopping_dispute_id:
            # Check activity log for the shopping dispute ID
            log = ActivityLog.objects.filter(
                action='dispute_filed',
                metadata__isnull=False,
            ).order_by('-created_at').first()
            if log and log.metadata:
                shopping_dispute_id = log.metadata.get('shopping_dispute_id')

        if not shopping_dispute_id:
            logger.warning(
                f"Cannot sync dispute #{dispute.dispute_id}: "
                f"shopping_dispute_id not found"
            )
            return

        platform = dispute.payment_request_item.payment_request.platform
        # Shopping app's callback URL (derive from platform domain)
        shopping_app_url = platform.domain.rstrip('/')

        payload = {
            'api_key': str(platform.api_key),
            'shopping_dispute_id': shopping_dispute_id,
            'payment_dispute_id': dispute.dispute_id,
            'status': dispute.status,
            'payment_status': payment_status,
            'refund_amount': refund_amount,
            'admin_notes': admin_notes,
            'resolved_at': dispute.resolved_at.isoformat() if dispute.resolved_at else None,
        }

        response = http_requests.post(
            f"{shopping_app_url}/api/webhook/dispute-status/",
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=15,
        )

        if response.status_code == 200:
            logger.info(
                f"✅ Dispute #{dispute.dispute_id} status synced to shopping app "
                f"(shopping #{shopping_dispute_id})"
            )
        else:
            logger.error(
                f"❌ Failed to sync dispute to shopping app: "
                f"{response.status_code} - {response.text}"
            )

    except http_requests.exceptions.RequestException as e:
        logger.error(f"❌ Connection error syncing dispute: {str(e)}")
    except Exception as e:
        logger.error(f"❌ Error syncing dispute: {str(e)}", exc_info=True)

