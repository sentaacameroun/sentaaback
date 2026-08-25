"""
Nettoyage Cloudinary sur suppression (voir common/images/ pour le contexte quota).

`post_delete` plutôt qu'un `.delete()` surchargé sur le modèle : c'est le seul hook qui se
déclenche aussi pour les suppressions en cascade — supprimer une `Listing` cascade sur ses
`ListingImage` (voir marketplace/models.py), et Django envoie ce signal pour CHAQUE ligne
ainsi collectée, pas seulement pour un `.delete()` direct sur `ListingImage`. Un seul
récepteur sur `ListingImage` couvre donc les deux chemins de suppression.s
"""

import logging

import cloudinary.uploader
from cloudinary.exceptions import Error as CloudinaryError
from django.db.models.signals import post_delete
from django.dispatch import receiver

from marketplace.models import Category
from marketplace.models import ListingImage

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Category)
def delete_category_icon_from_cloudinary(sender, instance, **kwargs):
    resource = instance.icon
    if not resource:
        return
    try:
        cloudinary.uploader.destroy(
            resource.public_id, resource_type="image", type="upload"
        )
    except CloudinaryError:
        logger.exception(
            "Échec de suppression Cloudinary de l'icône catégorie (category=%s, public_id=%s)",
            instance.id,
            resource.public_id,
        )


@receiver(post_delete, sender=ListingImage)
def delete_listing_image_from_cloudinary(sender, instance, **kwargs):
    resource = instance.image
    if not resource:
        return
    try:
        cloudinary.uploader.destroy(
            resource.public_id, resource_type="image", type="upload"
        )
    except CloudinaryError:
        logger.exception(
            "Échec de suppression Cloudinary d'une image d'annonce"
            " (listing_image=%s, public_id=%s)",
            instance.id,
            resource.public_id,
        )
