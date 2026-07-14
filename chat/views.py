from rest_framework import mixins
from rest_framework import permissions
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from chat.models import Conversation
from chat.permissions import IsConversationParticipant
from chat.serializers import ConversationCreateSerializer
from chat.serializers import ConversationSerializer
from chat.serializers import MessageSerializer


class ConversationViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ConversationSerializer

    def get_queryset(self):
        return Conversation.objects.filter(participants=self.request.user).distinct()

    def get_permissions(self):
        if self.action in ("retrieve", "messages"):
            return [permissions.IsAuthenticated(), IsConversationParticipant()]
        return [permissions.IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        serializer = ConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        buyer = request.user

        listing = serializer.validated_data.get("listing")
        order = serializer.validated_data.get("order")
        if order:
            listing = order.listing
            other = order.buyer if order.buyer != buyer else order.listing.seller
        else:
            other = listing.seller

        if other == buyer:
            return Response(
                {"error": "Impossible de créer une conversation avec vous-même."},
                status=400,
            )

        conversation, _ = Conversation.get_or_create_for_listing(buyer, other, listing)
        if order and conversation.order_id != order.id:
            conversation.order = order
            conversation.save(update_fields=["order"])

        return Response(
            ConversationSerializer(conversation, context={"request": request}).data,
            status=201,
        )

    @action(detail=True, methods=["get"])
    def messages(self, request, pk=None):
        conversation = self.get_object()
        queryset = conversation.messages.select_related("sender").order_by("created_at")
        page = self.paginate_queryset(queryset)
        serializer = MessageSerializer(
            page if page is not None else queryset, many=True
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)
