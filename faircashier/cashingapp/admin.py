from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (
    Users, Group, Platform, Wallet, Transaction, PaymentRequest,
    PaymentRequestItem, Dispute, MobileMoneyTransaction, ActivityLog
)


@admin.register(Users)
class UsersAdmin(BaseUserAdmin):
    list_display = ('email', 'role', 'is_active', 'is_staff', 'created_at')
    list_filter = ('role', 'is_active', 'is_staff', 'created_at')
    search_fields = ('email', 'role')
    ordering = ('-created_at',)

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('phone_number', 'admin_email')}),
        ('Permissions', {
         'fields': ('role', 'is_active', 'is_staff', 'is_superuser')}),
        ('Important dates', {'fields': ('created_at', 'updated_at')}),
    )

    readonly_fields = ('created_at', 'updated_at')

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'role', 'phone_number'),
        }),
    )


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'admin', 'superadmin')
    search_fields = ('name',)


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ('platform_name', 'admin', 'domain',
                    'is_active', 'created_at')
    list_filter = ('is_active', 'mobile_money_provider', 'created_at')
    search_fields = ('platform_name', 'domain', 'admin__email')
    readonly_fields = ('api_key', 'created_at', 'updated_at')

    fieldsets = (
        ('Platform Info', {'fields': ('platform_name', 'domain', 'admin')}),
        ('API Configuration', {
         'fields': ('api_key', 'return_url', 'callback_url')}),
        ('Mobile Money', {
         'fields': ('mobile_money_provider', 'mobile_money_api_key')}),
        ('Status', {'fields': ('is_active',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'balance', 'currency', 'is_active', 'updated_at')
    list_filter = ('is_active', 'currency', 'created_at')
    search_fields = ('user__email',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('transaction_id', 'transaction_type',
                    'amount', 'status', 'created_at')
    list_filter = ('transaction_type', 'status', 'created_at')
    search_fields = ('transaction_id',
                     'from_wallet__user__email', 'to_wallet__user__email')
    readonly_fields = ('transaction_id', 'created_at', 'updated_at')

    fieldsets = (
        ('Transaction Info', {
         'fields': ('transaction_id', 'transaction_type', 'status')}),
        ('Amount', {'fields': ('amount', 'currency')}),
        ('Wallets', {'fields': ('from_wallet', 'to_wallet')}),
        ('Platform', {'fields': ('platform',)}),
        ('Details', {'fields': ('description', 'mobile_money_reference')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )


@admin.register(PaymentRequest)
class PaymentRequestAdmin(admin.ModelAdmin):
    list_display = ('request_id', 'platform', 'buyer_email',
                    'total_amount', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('request_id', 'buyer_email', 'platform__platform_name')
    readonly_fields = ('request_id', 'created_at', 'updated_at')

    fieldsets = (
        ('Request Info', {
         'fields': ('request_id', 'platform', 'buyer_email')}),
        ('Amount', {'fields': ('total_amount', 'currency')}),
        ('Status', {'fields': ('status',)}),
        ('Metadata', {'fields': ('metadata',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )


@admin.register(PaymentRequestItem)
class PaymentRequestItemAdmin(admin.ModelAdmin):
    list_display = ('item_id', 'payment_request', 'seller_email',
                    'amount', 'is_cleared', 'created_at')
    list_filter = ('is_cleared', 'created_at')
    search_fields = ('seller_email', 'payment_request__request_id')
    readonly_fields = ('created_at', 'updated_at', 'cleared_at')


@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):
    list_display = ('dispute_id', 'buyer', 'seller',
                    'reason', 'status', 'created_at')
    list_filter = ('status', 'reason', 'created_at')
    search_fields = ('buyer__email', 'seller__email', 'description')
    readonly_fields = ('created_at', 'updated_at', 'resolved_at')

    fieldsets = (
        ('Dispute Info', {'fields': ('dispute_id', 'payment_request_item')}),
        ('Parties', {'fields': ('buyer', 'seller')}),
        ('Details', {'fields': ('reason', 'description')}),
        ('Status', {'fields': ('status', 'refund_transaction')}),
        ('Resolution', {
         'fields': ('resolved_by', 'admin_notes', 'resolved_at')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )


@admin.register(MobileMoneyTransaction)
class MobileMoneyTransactionAdmin(admin.ModelAdmin):
    list_display = ('mm_transaction_id', 'operation_type',
                    'phone_number', 'amount', 'status', 'created_at')
    list_filter = ('operation_type', 'status', 'created_at')
    search_fields = ('phone_number', 'external_reference')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('log_id', 'user', 'action', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('user__email', 'description', 'action')
    readonly_fields = ('created_at',)

    fieldsets = (
        ('Log Info', {'fields': ('log_id', 'user', 'platform')}),
        ('Action', {'fields': ('action', 'description')}),
        ('Metadata', {'fields': ('ip_address', 'metadata')}),
        ('Timestamp', {'fields': ('created_at',)}),
    )
