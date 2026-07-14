from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins
from rest_framework import permissions
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from logistics.models import Delivery
from logistics.permissions import IsAssignedCourier
from logistics.permissions import IsCourier
from logistics.serializers import DeliveryAssignSerializer
from logistics.serializers import DeliveryLocationUpdateSerializer
from logistics.serializers import DeliverySerializer
from logistics.serializers import DeliveryStatusUpdateSerializer

# Machine à états : depuis quel statut peut-on aller vers quels statuts suivants.
TRANSITIONS = {
    "assigned": {"picked_up", "failed"},
    "picked_up": {"in_transit", "failed"},
    "in_transit": {"delivered", "failed"},
}


class DeliveryViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """
    Pas de création/suppression manuelle : une Delivery est auto-créée
    (escrow.services.delivery_hooks.on_order_paid) quand l'Order passe en paid_escrow.
    """

    serializer_class = DeliverySerializer

    def get_queryset(self):
        user = self.request.user
        qs = Delivery.objects.select_related(
            "order", "order__listing", "order__buyer", "courier"
        )
        if user.is_staff:
            return qs
        return qs.filter(
            Q(courier=user) | Q(order__buyer=user) | Q(order__listing__seller=user)
        ).distinct()

    def get_permissions(self):
        if self.action == "assign":
            return [permissions.IsAdminUser()]
        if self.action == "claim":
            return [permissions.IsAuthenticated(), IsCourier()]
        if self.action in ("update_status", "update_location"):
            return [permissions.IsAuthenticated(), IsAssignedCourier()]
        return [permissions.IsAuthenticated()]

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        delivery = self.get_object()
        serializer = DeliveryAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        delivery.courier = serializer.validated_data["courier"]
        delivery.status = "assigned"
        delivery.assigned_at = timezone.now()
        delivery.save(update_fields=["courier", "status", "assigned_at"])
        return Response(DeliverySerializer(delivery).data)

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        """
        Auto-attribution "premier arrivé, premier servi" : un coursier en ligne réclame une
        livraison notifiée (ou non — voir notify_nearby_couriers) par son id. Update atomique
        conditionnel : si un autre coursier a déjà réclamé entre-temps, 0 ligne affectée -> 409.
        Coexiste en permanence avec `assign/` (staff) ; le staff peut réassigner à tout moment.
        """
        updated = Delivery.objects.filter(
            pk=pk, status="pending_assignment", courier__isnull=True
        ).update(courier=request.user, status="assigned", assigned_at=timezone.now())
        if not updated:
            return Response(
                {"error": "Cette livraison n'est plus disponible."}, status=409
            )
        delivery = Delivery.objects.get(pk=pk)
        return Response(DeliverySerializer(delivery).data)

    @action(detail=True, methods=["post"], url_path="update-status")
    def update_status(self, request, pk=None):
        delivery = self.get_object()
        serializer = DeliveryStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        allowed = TRANSITIONS.get(delivery.status, set())
        if new_status not in allowed:
            return Response(
                {
                    "error": f"Transition {delivery.status} -> {new_status} non autorisée"
                },
                status=400,
            )

        delivery.status = new_status
        update_fields = ["status"]
        if "notes" in serializer.validated_data:
            delivery.notes = serializer.validated_data["notes"]
            update_fields.append("notes")

        now = timezone.now()
        if new_status == "picked_up":
            delivery.picked_up_at = now
            update_fields.append("picked_up_at")
            order = delivery.order
            order.status = "shipped"
            order.save(update_fields=["status"])
        elif new_status == "delivered":
            delivery.delivered_at = now
            update_fields.append("delivered_at")

        delivery.save(update_fields=update_fields)
        return Response(DeliverySerializer(delivery).data)

    @action(detail=True, methods=["post"], url_path="update-location")
    def update_location(self, request, pk=None):
        delivery = self.get_object()
        if delivery.status != "in_transit":
            return Response(
                {
                    "error": "Le suivi de position n'est actif que pendant le transit (in_transit)"
                },
                status=400,
            )

        serializer = DeliveryLocationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        delivery.courier_latitude = serializer.validated_data["latitude"]
        delivery.courier_longitude = serializer.validated_data["longitude"]
        delivery.courier_location_updated_at = timezone.now()
        delivery.save(
            update_fields=[
                "courier_latitude",
                "courier_longitude",
                "courier_location_updated_at",
            ]
        )

        # Push temps réel vers buyer/seller connectés au websocket de suivi (Phase C).
        channel_layer = get_channel_layer()
        if channel_layer is not None:
            async_to_sync(channel_layer.group_send)(
                f"delivery_{delivery.id}",
                {
                    "type": "location.update",
                    "location": {
                        "latitude": str(delivery.courier_latitude),
                        "longitude": str(delivery.courier_longitude),
                        "updated_at": delivery.courier_location_updated_at.isoformat(),
                    },
                },
            )
        return Response(DeliverySerializer(delivery).data)
