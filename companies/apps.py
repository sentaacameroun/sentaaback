from django.apps import AppConfig


class CompaniesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "companies"

    def ready(self):
        # Enregistre les récepteurs post_delete (nettoyage Cloudinary sur suppression).
        from companies import signals  # noqa: F401
