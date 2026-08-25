import uuid

from cloudinary.models import CloudinaryField
from django.conf import settings
from django.db import models

from common.images.validators import ImageFileValidator


def company_logo_public_id(instance):
    return f"{settings.CLOUDINARY_FOLDER_PREFIX}/companies/logos/{instance.id}"


class CompanyProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="company_profile",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    logo = CloudinaryField(
        resource_type="image",
        type="upload",
        public_id=company_logo_public_id,
        overwrite=True,
        invalidate=True,
        unique_filename=False,
        use_filename=False,
        allowed_formats=["jpg", "jpeg", "png", "webp"],
        validators=[ImageFileValidator(max_bytes=2 * 1024 * 1024)],
        null=True,
        blank=True,
    )
    sector = models.CharField(max_length=100, blank=True)
    website = models.URLField(blank=True)
    rccm_number = models.CharField(
        max_length=100, blank=True, help_text="Registre du commerce (optionnel)"
    )
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
