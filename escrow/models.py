import uuid
from django.db import models
from django.conf import settings
from marketplace.models import Listing
from django.core.validators import MinValueValidator

class Order(models.Model):
    """
    Gère le cycle de vie contractuel entre acheteur et vendeur.
    """
    STATUS_CHOICES = (
        ('pending', 'En attente de paiement'),
        ('paid_escrow', 'Payé (Fonds bloqués)'),
        ('shipped', 'Expédié'),
        ('received', 'Reçu (Validation acheteur)'),
        ('completed', 'Terminé (Fonds libérés)'),
        ('disputed', 'En litige'),
        ('refunded', 'Remboursé'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='purchases')
    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name='orders')
    
    # Montants
    item_price = models.DecimalField(max_digits=12, decimal_places=2)
    shipping_fee = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    service_fee = models.DecimalField(max_digits=12, decimal_places=2) # Commission Senta'a
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
  
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

class PaymentTransaction(models.Model):
    """
    Trace brute des échanges avec les APIs Mobile Money.
    """
    PAYMENT_METHODS = (('momo', 'MTN MoMo'), ('om', 'Orange Money'))
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='transactions')
    external_ref = models.CharField(max_length=100, unique=True, null=True, blank=True) # ID de l'opérateur
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=PAYMENT_METHODS)
    
    is_success = models.BooleanField(default=False)
    raw_response = models.JSONField(null=True, blank=True) # Pour le debug/audit
    
    created_at = models.DateTimeField(auto_now_add=True)