from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from escrow.models import Order, PaymentTransaction
from escrow.serializers import OrderSerializer
from django.db import models

class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer

    def get_queryset(self):
        user = self.request.user
        return Order.objects.filter(models.Q(buyer=user) | models.Q(listing__seller=user))

    @action(detail=True, methods=['post'])
    def confirm_reception(self, request, pk=None):
        """L'acheteur confirme avoir reçu l'objet : Libération des fonds"""
        order = self.get_object()
        if order.buyer != request.user:
            return Response({"error": "Action interdite"}, status=403)
        
        if order.status == 'shipped':
            order.status = 'completed'
            order.save()
            # Ici, on déclencherait le virement réel vers le vendeur
            return Response({"status": "Fonds libérés au vendeur"})
        return Response({"error": "La commande n'est pas en cours d'expédition"}, status=400)

class MobileMoneyWebhookView(viewsets.ViewSet):
    """Endpoint pour recevoir les confirmations de MTN/Orange"""
    permission_classes = [] # Doit être sécurisé par IP ou Signature

    def post(self, request):
        ref = request.data.get('external_reference')
        # Logique de mise à jour de la transaction et de la commande
        return Response(status=200)