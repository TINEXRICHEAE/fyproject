# cashingapp/management/commands/populate_payment_app.py

from django.core.management.base import BaseCommand
from django.utils import timezone
from decimal import Decimal
import uuid
from cashingapp.models import (
    Users, Wallet, Platform, Transaction, 
    PaymentRequest, PaymentRequestItem, ActivityLog
)


class Command(BaseCommand):
    help = 'Populates the payment app with sample data for all user types'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(self.style.SUCCESS('FAIR CASHIER - POPULATING DATABASE'))
        self.stdout.write(self.style.SUCCESS('='*60 + '\n'))

        # ========================================
        # 1. CREATE SUPERADMIN
        # ========================================
        self.stdout.write(self.style.SUCCESS('\n[1/5] Creating Superadmin...'))
        
        superadmin_email = 'tinexnox@gmail.com'
        superadmin, created = Users.objects.get_or_create(
            email=superadmin_email,
            defaults={
                'role': 'superadmin',
                'is_staff': True,
                'is_superuser': True,
                'is_active': True
            }
        )
        if created:
            superadmin.set_password('Nicole@2022')
            superadmin.save()
            self.stdout.write(self.style.SUCCESS(
                f'✅ Created superadmin: {superadmin_email}'))
        else:
            self.stdout.write(self.style.WARNING(
                f'⚠️  Superadmin already exists: {superadmin_email}'))
        
        # Create wallet for superadmin
        superadmin_wallet, _ = Wallet.objects.get_or_create(
            user=superadmin,
            defaults={'balance': Decimal('1000000.00'), 'currency': 'UGX'}
        )
        self.stdout.write(self.style.SUCCESS(
            f'✅ Created wallet for superadmin: {superadmin_wallet.balance} UGX'))

        # ========================================
        # 2. CREATE ADMINS WITH PLATFORMS
        # ========================================
        self.stdout.write(self.style.SUCCESS('\n[2/5] Creating Admins and Platforms...'))
        
        admin_data = [
            {
                'email': 'admin1@marketapp.com',
                'password': 'admin123',
                'platform_name': 'Market E-commerce',
                'domain': 'http://localhost:8000',
                'return_url': 'http://localhost:8000/payment/complete/',
                'callback_url': 'http://localhost:8000/api/payment/callback/'
            },
            {
                'email': 'admin2@shopapp.com',
                'password': 'admin456',
                'platform_name': 'Shop App',
                'domain': 'http://127.0.0.1:8000',
                'return_url': 'http://127.0.0.1:8000/payment/complete/',
                'callback_url': 'http://127.0.0.1:8000/api/payment/callback/'
            }
        ]
        
        admins = []
        platforms = []
        
        for data in admin_data:
            admin, created = Users.objects.get_or_create(
                email=data['email'],
                defaults={
                    'role': 'admin',
                    'is_staff': True,
                    'is_superuser': False,
                    'is_active': True
                }
            )
            if created:
                admin.set_password(data['password'])
                admin.save()
                self.stdout.write(self.style.SUCCESS(
                    f'✅ Created admin: {data["email"]}'))
            else:
                self.stdout.write(self.style.WARNING(
                    f'⚠️  Admin already exists: {data["email"]}'))
            
            admins.append(admin)
            
            # Create wallet for admin
            admin_wallet, _ = Wallet.objects.get_or_create(
                user=admin,
                defaults={'balance': Decimal('500000.00'), 'currency': 'UGX'}
            )
            
            # Create platform for admin
            platform, created = Platform.objects.get_or_create(
                admin=admin,
                platform_name=data['platform_name'],
                defaults={
                    'domain': data['domain'],
                    'return_url': data['return_url'],
                    'callback_url': data['callback_url'],
                    'mobile_money_provider': 'mtn',
                    'is_active': True
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(
                    f'✅ Created platform: {platform.platform_name}'))
                self.stdout.write(self.style.SUCCESS(
                    f'   API Key: {platform.api_key}'))
            else:
                self.stdout.write(self.style.WARNING(
                    f'⚠️  Platform already exists: {platform.platform_name}'))
            
            platforms.append(platform)

        # ========================================
        # 3. CREATE SELLERS (same as market app)
        # ========================================
        self.stdout.write(self.style.SUCCESS('\n[3/5] Creating Sellers...'))
        
        seller_emails = [
            'seller1@example.com',
            'seller2@example.com',
            'seller3@example.com'
        ]
        
        sellers = []
        for email in seller_emails:
            seller, created = Users.objects.get_or_create(
                email=email,
                defaults={
                    'role': 'seller',
                    'is_staff': False,
                    'is_superuser': False,
                    'is_active': True
                }
            )
            if created:
                # Set unusable password (PIN-based auth)
                seller.set_unusable_password()
                seller.save()
                self.stdout.write(self.style.SUCCESS(
                    f'✅ Created seller: {email}'))
            else:
                self.stdout.write(self.style.WARNING(
                    f'⚠️  Seller already exists: {email}'))
            
            sellers.append(seller)
            
            # Create wallet with initial balance
            initial_balance = Decimal('100000.00') if created else Decimal('0.00')
            seller_wallet, _ = Wallet.objects.get_or_create(
                user=seller,
                defaults={'balance': initial_balance, 'currency': 'UGX'}
            )
            
            # Set default PIN for sellers (in production, they'd set their own)
            if not seller.pin:
                from cashingapp.pin_auth import PINAuthenticator
                pin_result = PINAuthenticator.set_pin(seller, '8001', '8001')
                if pin_result['success']:
                    self.stdout.write(self.style.SUCCESS(
                        f'   PIN set: 8001 | Wallet: {seller_wallet.balance} UGX'))
                else:
                    self.stdout.write(self.style.ERROR(
                        f'   Failed to set PIN: {pin_result["error"]}'))

        # ========================================
        # 4. CREATE BUYERS (same as market app)
        # ========================================
        self.stdout.write(self.style.SUCCESS('\n[4/5] Creating Buyers...'))
        
        buyer_emails = [
            'buyer1@example.com',
            'buyer2@example.com',
            'buyer3@example.com',
            'buyer4@example.com',
            'buyer5@example.com'
        ]
        
        buyers = []
        for email in buyer_emails:
            buyer, created = Users.objects.get_or_create(
                email=email,
                defaults={
                    'role': 'buyer',
                    'is_staff': False,
                    'is_superuser': False,
                    'is_active': True
                }
            )
            if created:
                # Set unusable password (PIN-based auth)
                buyer.set_unusable_password()
                buyer.save()
                self.stdout.write(self.style.SUCCESS(
                    f'✅ Created buyer: {email}'))
            else:
                self.stdout.write(self.style.WARNING(
                    f'⚠️  Buyer already exists: {email}'))
            
            buyers.append(buyer)
            
            # Create wallet with initial balance
            initial_balance = Decimal('50000.00') if created else Decimal('0.00')
            buyer_wallet, _ = Wallet.objects.get_or_create(
                user=buyer,
                defaults={'balance': initial_balance, 'currency': 'UGX'}
            )
            
            # Set default PIN for buyers
            if not buyer.pin:
                from cashingapp.pin_auth import PINAuthenticator
                pin_result = PINAuthenticator.set_pin(buyer, '8000', '8000')
                if pin_result['success']:
                    self.stdout.write(self.style.SUCCESS(
                        f'   PIN set: 8000 | Wallet: {buyer_wallet.balance} UGX'))
                else:
                    self.stdout.write(self.style.ERROR(
                        f'   Failed to set PIN: {pin_result["error"]}'))

        # ========================================
        # 5. CREATE SAMPLE TRANSACTIONS
        # ========================================
        self.stdout.write(self.style.SUCCESS('\n[5/5] Creating Sample Transactions...'))
        
        # Create some deposit transactions for buyers
        sample_transactions = [
            {
                'from_user': None,
                'to_user': buyers[0],
                'amount': Decimal('20000.00'),
                'type': 'deposit',
                'platform': platforms[0]
            },
            {
                'from_user': None,
                'to_user': buyers[1],
                'amount': Decimal('15000.00'),
                'type': 'deposit',
                'platform': platforms[1]
            },
            {
                'from_user': buyers[0],
                'to_user': sellers[0],
                'amount': Decimal('5000.00'),
                'type': 'transfer',
                'platform': platforms[0]
            },
            {
                'from_user': buyers[1],
                'to_user': sellers[1],
                'amount': Decimal('8000.00'),
                'type': 'transfer',
                'platform': platforms[1]
            },
        ]
        
        for tx_data in sample_transactions:
            try:
                from_wallet = tx_data['from_user'].wallet if tx_data['from_user'] else None
                to_wallet = tx_data['to_user'].wallet if tx_data['to_user'] else None
                
                transaction = Transaction.objects.create(
                    platform=tx_data['platform'],
                    from_wallet=from_wallet,
                    to_wallet=to_wallet,
                    amount=tx_data['amount'],
                    currency='UGX',
                    transaction_type=tx_data['type'],
                    status='completed',
                    description=f'Sample {tx_data["type"]} transaction'
                )
                
                # Update wallet balances
                if from_wallet:
                    from_wallet.balance -= tx_data['amount']
                    from_wallet.save()
                
                if to_wallet:
                    to_wallet.balance += tx_data['amount']
                    to_wallet.save()
                
                self.stdout.write(self.style.SUCCESS(
                    f'✅ Created {tx_data["type"]}: {tx_data["amount"]} UGX'))
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'❌ Failed to create transaction: {str(e)}'))

        # ========================================
        # SUMMARY
        # ========================================
        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(self.style.SUCCESS('POPULATION COMPLETE!'))
        self.stdout.write(self.style.SUCCESS('='*60))
        
        self.stdout.write(self.style.SUCCESS(f'\n📊 SUMMARY:'))
        self.stdout.write(self.style.SUCCESS(f'   Superadmin: 1'))
        self.stdout.write(self.style.SUCCESS(f'   Admins: {len(admins)}'))
        self.stdout.write(self.style.SUCCESS(f'   Platforms: {len(platforms)}'))
        self.stdout.write(self.style.SUCCESS(f'   Sellers: {len(sellers)}'))
        self.stdout.write(self.style.SUCCESS(f'   Buyers: {len(buyers)}'))
        self.stdout.write(self.style.SUCCESS(f'   Transactions: {Transaction.objects.count()}'))
        self.stdout.write(self.style.SUCCESS(f'   Total Wallets: {Wallet.objects.count()}'))
        
        self.stdout.write(self.style.SUCCESS(f'\n🔐 LOGIN CREDENTIALS:'))
        self.stdout.write(self.style.SUCCESS(f'   Superadmin:'))
        self.stdout.write(self.style.SUCCESS(f'      Email: tinexnox@gmail.com'))
        self.stdout.write(self.style.SUCCESS(f'      Password: Nicole@2022'))
        
        self.stdout.write(self.style.SUCCESS(f'\n   Admins:'))
        for i, admin in enumerate(admins):
            self.stdout.write(self.style.SUCCESS(
                f'      {i+1}. Email: {admin.email} | Password: {admin_data[i]["password"]}'))
        
        self.stdout.write(self.style.SUCCESS(f'\n   Sellers (PIN-based):'))
        for seller in sellers:
            wallet = seller.wallet
            self.stdout.write(self.style.SUCCESS(
                f'      Email: {seller.email} | PIN: 1234 | Balance: {wallet.balance} UGX'))
        
        self.stdout.write(self.style.SUCCESS(f'\n   Buyers (PIN-based):'))
        for buyer in buyers:
            wallet = buyer.wallet
            self.stdout.write(self.style.SUCCESS(
                f'      Email: {buyer.email} | PIN: 0000 | Balance: {wallet.balance} UGX'))
        
        self.stdout.write(self.style.SUCCESS(f'\n   Platforms:'))
        for platform in platforms:
            self.stdout.write(self.style.SUCCESS(
                f'      {platform.platform_name}:'))
            self.stdout.write(self.style.SUCCESS(
                f'         API Key: {platform.api_key}'))
            self.stdout.write(self.style.SUCCESS(
                f'         Domain: {platform.domain}'))
        
        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(self.style.SUCCESS('READY TO USE!'))
        self.stdout.write(self.style.SUCCESS('='*60 + '\n'))