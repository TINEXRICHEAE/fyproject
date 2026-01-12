import requests
from django.conf import settings


class MobileMoneyAPI:
    """Base class for mobile money API integration"""

    def __init__(self, api_key, provider='mtn'):
        self.api_key = api_key
        self.provider = provider
        self.base_url = self.get_base_url()

    def get_base_url(self):
        """Get API base URL based on provider"""
        urls = {
            'mtn': 'https://api.mtn.com/v1/',
            'airtel': 'https://api.airtel.com/v1/',
        }
        return urls.get(self.provider, '')

    def request_collection(self, phone_number, amount, reference):
        """Request money collection from customer"""
        # Implement actual API call here
        # This is a placeholder
        endpoint = f"{self.base_url}collection/request"
        payload = {
            'phone_number': phone_number,
            'amount': amount,
            'reference': reference,
            'currency': 'UGX'
        }
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        try:
            response = requests.post(endpoint, json=payload, headers=headers)
            return response.json()
        except Exception as e:
            return {'error': str(e)}

    def request_disbursement(self, phone_number, amount, reference):
        """Send money to customer"""
        # Implement actual API call here
        # This is a placeholder
        endpoint = f"{self.base_url}disbursement/request"
        payload = {
            'phone_number': phone_number,
            'amount': amount,
            'reference': reference,
            'currency': 'UGX'
        }
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        try:
            response = requests.post(endpoint, json=payload, headers=headers)
            return response.json()
        except Exception as e:
            return {'error': str(e)}

    def check_transaction_status(self, reference):
        """Check status of a transaction"""
        # Implement actual API call here
        endpoint = f"{self.base_url}transaction/{reference}/status"
        headers = {
            'Authorization': f'Bearer {self.api_key}'
        }

        try:
            response = requests.get(endpoint, headers=headers)
            return response.json()
        except Exception as e:
            return {'error': str(e)}
