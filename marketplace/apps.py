from django.apps import AppConfig


class MarketplaceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "marketplace"

    def ready(self):
        # Enregistre les récepteurs post_delete (nettoyage Cloudinary sur suppression).
        from marketplace import signals  # noqa: F401
