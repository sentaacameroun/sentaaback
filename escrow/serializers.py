from decimal import Decimal

from rest_framework import serializers

from .models import Order
from .models import PaymentTransaction
from marketplace.models import Offer


class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = "__all__"
        read_only_fields = [
            "buyer",
            "status",
            "item_price",
            "service_fee",
            "total_amount",
            "paid_at",
            "payout_at",
            "offer",
        ]

    def validate(self, data):
        listing = data["listing"]
        if listing.seller == self.context["request"].user:
            raise serializers.ValidationError(
                "Vous ne pouvez pas acheter votre propre article."
            )
        if listing.status != "active":
            raise serializers.ValidationError("Cet article n'est plus disponible.")
        return data

    def create(self, validated_data):
        listing = validated_data["listing"]
        buyer = self.context["request"].user

        # Une négociation acceptée (marketplace.Offer) prime sur le prix catalogue.
        accepted_offer = Offer.objects.filter(
            listing=listing, buyer=buyer, status="accepted"
        ).first()
        price = accepted_offer.proposed_price if accepted_offer else listing.price

        service_fee = price * Decimal("0.03")
        total = price + service_fee + validated_data.get("shipping_fee", 0)

        return Order.objects.create(
            **validated_data,
            buyer=buyer,
            offer=accepted_offer,
            item_price=price,
            service_fee=service_fee,
            total_amount=total
        )


class InitiatePaymentSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    channel = serializers.ChoiceField(choices=PaymentTransaction.CHANNELS)
