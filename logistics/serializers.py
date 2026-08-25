from rest_framework import serializers

from logistics.models import Delivery
from users.models import User


class DeliverySerializer(serializers.ModelSerializer):
    courier_name = serializers.ReadOnlyField(source="courier.first_name")
    # Nécessaire à l'écran "Mes revenus" livreur : somme des shipping_fee des livraisons
    # `delivered` du coursier. `order` n'étant qu'une PK, ce montant était sinon inaccessible.
    shipping_fee = serializers.ReadOnlyField(source="order.shipping_fee")
    # Masqué au coursier : ce SerializerMethodField prime sur le champ modèle `confirmation_code`
    # du même nom (déclaration explicite > "__all__"). Ne renvoie la vraie valeur qu'à
    # l'acheteur — le coursier ne doit connaître le code qu'en le recevant en main propre.
    confirmation_code = serializers.SerializerMethodField()

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
            "confirmation_attempts",
            "confirmation_locked_until",
        ]

    def get_confirmation_code(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and getattr(obj.order, "buyer_id", None) == user.id:
            return obj.confirmation_code
        return None


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


class DeliveryConfirmSerializer(serializers.Serializer):
    code = serializers.RegexField(
        regex=r"^\d{6}$", error_messages={"invalid": "Code à 6 chiffres requis."}
    )
