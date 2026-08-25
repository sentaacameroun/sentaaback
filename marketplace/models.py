import uuid

from cloudinary.models import CloudinaryField
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from common.images.validators import ImageFileValidator
from users.models import User


# Fonctions nommées au niveau module (pas des lambdas) : `makemigrations` doit pouvoir les
# sérialiser par chemin d'import dans le fichier de migration.
def category_icon_public_id(instance):
    # Déterministe par catégorie : remplacer l'icône (admin) écrase l'existante sur Cloudinary
    # (overwrite=True ci-dessous) au lieu d'en laisser une orpheline.
    return f"{settings.CLOUDINARY_FOLDER_PREFIX}/categories/icons/{instance.id}"


def listing_image_public_id(instance):
    # Une ListingImage = une image permanente et distincte (jamais remplacée en place, voir
    # marketplace/views.py::_attach_uploaded_images) : id de ligne pas encore connu à ce stade
    # (PK auto-incrémentée, assignée seulement après l'INSERT), d'où l'uuid4 dédié plutôt que
    # `instance.pk`. `listing_id` regroupe les images d'une même annonce dans un même dossier.
    return f"{settings.CLOUDINARY_FOLDER_PREFIX}/listings/{instance.listing_id}/{uuid.uuid4()}"


class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, verbose_name="Nom")
    slug = models.SlugField(unique=True)
    icon = CloudinaryField(
        resource_type="image",
        type="upload",
        public_id=category_icon_public_id,
        overwrite=True,
        invalidate=True,
        unique_filename=False,
        use_filename=False,
        allowed_formats=["jpg", "jpeg", "png", "webp"],
        validators=[ImageFileValidator(max_bytes=2 * 1024 * 1024)],
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Catégorie"
        verbose_name_plural = "Catégories"

    def __str__(self):
        return self.name


class Listing(models.Model):
    STATUS_CHOICES = (
        ("active", "Active"),
        ("sold", "Vendu"),
        ("archived", "Archivé"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seller = models.ForeignKey(User, on_delete=models.CASCADE, related_name="listings")
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="listings"
    )
    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="listings",
    )

    title = models.CharField(max_length=255, db_index=True)
    description = models.TextField()
    price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    city = models.CharField(max_length=100, default="Douala")  # Focus initial Cameroun

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    is_promoted = models.BooleanField(default=False)  # Pour le modèle freemium

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_promoted", "-created_at"]
        indexes = [
            models.Index(fields=["status", "city"]),
        ]

    def __str__(self):
        return self.title


class ListingImage(models.Model):
    listing = models.ForeignKey(
        Listing, on_delete=models.CASCADE, related_name="images"
    )
    image = CloudinaryField(
        resource_type="image",
        type="upload",
        public_id=listing_image_public_id,
        overwrite=False,  # chaque ligne est une image distincte, jamais remplacée en place
        unique_filename=False,
        use_filename=False,
        allowed_formats=["jpg", "jpeg", "png", "webp"],
        validators=[ImageFileValidator(max_bytes=5 * 1024 * 1024)],
    )
    is_main = models.BooleanField(default=False)  # Image de couverture

    def __str__(self):
        return f"Image for {self.listing.title}"


class Offer(models.Model):
    STATUS_CHOICES = (
        ("pending", "En attente"),
        ("countered", "Contre-offre"),
        ("accepted", "Acceptée"),
        ("rejected", "Refusée"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing = models.ForeignKey(
        Listing, on_delete=models.CASCADE, related_name="offers"
    )
    buyer = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="offers_made"
    )
    proposed_price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    last_offered_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="offers_proposed"
    )
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("listing", "buyer")
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Offre {self.proposed_price} sur {self.listing.title} ({self.status})"


class ListingFavorite(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="favorite_listings"
    )
    listing = models.ForeignKey(
        Listing, on_delete=models.CASCADE, related_name="favorited_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "listing")
