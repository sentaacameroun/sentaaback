import logging

import cloudinary.uploader
from cloudinary.exceptions import Error as CloudinaryError
from django.db.models.signals import post_delete
from django.dispatch import receiver

from companies.models import CompanyProfile

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=CompanyProfile)
def delete_company_logo_from_cloudinary(sender, instance, **kwargs):
    resource = instance.logo
    if not resource:
        return
    try:
        cloudinary.uploader.destroy(
            resource.public_id, resource_type="image", type="upload"
        )
    except CloudinaryError:
        logger.exception(
            "Échec de suppression Cloudinary du logo entreprise (company=%s, public_id=%s)",
            instance.id,
            resource.public_id,
        )
