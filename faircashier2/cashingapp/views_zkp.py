"""
Payment App — views_zkp.py (FIXED)

Verification-only. Registration is Shopping App's job.

FIX: _verify_seller() now accepts THREE auth modes:
  a) PIN auth:       POST {email, pin}
  b) Session auth:   POST {email} when seller_dashboard_auth_{email} is in session
  c) dash_token auth: POST/GET {email, dash_token} — signed token from seller dashboard
                      (works inside cross-origin iframes where cookies are blocked)
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
from .seller_proxy_views import _verify_dash_token      # ← NEW: reuse the same helper

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

    # ── Auth check for GET (dash_token or session) ────────────────
    dash_token = request.GET.get('dash_token', '').strip()
    authenticated = False

    if dash_token and _verify_dash_token(dash_token, email):
        authenticated = True

    if not authenticated:
        session_key = f'seller_dashboard_auth_{email}'
        if request.session.get(session_key):
            authenticated = True

    # Allow GET status check without auth (returns public status),
    # but log it for awareness
    if not authenticated:
        logger.debug(f"ZKP status check without auth for {email} (allowed for GET)")

    try:
        user = Users.objects.get(email=email, role='seller')
    except Users.DoesNotExist:
        return JsonResponse({'error': 'Seller not found'}, status=404)

    # Check if Shopping App has registered this seller (for unverified vs not-registered)
    commitment_hash = getattr(user, 'zkp_commitment_hash', '') or ''
    if not commitment_hash:
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
        'zkp_verified_at': (
            user.zkp_verified_at.isoformat()
            if getattr(user, 'zkp_verified_at', None) else None
        ),
        'seller_id_hash': getattr(user, 'zkp_seller_id_hash', ''),
        'kyc_root': getattr(user, 'zkp_kyc_root', ''),
        'commitment_hash': commitment_hash,
    })


def _verify_seller(request):
    """
    Verify a seller's KYC proof via Strapi.
    
    Accepts THREE auth modes:
      1. PIN auth:       POST {email, pin}
      2. Session auth:   POST {email} — when seller_dashboard_auth_{email} is in session
      3. dash_token auth: POST {email, dash_token} — signed token from seller dashboard
                          (works inside cross-origin iframes where cookies are blocked)
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    email = body.get('email', '').strip().lower()
    pin = body.get('pin', '').strip()
    dash_token = body.get('dash_token', '').strip()       # ← NEW
    if not email:
        return JsonResponse({'error': 'Email is required'}, status=400)

    try:
        user = Users.objects.get(email=email, role='seller')
    except Users.DoesNotExist:
        return JsonResponse({'error': 'Seller not found'}, status=404)

    # ── Auth: try PIN first, then dash_token, then session ────────
    authenticated = False

    if pin:
        # Mode 1: PIN auth
        pin_result = PINAuthenticator.verify_pin(user, pin)
        if not pin_result['valid']:
            return JsonResponse({
                'error': pin_result.get('error', 'Invalid PIN'),
                'attempts_remaining': pin_result.get('attempts_remaining'),
            }, status=401)
        authenticated = True

    if not authenticated and dash_token:
        # Mode 2: Signed dash_token auth (iframe-safe, no cookies needed)
        if _verify_dash_token(dash_token, email):
            authenticated = True
            logger.debug(f"ZKP verify: dash_token auth OK for {email}")

    if not authenticated:
        # Mode 3: Session auth (set by seller_dashboard_iframe POST handler)
        session_key = f'seller_dashboard_auth_{email}'
        if request.session.get(session_key):
            authenticated = True
            logger.debug(f"ZKP verify: session auth OK for {email}")

    if not authenticated:
        return JsonResponse({
            'error': 'Authentication required. Enter PIN or refresh the page.',
        }, status=401)

    # ── Fetch proof from Shopping App ─────────────────────────────────
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
        return JsonResponse({
            'error': 'Incomplete proof data',
            'zkp_verified': False,
        }, status=400)

    # ── Verify via Strapi ─────────────────────────────────────────────
    try:
        verification = client.verify_kyc_proof(proof, public_signals)
    except Exception as e:
        logger.error(f"Strapi verification failed for {email}: {e}")
        return JsonResponse({
            'error': 'Verification service unavailable',
            'zkp_verified': False,
        }, status=503)

    # Strapi kycVerifyProof returns:
    #   { verified: bool, publicSignals: {kyc_root, seller_id_hash, current_year}, meta: {verified_at, ...} }
    is_valid = verification.get('verified', False)
    pub = verification.get('publicSignals', {})
    meta = verification.get('meta', {})

    # ── Store result ──────────────────────────────────────────────────
    user.zkp_verified = is_valid
    user.zkp_seller_id_hash = pub.get('seller_id_hash', '')
    user.zkp_kyc_root = pub.get('kyc_root', '')
    user.zkp_commitment_hash = proof_data.get('commitment_hash', '')
    if is_valid:
        user.zkp_verified_at = timezone.now()
    user.save(update_fields=[
        'zkp_verified', 'zkp_seller_id_hash', 'zkp_kyc_root',
        'zkp_commitment_hash', 'zkp_verified_at',
    ])

    logger.info(f"Seller ZKP verification {'OK' if is_valid else 'FAILED'} for {email}")

    return JsonResponse({
        'success': is_valid,
        'zkp_verified': is_valid,
        'seller_id_hash': pub.get('seller_id_hash', ''),
        'kyc_root': pub.get('kyc_root', ''),
        'commitment_hash': proof_data.get('commitment_hash', ''),
        'verified_at': meta.get('verified_at', ''),
    })


# ── Internal API ──────────────────────────────────────────────────────

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
        return JsonResponse({
            'email': email, 'zkp_verified': False, 'error': 'Not found',
        }, status=404)

    return JsonResponse({
        'email': email,
        'zkp_verified': getattr(user, 'zkp_verified', False),
        'zkp_verified_at': (
            user.zkp_verified_at.isoformat()
            if getattr(user, 'zkp_verified_at', None) else None
        ),
        'seller_id_hash': getattr(user, 'zkp_seller_id_hash', ''),
        'kyc_root': getattr(user, 'zkp_kyc_root', ''),
        'commitment_hash': getattr(user, 'zkp_commitment_hash', ''),
    })



# ─────────────────────────────────────────────────────────────────────────────
# Internal API — Shopping App fetches seller's actual verification status
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["GET"])
def api_internal_seller_zkp_status(request, seller_email):
    """
    Shopping App calls this to get the ground-truth verification status
    from the Payment App's User model.
    
    GET /api/internal/seller-zkp-status/<email>/
    Headers: X-Internal-Secret
    
    Returns the ACTUAL verification fields stored on User after
    Payment App verified the proof via Strapi /verify-kyc-proof.
    
    Shopping App compares commitment_hash from this response with its own
    SellerVerification.zkp_commitment_hash to confirm consistency.
    """
    secret = request.headers.get('X-Internal-Secret', '')
    if secret != settings.SHOPPING_APP_INTERNAL_SECRET:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    email = seller_email.strip().lower()
    try:
        user = Users.objects.get(email=email, role='seller')
    except Users.DoesNotExist:
        return JsonResponse({
            'email': email,
            'zkp_verified': False,
            'exists_in_payment_app': False,
            'error': 'Seller not found in payment system',
        }, status=404)

    return JsonResponse({
        'email': email,
        'exists_in_payment_app': True,
        # Ground-truth verification fields from User model
        'zkp_verified': getattr(user, 'zkp_verified', False),
        'zkp_verified_at': (
            user.zkp_verified_at.isoformat()
            if getattr(user, 'zkp_verified_at', None) else None
        ),
        'zkp_seller_id_hash': getattr(user, 'zkp_seller_id_hash', ''),
        'zkp_kyc_root': getattr(user, 'zkp_kyc_root', ''),
        'zkp_commitment_hash': getattr(user, 'zkp_commitment_hash', ''),
    })