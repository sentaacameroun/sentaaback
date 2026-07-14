from rest_framework import serializers

from logistics.models import Delivery
from users.models import User


class DeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = Delivery
        fields = "__all__"
        read_only_fields = [
            "order",
            "courier",
            "tracking_number",
            "status",
            "pickup_latitude",
            "pickup_longitude",
            "destination_latitude",
            "destination_longitude",
            "courier_latitude",
            "courier_longitude",
            "courier_location_updated_at",
            "assigned_at",
            "picked_up_at",
            "delivered_at",
        ]


class DeliveryAssignSerializer(serializers.Serializer):
    courier = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_courier=True)
    )


class DeliveryStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Delivery.STATUS_CHOICES)
    notes = serializers.CharField(required=False, allow_blank=True)


class DeliveryLocationUpdateSerializer(serializers.Serializer):
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6)
