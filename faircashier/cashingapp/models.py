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
    phone_number = models.CharField(max_length=15, blank=True, null=True)
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
    domain = models.URLField(max_length=500)
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
    currency = models.CharField(max_length=3, default='UGX')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'wallets'

    def __str__(self):
        return f"Wallet({self.user.email}: {self.balance} {self.currency})"


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
        ('open', 'Open'),
        ('under_review', 'Under Review'),
        ('auto_refunded', 'Auto Refunded'),
        ('escalated', 'Escalated to Admin'),
        ('resolved', 'Resolved'),
        ('rejected', 'Rejected'),
    )
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default='open')
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
