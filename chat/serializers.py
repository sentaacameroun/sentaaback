from rest_framework import serializers

from chat.models import Conversation
from chat.models import Message
from escrow.models import Order
from marketplace.models import Listing


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "conversation", "sender", "body", "is_read", "created_at"]
        read_only_fields = ["id", "conversation", "sender", "is_read", "created_at"]


class ConversationSerializer(serializers.ModelSerializer):
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "participants",
            "listing",
            "order",
            "created_at",
            "last_message",
            "unread_count",
        ]
        read_only_fields = fields

    def get_last_message(self, obj):
        message = obj.messages.order_by("-created_at").first()
        return MessageSerializer(message).data if message else None

    def get_unread_count(self, obj):
        user = self.context["request"].user
        return obj.messages.filter(is_read=False).exclude(sender=user).count()


class ConversationCreateSerializer(serializers.Serializer):
    listing = serializers.PrimaryKeyRelatedField(
        queryset=Listing.objects.all(), required=False
    )
    order = serializers.PrimaryKeyRelatedField(
        queryset=Order.objects.all(), required=False
    )

    def validate(self, data):
        if not data.get("listing") and not data.get("order"):
            raise serializers.ValidationError("Fournir 'listing' ou 'order'.")
        return data
