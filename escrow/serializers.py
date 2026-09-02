from decimal import Decimal

from rest_framework import serializers

from .models import Order
from .models import PaymentTransaction
from logistics.geo import haversine_km
from marketplace.models import Offer

# Barème de livraison calculé côté serveur — jamais fourni par le client (voir
# .claude/rules/escrow-core.md : ne jamais faire confiance à un montant envoyé par le
# client). Forfait de base + tarif au km sur la distance vendeur → destination. La logique
# métier définitive reste une question ouverte (REFACTOR_PLAN.md §3) ; ce barème est un
# défaut sûr, l'essentiel du correctif est que le champ soit read_only et recalculé ici.
SHIPPING_BASE_FEE = Decimal("500")
SHIPPING_FEE_PER_KM = Decimal("100")


class OrderSerializer(serializers.ModelSerializer):
    listing_title = serializers.ReadOnlyField(source="listing.title")
    listing_image = serializers.SerializerMethodField()
    seller = serializers.ReadOnlyField(source="listing.seller_id")
    seller_name = serializers.ReadOnlyField(source="listing.seller.first_name")

    class Meta:
        model = Order
        fields = "__all__"
        read_only_fields = [
            "buyer",
            "status",
            "item_price",
            "service_fee",
            "shipping_fee",
            "total_amount",
            "paid_at",
            "payout_at",
            "offer",
        ]

    def get_listing_image(self, obj):
        image = (
            obj.listing.images.filter(is_main=True).first()
            or obj.listing.images.first()
        )
        if not image:
            return None
        request = self.context.get("request")
        url = image.image.url
        return request.build_absolute_uri(url) if request else url

    def validate(self, data):
        listing = data["listing"]
        if listing.seller == self.context["request"].user:
            raise serializers.ValidationError(
                "Vous ne pouvez pas acheter votre propre article."
            )
        if listing.status != "active":
            raise serializers.ValidationError("Cet article n'est plus disponible.")
        return data

    def _compute_shipping_fee(self, listing, validated_data):
        # Distance vendeur → point de livraison. Si l'une des coordonnées manque
        # (vendeur sans position, ou commande sans destination GPS), on retombe sur le
        # forfait de base plutôt que d'exposer un montant nul manipulable.
        seller = listing.seller
        dest_lat = validated_data.get("destination_latitude")
        dest_lon = validated_data.get("destination_longitude")
        if None in (seller.latitude, seller.longitude, dest_lat, dest_lon):
            return SHIPPING_BASE_FEE

        distance_km = haversine_km(
            seller.latitude, seller.longitude, dest_lat, dest_lon
        )
        # haversine_km renvoie un float : passer par str() pour une conversion Decimal
        # exacte (jamais float * Decimal sur de l'argent).
        fee = SHIPPING_BASE_FEE + SHIPPING_FEE_PER_KM * Decimal(str(distance_km))
        return fee.quantize(Decimal("0.01"))

    def create(self, validated_data):
        listing = validated_data["listing"]
        buyer = self.context["request"].user

        # Une négociation acceptée (marketplace.Offer) prime sur le prix catalogue.
        accepted_offer = Offer.objects.filter(
            listing=listing, buyer=buyer, status="accepted"
        ).first()
        price = accepted_offer.proposed_price if accepted_offer else listing.price

        service_fee = price * Decimal("0.03")
        # `shipping_fee` est read_only : recalculé côté serveur, jamais lu depuis
        # l'entrée client (voir read_only_fields).
        shipping_fee = self._compute_shipping_fee(listing, validated_data)
        total = price + service_fee + shipping_fee

        return Order.objects.create(
            **validated_data,
            buyer=buyer,
            offer=accepted_offer,
            item_price=price,
            service_fee=service_fee,
            shipping_fee=shipping_fee,
            total_amount=total
        )


class InitiatePaymentSerializer(serializers.Serializer):
    """
    Deux façons d'initier un paiement (voir escrow/services/providers/ — seul KPay
    distingue vraiment les deux modes ; NotchPay reste toujours en page hébergée) :

    - `phone_number` + `channel` fournis ensemble : paiement direct (push USSD chez KPay,
      pré-remplissage chez NotchPay qui n'a pas de mode USSD).
    - Aucun des deux fourni : mode page hébergée explicite (KPay : mode GATEWAY — Mobile
      Money **et** cartes bancaires/PayPal sur la même page, l'acheteur choisit sur place).
      `return_url` devient alors obligatoire : c'est le frontend (deep link app ou page web,
      propre à son propre schéma) qui la fournit, pas le backend.
    """

    phone_number = serializers.CharField(
        max_length=20, required=False, allow_blank=False
    )
    channel = serializers.ChoiceField(
        choices=PaymentTransaction.CHANNELS, required=False
    )
    return_url = serializers.URLField(required=False)
    cancel_url = serializers.URLField(required=False)

    def validate(self, attrs):
        has_channel = bool(attrs.get("channel"))
        has_phone = bool(attrs.get("phone_number"))
        if has_channel != has_phone:
            raise serializers.ValidationError(
                "phone_number et channel doivent être fournis ensemble (paiement direct), "
                "ou omis tous les deux (page hébergée)."
            )
        if not has_channel and not attrs.get("return_url"):
            raise serializers.ValidationError(
                "return_url est obligatoire pour un paiement par page hébergée (sans "
                "phone_number/channel)."
            )
        return attrs
