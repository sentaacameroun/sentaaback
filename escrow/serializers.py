from rest_framework import serializers
from .models import Order, PaymentTransaction
from decimal import Decimal

class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = '__all__'
        read_only_fields = ['buyer', 'status', 'service_fee', 'total_amount']

    def validate(self, data):
        listing = data['listing']
        if listing.seller == self.context['request'].user:
            raise serializers.ValidationError("Vous ne pouvez pas acheter votre propre article.")
        if listing.status != 'active':
            raise serializers.ValidationError("Cet article n'est plus disponible.")
        return data

    def create(self, validated_data):
        listing = validated_data['listing']
        price = listing.price
        service_fee = price * Decimal('0.03')
        total = price + service_fee + validated_data.get('shipping_fee', 0)
        
        return Order.objects.create(
            **validated_data,
            buyer=self.context['request'].user,
            item_price=price,
            service_fee=service_fee,
            total_amount=total
        )