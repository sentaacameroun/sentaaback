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
        # Étape intermédiaire (PR 3) : le coursier a livré (code saisi), mais les fonds
        # ne sont pas encore libérés. Seule la confirmation acheteur (complete_and_release)
        # fait passer delivered → completed. Voir escrow/services/order_lifecycle.py.
        ("delivered", "Livré (En attente confirmation acheteur)"),
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
    Trace brute des échanges avec les fournisseurs de paiement Mobile Money (NotchPay, KPay,
    MoneyFusion — voir escrow/services/providers/, chaîne de fallback avec résilience
    automatique en cas d'indisponibilité d'un fournisseur).
    """

    TRANSACTION_TYPES = (
        ("collect", "Collecte (paiement acheteur)"),
        ("withdraw", "Reversement (paiement vendeur)"),
        ("courier_payout", "Reversement (paiement livreur)"),
    )
    STATUS_CHOICES = (
        ("pending", "En attente"),
        ("successful", "Réussi"),
        ("failed", "Échoué"),
    )
    # Canal abstrait, indépendant du fournisseur ayant traité la transaction — chaque
    # fournisseur traduit vers son propre code (voir escrow/services/providers/*.py).
    CHANNELS = (("mtn", "MTN MoMo"), ("orange", "Orange Money"))
    PROVIDERS = (
        ("notchpay", "NotchPay"),
        ("kpay", "KPay"),
        ("moneyfusion", "MoneyFusion"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # PROTECT : le journal financier ne doit jamais être supprimé par cascade quand une
    # Order est supprimée. Toute tentative de suppression d'une commande ayant des
    # transactions lève une IntegrityError au niveau base (voir escrow/tests.py).
    order = models.ForeignKey(
        Order, on_delete=models.PROTECT, related_name="transactions"
    )
    transaction_type = models.CharField(
        max_length=20, choices=TRANSACTION_TYPES, default="collect"
    )
    # Référence interne générée par Senta'a (idempotence), propagée tel quel au fournisseur
    # retenu par le router — stable quel que soit le fournisseur ayant effectivement traité
    # l'opération.
    external_ref = models.CharField(max_length=100, unique=True, null=True, blank=True)
    # Clé d'idempotence applicative : garantit qu'une même opération (collecte, reversement)
    # ne peut produire qu'une seule ligne, via une contrainte unique en base plutôt qu'une
    # simple garde applicative. Nullable en transition (lignes créées avant l'ajout du champ).
    idempotency_key = models.CharField(
        max_length=255, unique=True, null=True, blank=True
    )
    # Fournisseur ayant effectivement traité la transaction, et sa propre référence (son id/
    # token) — distincte de `external_ref`, utile pour la traçabilité/réconciliation avec le
    # tableau de bord du fournisseur. `blank=True` pour compatibilité avec les lignes créées
    # avant l'ajout de ce champ.
    provider = models.CharField(max_length=20, choices=PROVIDERS, blank=True)
    provider_reference = models.CharField(max_length=150, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    channel = models.CharField(max_length=10, choices=CHANNELS)
    phone_number = models.CharField(max_length=20, blank=True)

    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    is_success = models.BooleanField(default=False)
    raw_response = models.JSONField(null=True, blank=True)  # Pour le debug/audit

    # Nombre de passages de la tâche de réconciliation (escrow/tasks.py) sur ce reversement
    # resté `pending`. Borne le nombre de re-vérifications (pas de retry infini silencieux) :
    # au-delà du seuil, la ligne est sortie de la file et loggée pour intervention manuelle.
    reconciliation_attempts = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
