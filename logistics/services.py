import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from logistics.geo import haversine_km
from logistics.models import Delivery

logger = logging.getLogger(__name__)


def create_delivery_for_order(order):
    """Auto-crée une Delivery non assignée dès que l'order passe en paid_escrow."""
    seller = order.listing.seller
    delivery, created = Delivery.objects.get_or_create(
        order=order,
        defaults={
            "destination_latitude": order.destination_latitude,
            "destination_longitude": order.destination_longitude,
            "pickup_latitude": seller.latitude,
            "pickup_longitude": seller.longitude,
        },
    )
    if created:
        notify_nearby_couriers(delivery)
    return delivery


def notify_nearby_couriers(delivery, radius_km=5, limit=5):
    """
    Diffuse la disponibilité d'une livraison aux coursiers en ligne les plus proches du point
    de pickup. Attribution "premier arrivé, premier servi" côté client via
    DeliveryViewSet.claim (atomique) — ce push est une optimisation de dispatch, pas une porte
    de sécurité : n'importe quel coursier en ligne peut réclamer via claim/, notifié ou non.
    L'attribution manuelle par le staff (assign/) reste disponible en parallèle à tout moment.
    """
    if delivery.pickup_latitude is None or delivery.pickup_longitude is None:
        logger.info(
            "Delivery %s sans coordonnées de pickup, pas de dispatch.", delivery.id
        )
        return

    from users.models import User

    candidates = User.objects.filter(
        is_courier=True,
        is_available=True,
        latitude__isnull=False,
        longitude__isnull=False,
    )
    nearby = []
    for courier in candidates:
        distance = haversine_km(
            delivery.pickup_latitude,
            delivery.pickup_longitude,
            courier.latitude,
            courier.longitude,
        )
        if distance <= radius_km:
            nearby.append((distance, courier))
    nearby.sort(key=lambda pair: pair[0])

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    for _, courier in nearby[:limit]:
        async_to_sync(channel_layer.group_send)(
            f"courier_{courier.id}",
            {
                "type": "delivery.available",
                "delivery": {
                    "id": str(delivery.id),
                    "pickup_latitude": str(delivery.pickup_latitude),
                    "pickup_longitude": str(delivery.pickup_longitude),
                    "destination_latitude": str(delivery.destination_latitude)
                    if delivery.destination_latitude
                    else None,
                    "destination_longitude": str(delivery.destination_longitude)
                    if delivery.destination_longitude
                    else None,
                },
            },
        )
