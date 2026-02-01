"""
Fair Cashier Payment Processor Module

This module simulates payment gateway integration with MTN Mobile Money, Airtel Money,
and bank transfers. In production, these simulated functions would be replaced with
actual API calls to licensed payment gateways.

Academic Implementation Note:
As per the project proposal, this demonstrates payment gateway integration using
sample API request/response payloads modeled after real mobile money APIs.
Production deployment would map directly to official REST APIs requiring
licensed access and production credentials.
"""

import time
import random
import uuid
from decimal import Decimal
from django.utils import timezone
from django.http import JsonResponse
from django.db import transaction as db_transaction
import logging

logger = logging.getLogger(__name__)


class PaymentProcessor:
    """
    Main payment processor class that simulates mobile money and bank integrations.
    This would be replaced with actual API clients in production.
    """
    
    # Simulated API endpoints (would be real URLs in production)
    MTN_API_URL = "https://sandbox.momodeveloper.mtn.com"
    AIRTEL_API_URL = "https://openapiuat.airtel.africa"
    BANK_API_URL = "https://api.bankgateway.com"
    
    def __init__(self, api_key, provider='mtn'):
        """
        Initialize payment processor with API credentials
        
        Args:
            api_key: Platform's payment API key
            provider: Payment provider (mtn, airtel, bank)
        """
        self.api_key = api_key
        self.provider = provider.lower()
        self.transaction_reference = None
    
    def request_collection(self, phone_number, amount, description="Deposit"):
        """
        Request money collection from customer (Deposit)
        
        Simulates the following production API flow:
        1. POST /collection/v1_0/requesttopay (MTN)
        2. POST /merchant/v1/payments/ (Airtel)
        
        Args:
            phone_number: Customer's mobile money number
            amount: Amount to collect
            description: Transaction description
            
        Returns:
            dict: Simulated API response
        """
        self.transaction_reference = str(uuid.uuid4())
        
        # Simulate API request delay
        time.sleep(random.uniform(0.5, 1.5))
        
        if self.provider == 'mtn':
            return self._simulate_mtn_collection(phone_number, amount, description)
        elif self.provider == 'airtel':
            return self._simulate_airtel_collection(phone_number, amount, description)
        else:
            return self._simulate_error("Unsupported provider")
    
    def request_disbursement(self, phone_number, amount, description="Cashout"):
        """
        Request money disbursement to customer (Cashout)
        
        Simulates the following production API flow:
        1. POST /disbursement/v1_0/transfer (MTN)
        2. POST /standard/v1/disbursements/ (Airtel)
        
        Args:
            phone_number: Customer's mobile money number
            amount: Amount to disburse
            description: Transaction description
            
        Returns:
            dict: Simulated API response
        """
        self.transaction_reference = str(uuid.uuid4())
        
        # Simulate API request delay
        time.sleep(random.uniform(0.5, 1.5))
        
        if self.provider == 'mtn':
            return self._simulate_mtn_disbursement(phone_number, amount, description)
        elif self.provider == 'airtel':
            return self._simulate_airtel_disbursement(phone_number, amount, description)
        else:
            return self._simulate_error("Unsupported provider")
    
    def check_transaction_status(self, reference_id):
        """
        Check status of a transaction
        
        Simulates:
        1. GET /collection/v1_0/requesttopay/{referenceId} (MTN)
        2. GET /standard/v1/payments/{transaction_id} (Airtel)
        
        Args:
            reference_id: Transaction reference ID
            
        Returns:
            dict: Transaction status
        """
        # Simulate API delay
        time.sleep(random.uniform(0.2, 0.8))
        
        # Simulate 95% success rate
        success = random.random() < 0.95
        
        if success:
            return {
                'status': 'SUCCESSFUL',
                'reference_id': reference_id,
                'reason': None,
                'timestamp': timezone.now().isoformat()
            }
        else:
            return {
                'status': 'FAILED',
                'reference_id': reference_id,
                'reason': 'INSUFFICIENT_BALANCE',
                'timestamp': timezone.now().isoformat()
            }
    
    # ============= MTN Mobile Money Simulations =============
    
    def _simulate_mtn_collection(self, phone_number, amount, description):
        """
        Simulate MTN Mobile Money Collection API
        
        Real API endpoint: POST https://sandbox.momodeveloper.mtn.com/collection/v1_0/requesttopay
        Request headers would include: X-Reference-Id, X-Target-Environment, Ocp-Apim-Subscription-Key
        """
        # Simulate 95% success rate
        success = random.random() < 0.95
        
        if success:
            return {
                'status': 'success',
                'provider': 'MTN Mobile Money',
                'operation': 'collection',
                'reference_id': self.transaction_reference,
                'message': 'Collection request initiated successfully',
                'details': {
                    'amount': float(amount),
                    'currency': 'UGX',
                    'payer': {
                        'partyIdType': 'MSISDN',
                        'partyId': phone_number
                    },
                    'payerMessage': description,
                    'payeeNote': f'Deposit to Fair Cashier',
                    'status': 'PENDING',
                    'reason': None
                },
                'api_response_code': '202',
                'timestamp': timezone.now().isoformat(),
                'next_action': 'Customer will receive USSD prompt to approve payment'
            }
        else:
            return self._simulate_error(
                "Transaction failed",
                error_code='PAYER_NOT_FOUND',
                provider='MTN'
            )
    
    def _simulate_mtn_disbursement(self, phone_number, amount, description):
        """
        Simulate MTN Mobile Money Disbursement API
        
        Real API endpoint: POST https://sandbox.momodeveloper.mtn.com/disbursement/v1_0/transfer
        """
        success = random.random() < 0.95
        
        if success:
            return {
                'status': 'success',
                'provider': 'MTN Mobile Money',
                'operation': 'disbursement',
                'reference_id': self.transaction_reference,
                'message': 'Disbursement completed successfully',
                'details': {
                    'amount': float(amount),
                    'currency': 'UGX',
                    'payee': {
                        'partyIdType': 'MSISDN',
                        'partyId': phone_number
                    },
                    'payerMessage': description,
                    'payeeNote': f'Withdrawal from Fair Cashier',
                    'status': 'SUCCESSFUL',
                    'reason': None
                },
                'api_response_code': '200',
                'timestamp': timezone.now().isoformat(),
                'confirmation': f'Sent UGX {amount:,.2f} to {phone_number}'
            }
        else:
            return self._simulate_error(
                "Disbursement failed",
                error_code='NOT_ENOUGH_FUNDS',
                provider='MTN'
            )
    
    # ============= Airtel Money Simulations =============
    
    def _simulate_airtel_collection(self, phone_number, amount, description):
        """
        Simulate Airtel Money Collection API
        
        Real API endpoint: POST https://openapiuat.airtel.africa/merchant/v1/payments/
        Request headers would include: Authorization, X-Country, X-Currency
        """
        success = random.random() < 0.95
        
        if success:
            return {
                'status': 'success',
                'provider': 'Airtel Money',
                'operation': 'collection',
                'reference_id': self.transaction_reference,
                'message': 'Payment request sent to customer',
                'details': {
                    'transaction': {
                        'id': self.transaction_reference,
                        'amount': float(amount),
                        'currency': 'UGX',
                        'status': 'pending'
                    },
                    'subscriber': {
                        'country': 'UG',
                        'currency': 'UGX',
                        'msisdn': phone_number
                    },
                    'reference': description,
                    'pin': 'NOT_REQUIRED'
                },
                'api_response_code': '200',
                'timestamp': timezone.now().isoformat(),
                'next_action': 'Customer will receive push notification to approve'
            }
        else:
            return self._simulate_error(
                "Payment request failed",
                error_code='INVALID_MSISDN',
                provider='Airtel'
            )
    
    def _simulate_airtel_disbursement(self, phone_number, amount, description):
        """
        Simulate Airtel Money Disbursement API
        
        Real API endpoint: POST https://openapiuat.airtel.africa/standard/v1/disbursements/
        """
        success = random.random() < 0.95
        
        if success:
            return {
                'status': 'success',
                'provider': 'Airtel Money',
                'operation': 'disbursement',
                'reference_id': self.transaction_reference,
                'message': 'Disbursement completed successfully',
                'details': {
                    'transaction': {
                        'id': self.transaction_reference,
                        'amount': float(amount),
                        'currency': 'UGX',
                        'status': 'success'
                    },
                    'payee': {
                        'country': 'UG',
                        'currency': 'UGX',
                        'msisdn': phone_number
                    },
                    'reference': description,
                    'message': 'Disbursement successful'
                },
                'api_response_code': '200',
                'timestamp': timezone.now().isoformat(),
                'confirmation': f'Sent UGX {amount:,.2f} to {phone_number}'
            }
        else:
            return self._simulate_error(
                "Disbursement failed",
                error_code='INSUFFICIENT_BALANCE',
                provider='Airtel'
            )
    
    # ============= Helper Methods =============
    
    def _simulate_error(self, message, error_code='TRANSACTION_FAILED', provider=None):
        """Simulate API error response"""
        return {
            'status': 'error',
            'provider': provider or self.provider.upper(),
            'error_code': error_code,
            'message': message,
            'reference_id': self.transaction_reference,
            'timestamp': timezone.now().isoformat(),
            'details': {
                'description': self._get_error_description(error_code),
                'resolution': 'Please try again or contact support'
            }
        }
    
    def _get_error_description(self, error_code):
        """Get human-readable error descriptions"""
        error_descriptions = {
            'PAYER_NOT_FOUND': 'The mobile money account was not found',
            'NOT_ENOUGH_FUNDS': 'Insufficient funds in the account',
            'INVALID_MSISDN': 'Invalid phone number format',
            'TRANSACTION_FAILED': 'Transaction could not be completed',
            'INSUFFICIENT_BALANCE': 'Insufficient balance for this transaction',
            'TIMEOUT': 'Transaction timed out',
            'DUPLICATE_TRANSACTION': 'Duplicate transaction detected'
        }
        return error_descriptions.get(error_code, 'Unknown error occurred')


# ============= High-Level Processing Functions =============

def process_deposit(user, platform, amount, phone_number):
    """
    Process a deposit transaction
    
    This function orchestrates the complete deposit flow:
    1. Validate inputs
    2. Create transaction records
    3. Call payment gateway
    4. Update wallet on success
    5. Log activity
    
    Args:
        user: User making the deposit
        platform: Platform through which deposit is made
        amount: Deposit amount
        phone_number: Mobile money number
        
    Returns:
        dict: Transaction result
    """
    from .models import Transaction, MobileMoneyTransaction, Wallet, ActivityLog
    
    try:
        # Initialize payment processor
        processor = PaymentProcessor(
            api_key=platform.mobile_money_api_key,
            provider=platform.mobile_money_provider
        )
        
        with db_transaction.atomic():
            # Create transaction record
            transaction = Transaction.objects.create(
                platform=platform,
                to_wallet=user.wallet,
                amount=amount,
                transaction_type='deposit',
                status='pending',
                description=f'Deposit via {platform.get_mobile_money_provider_display()}'
            )
            
            # Request collection from mobile money
            api_response = processor.request_collection(
                phone_number=phone_number,
                amount=float(amount),
                description=f'Deposit to Fair Cashier'
            )
            
            # Create mobile money transaction record
            mm_transaction = MobileMoneyTransaction.objects.create(
                platform=platform,
                transaction=transaction,
                operation_type='collection',
                phone_number=phone_number,
                amount=amount,
                external_reference=api_response.get('reference_id', str(uuid.uuid4())),
                api_response=api_response,
                status='pending' if api_response['status'] == 'success' else 'failed'
            )
            
            if api_response['status'] == 'success':
                # In simulation, we'll auto-complete after a delay
                # In production, this would be done via webhook callback
                transaction.status = 'processing'
                transaction.mobile_money_reference = api_response['reference_id']
                transaction.save()
                
                logger.info(f"Deposit initiated: {amount} UGX for user {user.email}")
                
                return {
                    'status': 'success',
                    'message': 'Deposit initiated successfully',
                    'transaction_id': str(transaction.transaction_id),
                    'reference_id': api_response['reference_id'],
                    'next_action': api_response.get('next_action', 'Please approve on your phone'),
                    'provider': api_response['provider']
                }
            else:
                transaction.status = 'failed'
                transaction.save()
                
                logger.error(f"Deposit failed for user {user.email}: {api_response.get('message')}")
                
                return {
                    'status': 'error',
                    'message': api_response.get('message', 'Deposit failed'),
                    'error_code': api_response.get('error_code'),
                    'transaction_id': str(transaction.transaction_id)
                }
    
    except Exception as e:
        logger.error(f"Deposit processing error: {str(e)}")
        return {
            'status': 'error',
            'message': 'Failed to process deposit',
            'error': str(e)
        }


def process_cashout(user, platform, amount, phone_number):
    """
    Process a cashout/withdrawal transaction
    
    This function orchestrates the complete cashout flow:
    1. Validate balance
    2. Create transaction records
    3. Call payment gateway for disbursement
    4. Update wallet on success
    5. Log activity
    
    Args:
        user: User making the withdrawal
        platform: Platform through which cashout is made
        amount: Cashout amount
        phone_number: Mobile money number
        
    Returns:
        dict: Transaction result
    """
    from .models import Transaction, MobileMoneyTransaction, Wallet, ActivityLog
    
    try:
        wallet = user.wallet
        
        # Validate balance
        if wallet.balance < amount:
            return {
                'status': 'error',
                'message': 'Insufficient balance',
                'available_balance': str(wallet.balance)
            }
        
        # Initialize payment processor
        processor = PaymentProcessor(
            api_key=platform.mobile_money_api_key,
            provider=platform.mobile_money_provider
        )
        
        with db_transaction.atomic():
            # Deduct from wallet first (will rollback if disbursement fails)
            wallet.balance -= amount
            wallet.save()
            
            # Create transaction record
            transaction = Transaction.objects.create(
                platform=platform,
                from_wallet=wallet,
                amount=amount,
                transaction_type='cashout',
                status='pending',
                description=f'Cashout via {platform.get_mobile_money_provider_display()}'
            )
            
            # Request disbursement to mobile money
            api_response = processor.request_disbursement(
                phone_number=phone_number,
                amount=float(amount),
                description=f'Withdrawal from Fair Cashier'
            )
            
            # Create mobile money transaction record
            mm_transaction = MobileMoneyTransaction.objects.create(
                platform=platform,
                transaction=transaction,
                operation_type='disbursement',
                phone_number=phone_number,
                amount=amount,
                external_reference=api_response.get('reference_id', str(uuid.uuid4())),
                api_response=api_response,
                status='successful' if api_response['status'] == 'success' else 'failed'
            )
            
            if api_response['status'] == 'success':
                transaction.status = 'completed'
                transaction.mobile_money_reference = api_response['reference_id']
                transaction.save()
                
                logger.info(f"Cashout completed: {amount} UGX for user {user.email}")
                
                return {
                    'status': 'success',
                    'message': 'Cashout completed successfully',
                    'transaction_id': str(transaction.transaction_id),
                    'reference_id': api_response['reference_id'],
                    'confirmation': api_response.get('confirmation'),
                    'new_balance': str(wallet.balance),
                    'provider': api_response['provider']
                }
            else:
                # Rollback wallet deduction
                wallet.balance += amount
                wallet.save()
                
                transaction.status = 'failed'
                transaction.save()
                
                logger.error(f"Cashout failed for user {user.email}: {api_response.get('message')}")
                
                return {
                    'status': 'error',
                    'message': api_response.get('message', 'Cashout failed'),
                    'error_code': api_response.get('error_code'),
                    'transaction_id': str(transaction.transaction_id)
                }
    
    except Exception as e:
        logger.error(f"Cashout processing error: {str(e)}")
        return {
            'status': 'error',
            'message': 'Failed to process cashout',
            'error': str(e)
        }


def complete_pending_deposit(transaction_id, external_reference):
    """
    Complete a pending deposit transaction
    
    This simulates the webhook callback that would be received from the
    payment gateway when the customer approves the payment.
    
    In production, this would be called by the payment gateway's webhook.
    
    Args:
        transaction_id: Internal transaction ID
        external_reference: Payment gateway reference
        
    Returns:
        dict: Completion result
    """
    from .models import Transaction, MobileMoneyTransaction
    
    try:
        with db_transaction.atomic():
            transaction = Transaction.objects.select_for_update().get(
                transaction_id=transaction_id
            )
            
            if transaction.status != 'processing':
                return {
                    'status': 'error',
                    'message': f'Transaction not in processing state: {transaction.status}'
                }
            
            # Update wallet
            wallet = transaction.to_wallet
            wallet.balance += transaction.amount
            wallet.save()
            
            # Update transaction
            transaction.status = 'completed'
            transaction.save()
            
            # Update mobile money transaction
            mm_transaction = transaction.mobile_money_transaction.first()
            if mm_transaction:
                mm_transaction.status = 'successful'
                mm_transaction.save()
            
            logger.info(f"Deposit completed: {transaction.amount} UGX")
            
            return {
                'status': 'success',
                'message': 'Deposit completed successfully',
                'transaction_id': str(transaction.transaction_id),
                'amount': str(transaction.amount),
                'new_balance': str(wallet.balance)
            }
    
    except Transaction.DoesNotExist:
        return {
            'status': 'error',
            'message': 'Transaction not found'
        }
    except Exception as e:
        logger.error(f"Error completing deposit: {str(e)}")
        return {
            'status': 'error',
            'message': str(e)
        }


# ============= Production Migration Guide =============

"""
MIGRATION TO PRODUCTION APIS:

To replace these simulations with actual payment gateway APIs:

1. MTN Mobile Money:
   - Register at https://momodeveloper.mtn.com/
   - Obtain API credentials (Primary Key, API User, API Key)
   - Update MTN_API_URL to production endpoint
   - Replace _simulate_mtn_* methods with actual API calls using requests library
   
   Example:
   ```python
   import requests
   
   def _mtn_collection(self, phone_number, amount, description):
       headers = {
           'X-Reference-Id': str(uuid.uuid4()),
           'X-Target-Environment': 'production',
           'Ocp-Apim-Subscription-Key': self.api_key,
           'Authorization': f'Bearer {self.get_access_token()}'
       }
       
       payload = {
           'amount': str(amount),
           'currency': 'UGX',
           'externalId': str(uuid.uuid4()),
           'payer': {'partyIdType': 'MSISDN', 'partyId': phone_number},
           'payerMessage': description,
           'payeeNote': 'Fair Cashier Deposit'
       }
       
       response = requests.post(
           f'{self.MTN_API_URL}/collection/v1_0/requesttopay',
           headers=headers,
           json=payload
       )
       
       return response.json()
   ```

2. Airtel Money:
   - Register at https://developers.airtel.africa/
   - Obtain client ID and client secret
   - Implement OAuth2 token management
   - Replace _simulate_airtel_* methods with actual API calls
   
3. Webhook Handling:
   - Implement webhook endpoints to receive payment confirmations
   - Verify webhook signatures for security
   - Call complete_pending_deposit() when payments are confirmed
   
4. Error Handling:
   - Implement retry logic for failed API calls
   - Add exponential backoff for rate limiting
   - Set up monitoring and alerting for failed transactions
"""
