import uuid

from django.db import models

from users.models import User


class Delivery(models.Model):
    STATUS_CHOICES = (
        ("pending_assignment", "En attente d'assignation"),
        ("assigned", "Assignée"),
        ("picked_up", "Récupérée"),
        ("in_transit", "En cours de livraison"),
        ("delivered", "Livrée"),
        ("failed", "Échouée"),
    )

    order = models.OneToOneField(
        "escrow.Order", on_delete=models.CASCADE, related_name="delivery"
    )
    courier = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"is_courier": True},
    )
    tracking_number = models.CharField(max_length=100, unique=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending_assignment",
        db_index=True,
    )

    # Coordonnées GPS uniquement (pas d'adresse texte comme source de vérité)
    pickup_latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    pickup_longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    destination_latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    destination_longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )

    # Suivi en temps réel de la position du coursier pendant in_transit
    courier_latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    courier_longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    courier_location_updated_at = models.DateTimeField(null=True, blank=True)

    estimated_arrival = models.DateTimeField(null=True, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.tracking_number:
            self.tracking_number = f"SENTAA-{uuid.uuid4().hex[:10].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        courier_name = self.courier.first_name if self.courier else "Unassigned"
        return f"Delivery for Order {self.order.id} - Courier: {courier_name}"

    class Meta:
        app_label = "logistics"
        verbose_name = "Delivery"
        verbose_name_plural = "Deliveries"
        db_table = "deliveries"
        ordering = ["-created_at"]
