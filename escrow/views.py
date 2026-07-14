import logging

from django.db import models
from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from escrow.models import Order
from escrow.models import PaymentTransaction
from escrow.serializers import InitiatePaymentSerializer
from escrow.serializers import OrderSerializer
from escrow.services.delivery_hooks import on_order_paid
from escrow.services.notchpay_client import NotchPayClient
from escrow.services.notchpay_client import NotchPayError

logger = logging.getLogger(__name__)


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer

    def get_queryset(self):
        user = self.request.user
        return Order.objects.filter(
            models.Q(buyer=user) | models.Q(listing__seller=user)
        )

    @action(detail=True, methods=["post"])
    def initiate_payment(self, request, pk=None):
        """L'acheteur déclenche la collecte Mobile Money vers l'escrow."""
        order = self.get_object()
        if order.buyer != request.user:
            return Response({"error": "Action interdite"}, status=403)
        if order.status != "pending":
            return Response(
                {"error": "Cette commande n'est pas en attente de paiement"}, status=400
            )

        serializer = InitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone_number = serializer.validated_data["phone_number"]
        channel = serializer.validated_data["channel"]

        reference = (
            f"SENTAA-{order.id.hex[:12].upper()}-{int(timezone.now().timestamp())}"
        )
        client = NotchPayClient()
        try:
            result = client.initialize_payment(
                amount=order.total_amount,
                phone=phone_number,
                currency="XAF",
                reference=reference,
                description=f"Paiement escrow commande {order.id}",
            )
        except NotchPayError:
            logger.exception(
                "Échec d'initialisation du paiement NotchPay pour order %s", order.id
            )
            return Response(
                {"error": "Impossible d'initier le paiement, réessayez"}, status=502
            )

        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref=reference,
            amount=order.total_amount,
            channel=channel,
            phone_number=phone_number,
            status="pending",
            raw_response=result,
        )
        return Response(
            {"reference": reference, "provider_response": result}, status=202
        )

    @action(detail=True, methods=["post"])
    def confirm_reception(self, request, pk=None):
        """L'acheteur confirme avoir reçu l'objet : libération des fonds vers le vendeur."""
        order = self.get_object()
        if order.buyer != request.user:
            return Response({"error": "Action interdite"}, status=403)

        if order.status != "shipped":
            return Response(
                {"error": "La commande n'est pas en cours d'expédition"}, status=400
            )

        order.status = "completed"
        order.save(update_fields=["status"])

        payout_amount = order.item_price - order.service_fee
        seller = order.listing.seller
        last_collect = (
            order.transactions.filter(transaction_type="collect", status="successful")
            .order_by("-created_at")
            .first()
        )
        channel = (
            last_collect.channel if last_collect else PaymentTransaction.CHANNELS[0][0]
        )
        phone_number = (
            last_collect.phone_number if last_collect else str(seller.phone_number)
        )

        client = NotchPayClient()
        try:
            result = client.initialize_transfer(
                amount=payout_amount,
                account_number=str(seller.phone_number),
                channel=channel,
                currency="XAF",
                description=f"Reversement vendeur commande {order.id}",
            )
            transfer_ref = None
            if isinstance(result, dict):
                transfer_ref = (result.get("transaction") or {}).get("reference")
            PaymentTransaction.objects.create(
                order=order,
                transaction_type="withdraw",
                external_ref=transfer_ref,
                amount=payout_amount,
                channel=channel,
                phone_number=phone_number,
                status="pending",
                raw_response=result,
            )
        except NotchPayError:
            # Le buyer a bien confirmé réception, on ne fait pas échouer sa requête :
            # le payout raté est tracé pour réconciliation manuelle par un admin.
            logger.exception(
                "Échec du reversement vendeur pour order %s ; réconciliation manuelle nécessaire",
                order.id,
            )
            PaymentTransaction.objects.create(
                order=order,
                transaction_type="withdraw",
                amount=payout_amount,
                channel=channel,
                phone_number=phone_number,
                status="failed",
                raw_response={"error": "payout_call_failed"},
            )

        order.payout_at = timezone.now()
        order.save(update_fields=["payout_at"])
        return Response({"status": "Fonds libérés au vendeur"})


class MobileMoneyWebhookView(APIView):
    """Endpoint public pour recevoir les confirmations NotchPay (collecte/reversement)."""

    permission_classes = [AllowAny]

    def post(self, request):
        reference = request.data.get("reference") or request.data.get(
            "external_reference"
        )
        if not reference:
            return Response(status=400)

        try:
            with transaction.atomic():
                txn = (
                    PaymentTransaction.objects.select_for_update()
                    .filter(external_ref=reference)
                    .first()
                )
                if txn is None:
                    # Référence inconnue de notre côté : on acquitte quand même
                    # pour éviter que NotchPay ne retente indéfiniment.
                    return Response(status=200)

                if txn.status != "pending":
                    # Déjà traité par un appel webhook précédent : idempotent.
                    return Response(status=200)

                client = NotchPayClient()
                try:
                    verified = client.verify_payment(reference)
                except NotchPayError:
                    logger.exception(
                        "Échec de vérification NotchPay pour %s", reference
                    )
                    return Response(status=502)

                provider_status = (
                    (verified.get("transaction") or {}).get("status", "").lower()
                )
                txn.raw_response = verified

                if provider_status in ("complete", "successful"):
                    txn.status = "successful"
                    txn.is_success = True
                    txn.save(update_fields=["status", "is_success", "raw_response"])

                    if txn.transaction_type == "collect":
                        order = txn.order
                        order.status = "paid_escrow"
                        order.paid_at = timezone.now()
                        order.save(update_fields=["status", "paid_at"])
                        on_order_paid(order)
                elif provider_status in ("failed", "canceled", "cancelled"):
                    txn.status = "failed"
                    txn.save(update_fields=["status", "raw_response"])
                else:
                    txn.save(update_fields=["raw_response"])
        except Exception:
            logger.exception("Erreur inattendue lors du traitement du webhook NotchPay")
            return Response(status=500)

        return Response(status=200)
