import uuid

from django.conf import settings
from django.db import models

from marketplace.models import Listing


class Order(models.Model):
    """
    Gère le cycle de vie contractuel entre acheteur et vendeur.
    """

    STATUS_CHOICES = (
        ("pending", "En attente de paiement"),
        ("paid_escrow", "Payé (Fonds bloqués)"),
        ("shipped", "Expédié"),
        ("received", "Reçu (Validation acheteur)"),
        ("completed", "Terminé (Fonds libérés)"),
        ("disputed", "En litige"),
        ("refunded", "Remboursé"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchases"
    )
    listing = models.ForeignKey(
        Listing, on_delete=models.PROTECT, related_name="orders"
    )
    # Traçabilité : si la commande vient d'une négociation acceptée (marketplace.Offer),
    # item_price est calculé sur offer.proposed_price plutôt que listing.price (voir
    # OrderSerializer.create ci-dessous).
    offer = models.ForeignKey(
        "marketplace.Offer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )

    # Montants
    item_price = models.DecimalField(max_digits=12, decimal_places=2)
    shipping_fee = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    service_fee = models.DecimalField(
        max_digits=12, decimal_places=2
    )  # Commission Senta'a
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True
    )

    # Point de livraison en coordonnées GPS (pas d'adresse texte comme source de vérité)
    destination_latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True
    )
    destination_longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True
    )
    destination_label = models.CharField(max_length=255, blank=True)

    paid_at = models.DateTimeField(null=True, blank=True)
    payout_at = models.DateTimeField(null=True, blank=True)

    # Déduplication des rappels Celery (notifications/tasks.py) : ne relancer qu'une fois.
    payment_reminder_sent_at = models.DateTimeField(null=True, blank=True)
    reception_reminder_sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class PaymentTransaction(models.Model):
    """
    Trace brute des échanges avec l'API NotchPay (MTN MoMo / Orange Money).
    """

    TRANSACTION_TYPES = (
        ("collect", "Collecte (paiement acheteur)"),
        ("withdraw", "Reversement (paiement vendeur)"),
    )
    STATUS_CHOICES = (
        ("pending", "En attente"),
        ("successful", "Réussi"),
        ("failed", "Échoué"),
    )
    CHANNELS = (("cm.mtn", "MTN MoMo"), ("cm.orange", "Orange Money"))

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="transactions"
    )
    transaction_type = models.CharField(
        max_length=10, choices=TRANSACTION_TYPES, default="collect"
    )
    external_ref = models.CharField(
        max_length=100, unique=True, null=True, blank=True
    )  # Référence NotchPay
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    channel = models.CharField(max_length=10, choices=CHANNELS)
    phone_number = models.CharField(max_length=20, blank=True)

    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    is_success = models.BooleanField(default=False)
    raw_response = models.JSONField(null=True, blank=True)  # Pour le debug/audit

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
