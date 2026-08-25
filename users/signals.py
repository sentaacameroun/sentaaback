import logging

import cloudinary.uploader
from cloudinary.exceptions import Error as CloudinaryError
from django.db.models.signals import post_delete
from django.dispatch import receiver

from users.models import User

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=User)
def delete_courier_document_from_cloudinary(sender, instance, **kwargs):
    resource = instance.courier_id_document
    if not resource:
        return
    try:
        cloudinary.uploader.destroy(
            resource.public_id, resource_type="image", type="authenticated"
        )
    except CloudinaryError:
        logger.exception(
            "Échec de suppression Cloudinary du document coursier (user=%s, public_id=%s)",
            instance.id,
            resource.public_id,
        )
