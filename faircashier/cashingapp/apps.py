from django.apps import AppConfig
from django.db.models.signals import post_migrate


class PaymentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cashingapp'

    def ready(self):
        from . import signals  # Import the signals module

        # Connect the post_migrate signal to create the anonymous user
        post_migrate.connect(signals.create_anonymous_user, sender=self)
