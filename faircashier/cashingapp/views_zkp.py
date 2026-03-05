"""
Payment App — views_zkp.py

Verification-only. Registration is now Shopping App's job.

Flow:
  1. Seller dashboard calls GET /seller/zkp-verify/?email=X → status
  2. Seller clicks Verify → POST /seller/zkp-verify/ {email, pin}
  3. Payment App fetches proof from Shopping App (no raw KYC)
  4. Payment App verifies via Strapi /verify-kyc-proof
  5. Stores verification result on User model
"""

import json
import logging
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .zkp_client import ZKPClient
from .pin_auth import PINAuthenticator
from .models import Users

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def seller_zkp_verify(request):
    if request.method == 'GET':
        return _get_verification_status(request)
    return _verify_seller(request)


def _get_verification_status(request):
    email = request.GET.get('email', '').strip().lower()
    if not email:
        return JsonResponse({'error': 'Email required'}, status=400)

    try:
        user = Users.objects.get(email=email, role='seller')
    except Users.DoesNotExist:
        return JsonResponse({'error': 'Seller not found'}, status=404)

    # Also try to check if Shopping App has a proof (so we can show "unverified" vs "not registered")
    commitment_hash = getattr(user, 'zkp_commitment_hash', '') or ''
    if not commitment_hash:
        # Try fetching from Shopping App to see if seller is registered there
        try:
            client = ZKPClient()
            proof_data = client.fetch_seller_proof_from_shopping_app(email)
            if proof_data and proof_data.get('zkp_status') == 'registered':
                commitment_hash = proof_data.get('commitment_hash', '')
        except Exception:
            pass

    return JsonResponse({
        'email': email,
        'zkp_verified': getattr(user, 'zkp_verified', False),
        'zkp_verified_at': getattr(user, 'zkp_verified_at', None) and user.zkp_verified_at.isoformat(),
        'seller_id_hash': getattr(user, 'zkp_seller_id_hash', ''),
        'kyc_root': getattr(user, 'zkp_kyc_root', ''),
        'commitment_hash': commitment_hash,
    })


def _verify_seller(request):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    email = body.get('email', '').strip().lower()
    pin = body.get('pin', '').strip()
    if not email or not pin:
        return JsonResponse({'error': 'Email and PIN required'}, status=400)

    try:
        user = Users.objects.get(email=email, role='seller')
    except Users.DoesNotExist:
        return JsonResponse({'error': 'Seller not found'}, status=404)

    pin_result = PINAuthenticator.verify_pin(user, pin)
    if not pin_result['valid']:
        return JsonResponse({
            'error': pin_result.get('error', 'Invalid PIN'),
            'attempts_remaining': pin_result.get('attempts_remaining'),
        }, status=401)

    # Fetch proof from Shopping App
    client = ZKPClient()
    proof_data = client.fetch_seller_proof_from_shopping_app(email)

    if not proof_data:
        return JsonResponse({
            'error': 'Seller ZKP proof not available',
            'detail': 'Complete KYC registration on the shopping platform first.',
            'zkp_verified': False,
        }, status=404)

    if proof_data.get('zkp_status') != 'registered':
        return JsonResponse({
            'error': f'Seller ZKP status: {proof_data.get("zkp_status")}',
            'zkp_verified': False,
        }, status=400)

    proof = proof_data.get('proof')
    public_signals = proof_data.get('public_signals')
    if not proof or not public_signals:
        return JsonResponse({'error': 'Incomplete proof data', 'zkp_verified': False}, status=400)

    # Verify via Strapi
    try:
        verification = client.verify_kyc_proof(proof, public_signals)
    except Exception as e:
        logger.error(f"Strapi verification failed for {email}: {e}")
        return JsonResponse({'error': 'Verification service unavailable', 'zkp_verified': False}, status=503)

    is_valid = verification.get('valid', False)

    # Store result
    user.zkp_verified = is_valid
    user.zkp_seller_id_hash = verification.get('seller_id_hash', '')
    user.zkp_kyc_root = verification.get('kyc_root', '')
    user.zkp_commitment_hash = proof_data.get('commitment_hash', '')
    if is_valid:
        user.zkp_verified_at = timezone.now()
    user.save(update_fields=[
        'zkp_verified', 'zkp_seller_id_hash', 'zkp_kyc_root',
        'zkp_commitment_hash', 'zkp_verified_at',
    ])

    logger.info(f"Seller ZKP verification {'OK' if is_valid else 'FAILED'} for {email}")

    return JsonResponse({
        'success': is_valid, 'zkp_verified': is_valid,
        'seller_id_hash': verification.get('seller_id_hash', ''),
        'kyc_root': verification.get('kyc_root', ''),
        'commitment_hash': proof_data.get('commitment_hash', ''),
        'verified_at': verification.get('verified_at', ''),
        'message': verification.get('message', ''),
    })


# ── Internal API ──

@csrf_exempt
@require_http_methods(["GET"])
def internal_seller_zkp_status(request, seller_email):
    """Shopping App can check if Payment App has verified a seller."""
    secret = request.headers.get('X-Internal-Secret', '')
    if secret != settings.SHOPPING_APP_INTERNAL_SECRET:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    email = seller_email.strip().lower()
    try:
        user = Users.objects.get(email=email, role='seller')
    except Users.DoesNotExist:
        return JsonResponse({'email': email, 'zkp_verified': False, 'error': 'Not found'}, status=404)

    return JsonResponse({
        'email': email,
        'zkp_verified': getattr(user, 'zkp_verified', False),
        'zkp_verified_at': user.zkp_verified_at.isoformat() if getattr(user, 'zkp_verified_at', None) else None,
        'seller_id_hash': getattr(user, 'zkp_seller_id_hash', ''),
        'kyc_root': getattr(user, 'zkp_kyc_root', ''),
        'commitment_hash': getattr(user, 'zkp_commitment_hash', ''),
    })