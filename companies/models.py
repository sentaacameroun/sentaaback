import uuid

from django.conf import settings
from django.db import models


class CompanyProfile(models.Model):
    """
    Profil entreprise optionnel et additif : n'importe quel compte peut s'en créer un
    pour vendre/recruter sous une bannière pro, sans que ça remplace le compte personnel.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="company_profile",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to="companies/", null=True, blank=True)
    sector = models.CharField(max_length=100, blank=True)
    website = models.URLField(blank=True)
    rccm_number = models.CharField(
        max_length=100, blank=True, help_text="Registre du commerce (optionnel)"
    )
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
