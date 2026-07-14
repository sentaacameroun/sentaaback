import uuid

from django.core.validators import MinValueValidator
from django.db import models

from users.models import User


class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, verbose_name="Nom")
    slug = models.SlugField(unique=True)
    icon = models.ImageField(upload_to="categories/", null=True, blank=True)

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
    image = models.ImageField(upload_to="listings/")
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
