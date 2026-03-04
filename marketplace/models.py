import uuid
from django.db import models
from users.models import User
from django.core.validators import MinValueValidator

class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, verbose_name="Nom")
    slug = models.SlugField(unique=True)
    icon = models.ImageField(upload_to='categories/', null=True, blank=True)

    class Meta:
        verbose_name = "Catégorie"
        verbose_name_plural = "Catégories"

    def __str__(self):
        return self.name

class Listing(models.Model):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('sold', 'Vendu'),
        ('archived', 'Archivé'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seller = models.ForeignKey(User, on_delete=models.CASCADE, related_name='listings')
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='listings')
    
    title = models.CharField(max_length=255, db_index=True)
    description = models.TextField()
    price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    city = models.CharField(max_length=100, default="Douala") # Focus initial Cameroun
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    is_promoted = models.BooleanField(default=False) # Pour le modèle freemium
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_promoted', '-created_at']
        indexes = [
            models.Index(fields=['status', 'city']),
        ]

    def __str__(self):
        return self.title

class ListingImage(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='listings/')
    is_main = models.BooleanField(default=False) # Image de couverture

    def __str__(self):
        return f"Image for {self.listing.title}"