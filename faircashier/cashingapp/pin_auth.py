# cashingapp/pin_auth.py (NEW FILE)

from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)

class PINAuthenticator:
    """
    Handles PIN-based authentication for buyers/sellers
    
    Security features:
    - Argon2 hashing (strong even for 4-digit PINs)
    - Rate limiting (max 3 attempts per 15 minutes)
    - Account lockout after 5 failed attempts
    - Cache-based attempt tracking
    """
    
    MAX_ATTEMPTS_SHORT = 3  # Per 15 minutes
    MAX_ATTEMPTS_TOTAL = 5  # Before account lock
    LOCKOUT_DURATION = 3600  # 1 hour in seconds
    RATE_LIMIT_WINDOW = 900  # 15 minutes in seconds
    
    @staticmethod
    def hash_pin(pin):
        """
        Hash a PIN using Argon2
        
        Args:
            pin (str): 4-digit PIN
            
        Returns:
            str: Hashed PIN
        """
        if not pin or len(pin) != 4 or not pin.isdigit():
            raise ValueError("PIN must be exactly 4 digits")
        
        # Use Argon2 explicitly for strong hashing
        return make_password(pin, hasher='argon2')
    
    @staticmethod
    def verify_pin(user, pin):
        """
        Verify PIN with rate limiting and lockout
        
        Args:
            user: Users model instance
            pin (str): PIN to verify
            
        Returns:
            dict: {
                'valid': bool,
                'error': str or None,
                'attempts_remaining': int or None
            }
        """
        # Check if account is locked
        if user.pin_locked_until and user.pin_locked_until > timezone.now():
            remaining = (user.pin_locked_until - timezone.now()).seconds // 60
            return {
                'valid': False,
                'error': f'Account locked. Try again in {remaining} minutes.',
                'attempts_remaining': 0
            }
        
        # Check rate limiting (cache-based for short-term)
        cache_key = f"pin_attempts:{user.email}"
        attempts = cache.get(cache_key, 0)
        
        if attempts >= PINAuthenticator.MAX_ATTEMPTS_SHORT:
            return {
                'valid': False,
                'error': 'Too many attempts. Please wait 15 minutes.',
                'attempts_remaining': 0
            }
        
        # Verify PIN
        if not user.pin:
            return {
                'valid': False,
                'error': 'PIN not set up',
                'attempts_remaining': None
            }
        
        is_valid = check_password(pin, user.pin)
        
        if is_valid:
            # Reset counters on success
            cache.delete(cache_key)
            user.pin_attempts = 0
            user.pin_locked_until = None
            user.save(update_fields=['pin_attempts', 'pin_locked_until'])
            
            logger.info(f"✅ PIN verified for {user.email}")
            
            return {
                'valid': True,
                'error': None,
                'attempts_remaining': None
            }
        else:
            # Increment attempt counters
            attempts += 1
            cache.set(cache_key, attempts, PINAuthenticator.RATE_LIMIT_WINDOW)
            
            user.pin_attempts += 1
            
            # Lock account after MAX_ATTEMPTS_TOTAL failures
            if user.pin_attempts >= PINAuthenticator.MAX_ATTEMPTS_TOTAL:
                user.pin_locked_until = timezone.now() + timezone.timedelta(
                    seconds=PINAuthenticator.LOCKOUT_DURATION
                )
                user.save(update_fields=['pin_attempts', 'pin_locked_until'])
                
                logger.warning(f"🔒 Account locked: {user.email}")
                
                return {
                    'valid': False,
                    'error': 'Account locked due to too many failed attempts. Try again in 1 hour.',
                    'attempts_remaining': 0
                }
            
            user.save(update_fields=['pin_attempts'])
            
            remaining = PINAuthenticator.MAX_ATTEMPTS_SHORT - attempts
            
            logger.warning(f"❌ Invalid PIN for {user.email} ({attempts}/{PINAuthenticator.MAX_ATTEMPTS_SHORT})")
            
            return {
                'valid': False,
                'error': 'Invalid PIN',
                'attempts_remaining': max(0, remaining)
            }
    
    @staticmethod
    def set_pin(user, pin, confirm_pin):
        """
        Set or update user's PIN
        
        Args:
            user: Users model instance
            pin (str): New PIN
            confirm_pin (str): PIN confirmation
            
        Returns:
            dict: {'success': bool, 'error': str or None}
        """
        # Validate PIN format
        if not pin or len(pin) != 4 or not pin.isdigit():
            return {'success': False, 'error': 'PIN must be exactly 4 digits'}
        
        # Check confirmation match
        if pin != confirm_pin:
            return {'success': False, 'error': 'PINs do not match'}
        
        # Prevent common/weak PINs
        weak_pins = ['0000', '1111', '2222', '3333', '4444', '5555', 
                     '6666', '7777', '8888', '9999', '1234', '4321']
        if pin in weak_pins:
            return {'success': False, 'error': 'PIN too common. Choose a different PIN'}
        
        # Hash and save
        try:
            user.pin = PINAuthenticator.hash_pin(pin)
            user.pin_attempts = 0
            user.pin_locked_until = None
            user.save(update_fields=['pin', 'pin_attempts', 'pin_locked_until'])
            
            logger.info(f"✅ PIN set for {user.email}")
            
            return {'success': True, 'error': None}
        except Exception as e:
            logger.error(f"❌ PIN set error for {user.email}: {str(e)}")
            return {'success': False, 'error': 'Failed to set PIN'}