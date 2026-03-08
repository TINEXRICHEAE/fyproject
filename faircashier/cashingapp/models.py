from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.utils import timezone
from django.db import models
from django.contrib.auth.models import Group as DjangoGroup
from decimal import Decimal
import uuid


class Group(DjangoGroup):
    """Extended Django Group model for managing user groups"""
    group_id = models.AutoField(primary_key=True)
    admin = models.ForeignKey(
        'Users',
        on_delete=models.CASCADE,
        related_name='managed_group',
        limit_choices_to={'role': 'admin'},
        null=True,
        blank=True
    )
    superadmin = models.ForeignKey(
        'Users',
        on_delete=models.CASCADE,
        related_name='supervised_groups',
        limit_choices_to={'role': 'superadmin'},
        null=True,
        blank=True
    )

    class Meta:
        db_table = 'groups'

    def __str__(self):
        if self.admin:
            return f"Group(name={self.name}, admin={self.admin.email})"
        elif self.superadmin:
            return f"Group(name={self.name}, superadmin={self.superadmin.email})"
        else:
            return f"Group(name={self.name})"


class UsersManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)

        if 'role' not in extra_fields:
            extra_fields['role'] = 'buyer'

        if extra_fields['role'] == 'admin':
            extra_fields['is_staff'] = True
            extra_fields['is_superuser'] = False
        elif extra_fields['role'] == 'superadmin':
            extra_fields['is_staff'] = True
            extra_fields['is_superuser'] = True
        else:
            extra_fields['is_staff'] = False
            extra_fields['is_superuser'] = False

        extra_fields['is_active'] = True

        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)

        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)

        if 'role' not in extra_fields:
            extra_fields['role'] = 'superadmin'

        return self.create_user(email, password, **extra_fields)

    def create_anonymous_user(self):
        """Create an anonymous user if it doesn't already exist."""
        anonymous_email = "anonymous@example.com"
        if not self.filter(email=anonymous_email).exists():
            anonymous_user = self.create(
                email=anonymous_email,
                role="buyer",
                is_active=False,
                is_staff=False,
                is_superuser=False,
            )
            anonymous_user.set_unusable_password()
            anonymous_user.save()
            return anonymous_user
        return None


class Users(AbstractBaseUser, PermissionsMixin):
    email = models.CharField(unique=True, max_length=50)
    password = models.CharField(max_length=128)
    ROLE_CHOICES = (
        ('buyer', 'Buyer'),
        ('seller', 'Seller'),
        ('admin', 'Admin'),
        ('superadmin', 'Super Admin'),
    )
    role = models.CharField(max_length=50, choices=ROLE_CHOICES)
    admin_email = models.EmailField(max_length=50, blank=True, null=True)
    platform_domain = models.EmailField(max_length=50, blank=True, null=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    # PIN authentication for buyers/sellers
    pin = models.CharField(
        max_length=255, 
        blank=True, 
        null=True,
        help_text="Hashed 4-digit PIN for transaction authentication"
    )
    pin_attempts = models.IntegerField(
        default=0,
        help_text="Failed PIN attempts counter"
    )
    pin_locked_until = models.DateTimeField(
        null=True, 
        blank=True,
        help_text="PIN locked until this timestamp"
    )
    # ZKP verification fields (sellers only — buyers/admins ignore these)
    zkp_verified = models.BooleanField(
        default=False,
        help_text="Whether this seller's KYC proof has been verified via ZKP"
    )
    zkp_verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When ZKP verification was completed"
    )
    zkp_seller_id_hash = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Seller ID hash from ZKP verification public signal"
    )
    zkp_kyc_root = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Merkle root at time of verification"
    )
    zkp_commitment_hash = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Poseidon commitment hash from registration"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)

    objects = UsersManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        db_table = 'users'

    def __str__(self):
        return f"User(id={self.id}, email={self.email}, role={self.role})"


class Platform(models.Model):
    """Registered relying party (RP) platforms/ecommerce apps"""
    platform_id = models.AutoField(primary_key=True)
    api_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    admin = models.ForeignKey(
        Users,
        on_delete=models.CASCADE,
        related_name='platforms',
        limit_choices_to={'role': 'admin'}
    )
    platform_name = models.CharField(max_length=255)
    domain = models.URLField(unique=True, max_length=500)
    return_url = models.URLField(
        max_length=500, help_text="URL to redirect after payment")
    callback_url = models.URLField(
        max_length=500, help_text="URL for payment notifications")
    mobile_money_api_key = models.CharField(
        max_length=500, blank=True, null=True)
    mobile_money_provider = models.CharField(
        max_length=50,
        choices=[
            ('mtn', 'MTN Mobile Money'),
            ('airtel', 'Airtel Money'),
            ('other', 'Other')
        ],
        default='mtn'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'platforms'

    def __str__(self):
        return f"{self.platform_name} ({self.admin.email})"


class Wallet(models.Model):
    """Digital wallet for each user"""
    wallet_id = models.AutoField(primary_key=True)
    user = models.OneToOneField(
        Users, on_delete=models.CASCADE, related_name='wallet')
    balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # ← NEW ─────────────────────────────────────────────────────────────────
    reserved_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text=(
            "Funds earmarked for pending item deposits. "
            "These remain in the wallet (balance is unchanged) but cannot be "
            "freely spent until the reservation is completed or released."
        )
    )
    # ────────────────────────────────────────────────────────────────────────

    currency = models.CharField(max_length=3, default='UGX')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'wallets'

    def __str__(self):
        return f"Wallet({self.user.email}: {self.balance} {self.currency})"

    # ← NEW ─────────────────────────────────────────────────────────────────
    @property
    def free_balance(self):
        """
        The portion of balance that can be freely spent or reserved.
        free_balance = balance - reserved_balance
        """
        return self.balance - self.reserved_balance
    # ────────────────────────────────────────────────────────────────────────


class Transaction(models.Model):
    """All transactions in the system"""
    transaction_id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False)
    platform = models.ForeignKey(
        Platform,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transactions'
    )
    from_wallet = models.ForeignKey(
        Wallet,
        on_delete=models.SET_NULL,
        null=True,
        related_name='outgoing_transactions'
    )
    to_wallet = models.ForeignKey(
        Wallet,
        on_delete=models.SET_NULL,
        null=True,
        related_name='incoming_transactions'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='UGX')
    TRANSACTION_TYPE_CHOICES = (
        ('deposit', 'Deposit'),
        ('transfer', 'Transfer'),
        ('cashout', 'Cashout'),
        ('refund', 'Refund'),
    )
    transaction_type = models.CharField(
        max_length=20, choices=TRANSACTION_TYPE_CHOICES)
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('reversed', 'Reversed'),
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending')
    mobile_money_reference = models.CharField(
        max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'transactions'
        ordering = ['-created_at']

    def __str__(self):
        return f"Transaction({self.transaction_type}: {self.amount} {self.currency})"


class PaymentRequest(models.Model):
    """Payment requests from RP platforms"""
    request_id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False)
    platform = models.ForeignKey(
        Platform, on_delete=models.CASCADE, related_name='payment_requests')
    buyer_email = models.EmailField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='UGX')
    STATUS_CHOICES = (
        ('initiated', 'Initiated'),
        ('awaiting_payment', 'Awaiting Payment'),
        ('paid', 'Paid'),
        ('awaiting_clearance', 'Awaiting Clearance'),
        ('cleared', 'Cleared'),
        ('failed', 'Failed'),
        ('disputed', 'Disputed'),
    )
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default='initiated')
    metadata = models.JSONField(
        blank=True, null=True, help_text="Additional data from RP platform")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payment_requests'
        ordering = ['-created_at']

    def __str__(self):
        return f"PaymentRequest({self.buyer_email}: {self.total_amount})"


class PaymentRequestItem(models.Model):
    """Individual seller items in a payment request"""
    item_id = models.AutoField(primary_key=True)
    payment_request = models.ForeignKey(
        PaymentRequest,
        on_delete=models.CASCADE,
        related_name='items'
    )
    seller_email = models.EmailField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='UGX')
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payment_item'
    )
    is_cleared = models.BooleanField(
        default=False, help_text="Buyer confirmed delivery")
    is_deposited = models.BooleanField(
            default=False,
            help_text="True when buyer has deposited funds to their wallet for this item"
        )
    deposited_amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        null=True, blank=True,
        help_text="Amount deposited to buyer wallet for this item"
    )
    deposited_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the deposit was made"
    )
    shopping_order_item_id = models.IntegerField(
        null=True, blank=True,
        help_text="Linked OrderItem.id on the shopping app — used for webhook status sync"
    )
    cleared_at = models.DateTimeField(null=True, blank=True)
    product_description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payment_request_items'

    def __str__(self):
        return f"Item({self.seller_email}: {self.amount})"


class Dispute(models.Model):
    """Dispute resolution system"""
    dispute_id = models.AutoField(primary_key=True)
    payment_request_item = models.ForeignKey(
        PaymentRequestItem,
        on_delete=models.CASCADE,
        related_name='disputes'
    )
    buyer = models.ForeignKey(
        Users,
        on_delete=models.CASCADE,
        related_name='filed_disputes',
        limit_choices_to={'role': 'buyer'}
    )
    seller = models.ForeignKey(
        Users,
        on_delete=models.CASCADE,
        related_name='received_disputes',
        limit_choices_to={'role': 'seller'}
    )
    REASON_CHOICES = (
        ('no_delivery', 'No Delivery'),
        ('wrong_item', 'Wrong Item'),
        ('damaged_item', 'Damaged Item'),
        ('incomplete_delivery', 'Incomplete Delivery'),
        ('other', 'Other'),
    )
    reason = models.CharField(max_length=50, choices=REASON_CHOICES)
    description = models.TextField()
    STATUS_CHOICES = (
        ('submitted', 'Submitted'),
        ('under_review', 'Under Review'),
        ('escalated', 'Escalated to Admin'),
        ('resolved_with_refund', 'Resolved With Refund'),
        ('resolved_without_refund', 'Resolved Without Refund'),
    )
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default='submitted')
    disputed_amount = models.DecimalField(
    max_digits=12, decimal_places=2, null=True, blank=True,
    help_text="Amount of the specific disputed item (not the full payment request item)"
    )
    refund_transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dispute_refund'
    )
    admin_notes = models.TextField(blank=True, null=True)
    resolved_by = models.ForeignKey(
        Users,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_disputes',
        limit_choices_to={'role__in': ['admin', 'superadmin']}
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'disputes'
        ordering = ['-created_at']

    def __str__(self):
        return f"Dispute({self.buyer.email} vs {self.seller.email})"


class MobileMoneyTransaction(models.Model):
    """Track mobile money API transactions"""
    mm_transaction_id = models.AutoField(primary_key=True)
    platform = models.ForeignKey(
        Platform, on_delete=models.CASCADE, related_name='mm_transactions')
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name='mobile_money_transaction'
    )
    OPERATION_CHOICES = (
        ('collection', 'Collection/Deposit'),
        ('disbursement', 'Disbursement/Cashout'),
    )
    operation_type = models.CharField(max_length=20, choices=OPERATION_CHOICES)
    phone_number = models.CharField(max_length=15)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='UGX')
    external_reference = models.CharField(max_length=255, unique=True)
    api_response = models.JSONField(blank=True, null=True)
    STATUS_CHOICES = (
        ('initiated', 'Initiated'),
        ('pending', 'Pending'),
        ('successful', 'Successful'),
        ('failed', 'Failed'),
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='initiated')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'mobile_money_transactions'
        ordering = ['-created_at']

    def __str__(self):
        return f"MM({self.operation_type}: {self.amount} - {self.status})"


class ActivityLog(models.Model):
    """Audit trail for all important actions"""
    log_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(
        Users,
        on_delete=models.SET_NULL,
        null=True,
        related_name='activity_logs'
    )
    platform = models.ForeignKey(
        Platform,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_logs'
    )
    ACTION_CHOICES = (
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('register', 'Registration'),
        ('deposit', 'Deposit'),
        ('transfer', 'Transfer'),
        ('cashout', 'Cashout'),
        ('refund', 'Refund'),
        ('dispute_filed', 'Dispute Filed'),
        ('dispute_resolved', 'Dispute Resolved'),
        ('platform_registered', 'Platform Registered'),
        ('settings_updated', 'Settings Updated'),
    )
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    description = models.TextField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'activity_logs'
        ordering = ['-created_at']

    def __str__(self):
        return f"ActivityLog({self.action} by {self.user.email if self.user else 'System'})"





class CashoutRequest(models.Model):
    """
    Seller cashout requests for platform admin review and disbursement.
    
    Flow:
    1. Seller submits request with amount + payment method details
    2. Platform admin reviews and approves/rejects
    3. Admin exports approved requests as CSV (grouped by payment method)
    4. Admin uploads CSV to actual MTN/Airtel/Bank bulk payment portal
    5. Admin marks requests as disbursed → wallet deducted + transaction created
    """
    cashout_id = models.AutoField(primary_key=True)
    seller = models.ForeignKey(
        Users,
        on_delete=models.CASCADE,
        related_name='cashout_requests',
        limit_choices_to={'role': 'seller'}
    )
    platform = models.ForeignKey(
        Platform,
        on_delete=models.CASCADE,
        related_name='cashout_requests'
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='UGX')

    PAYMENT_METHOD_CHOICES = (
        ('mtn_mobile_money', 'MTN Mobile Money'),
        ('airtel_mobile_money', 'Airtel Money'),
        ('bank_transfer', 'Bank Transfer'),
    )
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHOD_CHOICES)

    # Mobile money fields
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    recipient_name = models.CharField(
        max_length=150, blank=True, null=True,
        help_text="Full name of the mobile money or bank account holder"
    )

    # Bank transfer fields
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    account_number = models.CharField(max_length=50, blank=True, null=True)
    account_name = models.CharField(max_length=150, blank=True, null=True)

    STATUS_CHOICES = (
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('disbursed', 'Disbursed'),
        ('failed', 'Failed'),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # Seller note
    seller_note = models.TextField(blank=True, null=True)

    # Admin review fields
    admin_notes = models.TextField(blank=True, null=True)
    reviewed_by = models.ForeignKey(
        Users,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_cashouts',
        limit_choices_to={'role__in': ['admin', 'superadmin']}
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # Disbursement tracking
    disbursed_at = models.DateTimeField(null=True, blank=True)
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cashout_request'
    )
    external_reference = models.CharField(
        max_length=255, blank=True, null=True,
        help_text="Reference from external payment gateway (MTN/Airtel/Bank)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cashout_requests'
        ordering = ['-created_at']

    def __str__(self):
        return f"CashoutRequest({self.seller.email}: {self.amount} {self.currency} via {self.get_payment_method_display()})"

    @property
    def payment_destination(self):
        """Human-readable payment destination"""
        if self.payment_method == 'bank_transfer':
            return f"{self.bank_name} - {self.account_number}"
        return self.phone_number or "N/A"





class BalanceProof(models.Model):
    

    class TierResult(models.TextChoices):
        GREEN   = 'green',   'Can pay all'
        AMBER   = 'amber',   'Can pay some'
        RED     = 'red',     'Cannot pay any'
        UNKNOWN = 'unknown', 'Unknown'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_id       = models.CharField(max_length=50, db_index=True)
    seller_email   = models.EmailField(db_index=True)
    buyer_email    = models.EmailField()   # never sent to shopping app
    order_hash     = models.CharField(max_length=128)

    proof          = models.JSONField(null=True, blank=True)
    public_signals = models.JSONField(null=True, blank=True)
    verified       = models.BooleanField(default=False)

    tier_result    = models.CharField(
        max_length=10,
        choices=TierResult.choices,
        default=TierResult.UNKNOWN,
    )
    items_payable  = models.IntegerField(default=0)
    total_items    = models.IntegerField(default=0)
    binary_bracket = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)

    generated_at   = models.DateTimeField(auto_now_add=True)
    expires_at     = models.DateTimeField(null=True, blank=True)
    refresh_count  = models.IntegerField(default=0)
    include_cod    = models.BooleanField(default=False)

    class Meta:
        # Most recent proof per (order, seller) is what we serve
        ordering = ['-generated_at']
        indexes  = [
            models.Index(fields=['order_id', 'seller_email', '-generated_at'],
                         name='bp_order_seller_idx'),
        ]

    def __str__(self):
        return (f"BalanceProof order={self.order_id} seller={self.seller_email} "
                f"tier={self.tier_result} {self.items_payable}/{self.total_items}")

    @property
    def is_expired(self):
        from django.utils import timezone
        if not self.expires_at:
            return True
        return timezone.now() > self.expires_at



