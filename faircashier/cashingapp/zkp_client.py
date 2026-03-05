"""
Payment App — zkp_client.py (REPLACE existing)

KEPT: verify_kyc_proof(), generate_balance_proof(), verify_balance_proof()
NEW: fetch_seller_proof_from_shopping_app()
"""

import hashlib
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def encode_to_bigint(value):
    """Convert value to numeric BigInt string for Poseidon. BN254 field < 2^253."""
    s = str(value).strip()
    if s.isdigit():
        if int(s) < (2 ** 253):
            return s
        h = hashlib.sha256(s.encode()).hexdigest()
        return str(int(h, 16) % (2 ** 253))
    stripped = ''.join(c for c in s if c.isdigit())
    if stripped and int(stripped) < (2 ** 253):
        return stripped
    h = hashlib.sha256(s.encode()).hexdigest()
    return str(int(h, 16) % (2 ** 253))


class ZKPClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or settings.ZKP_STRAPI_URL).rstrip('/')

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        timeout = kwargs.pop('timeout', 30)
        try:
            resp = getattr(requests, method)(url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            logger.error(f"ZKP API timeout: {method.upper()} {url}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"ZKP API error: {method.upper()} {url} — {e}")
            raise

    # ── KYC Verification ONLY (no registration) ──

    def verify_kyc_proof(self, proof, public_signals):
        """Verify seller KYC proof. Payment App NEVER sees raw KYC fields."""
        return self._request('post', '/api/verify-kyc-proof', json={
            'proof': proof, 'publicSignals': public_signals,
        })

    # ── Balance Proof (Payment App generates — it has wallet balance) ──

    def generate_balance_proof(self, balance, required_amount, order_hash, timestamp):
        """Generate Groth16 balance proof. balance is PRIVATE input."""
        return self._request('post', '/api/generate-balance-proof', json={
            'balance': str(balance), 'requiredAmount': str(required_amount),
            'orderHash': str(order_hash), 'timestamp': str(timestamp),
        }, timeout=60)

    def verify_balance_proof(self, proof, public_signals):
        """Verify balance proof (self-check after generation)."""
        return self._request('post', '/api/verify-balance-proof', json={
            'proof': proof, 'publicSignals': public_signals,
        })

    # ── Public Tree Status ──

    def get_kyc_tree_status(self):
        return self._request('get', '/api/kyc-tree-status')

    def get_latest_root(self):
        return self._request('get', '/api/latest-root')

    def get_root_history(self):
        return self._request('get', '/api/root-history')

    # ── Fetch seller proof from Shopping App ──

    def fetch_seller_proof_from_shopping_app(self, seller_email):
        """Fetch proof+public_signals from Shopping App. NO raw KYC data."""
        url = f"{settings.SHOPPING_APP_URL}/internal/seller-zkp-proof/{seller_email}/"
        try:
            resp = requests.get(url, headers={
                'X-Internal-Secret': settings.SHOPPING_APP_INTERNAL_SECRET,
            }, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"Shopping App returned {resp.status_code} for {seller_email}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch seller proof: {e}")
            return None