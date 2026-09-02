import hashlib
import hmac
import logging

from django.conf import settings
from django.db import models
from django.db import transaction
from django.utils import timezone
from rest_framework import mixins
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
from escrow.services.order_lifecycle import OrderLifecycleError
from escrow.services.order_lifecycle import OrderLifecycleService
from escrow.services.providers import get_payment_client
from escrow.services.providers import KPayClient
from escrow.services.providers import NotchPayClient
from escrow.services.providers import PaymentProviderError
from escrow.services.providers import STATUS_FAILED
from escrow.services.providers import STATUS_SUCCESSFUL

logger = logging.getLogger(__name__)


class OrderViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer

    def get_queryset(self):
        user = self.request.user
        return Order.objects.filter(
            models.Q(buyer=user) | models.Q(listing__seller=user)
        )

    @action(detail=True, methods=["post"])
    def initiate_payment(self, request, pk=None):
        order = self.get_object()
        if order.buyer != request.user:
            return Response({"error": "Action interdite"}, status=403)
        if order.status != "pending":
            return Response(
                {"error": "Cette commande n'est pas en attente de paiement"}, status=400
            )

        serializer = InitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # `phone_number`/`channel` absents ensemble (validés par le serializer) → mode page
        # hébergée explicite (KPay : GATEWAY, Mobile Money + cartes/PayPal) ; `return_url`
        # est alors garantie présente par le serializer.
        phone_number = serializer.validated_data.get("phone_number", "")
        channel = serializer.validated_data.get("channel", "")
        return_url = serializer.validated_data.get("return_url")
        cancel_url = serializer.validated_data.get("cancel_url")

        reference = (
            f"SENTAA-{order.id.hex[:12].upper()}-{int(timezone.now().timestamp())}"
        )
        client = get_payment_client()
        try:
            result = client.initialize_payment(
                amount=order.total_amount,
                phone=phone_number or None,
                currency="XAF",
                reference=reference,
                channel=channel or None,
                return_url=return_url,
                cancel_url=cancel_url,
                description=f"Paiement escrow commande {order.id}",
            )
        except PaymentProviderError:
            logger.exception(
                "Échec d'initialisation du paiement %s pour order %s",
                client.name,
                order.id,
            )
            return Response(
                {"error": "Impossible d'initier le paiement, réessayez"}, status=502
            )

        if not result.provider_reference:
            logger.warning(
                "Réponse %s sans provider_reference pour order %s ; le webhook ne pourra "
                "pas vérifier l'origine du paiement pour cette transaction",
                client.name,
                order.id,
            )
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref=reference,
            provider=client.name,
            provider_reference=result.provider_reference or "",
            amount=order.total_amount,
            channel=channel,
            phone_number=phone_number,
            status="pending",
            raw_response=result.raw,
        )

        # `payment_flow` est le signal explicite que le frontend doit tester ("redirect" =
        # ouvrir/rediriger vers `checkout_url` ; "ussd" = rien à afficher, l'acheteur reçoit
        # un push direct sur son téléphone) — jamais deviner à partir de la seule présence de
        # `checkout_url` : un futur fournisseur/mode pourrait ajouter un 3e cas (QR code...).
        response_payload = {
            "reference": reference,
            "status": result.status,
            "payment_flow": result.payment_flow,
        }
        if result.checkout_url:
            response_payload["checkout_url"] = result.checkout_url
        return Response(response_payload, status=202)

    @action(detail=True, methods=["post"])
    def confirm_reception(self, request, pk=None):
        order = self.get_object()
        if order.buyer != request.user:
            return Response({"error": "Action interdite"}, status=403)
        try:
            OrderLifecycleService.complete_and_release(order.id, actor=request.user)
        except OrderLifecycleError:
            return Response(
                {"error": "La commande n'a pas encore été livrée"}, status=400
            )
        return Response({"status": "Fonds libérés au vendeur"})


class _CollectWebhookView(APIView):

    permission_classes = [AllowAny]

    def _process(self, *, reference, provider_reference, client):
        if not reference:
            return Response(status=400)

        try:
            with transaction.atomic():
                txn = (
                    PaymentTransaction.objects.select_for_update()
                    .filter(external_ref=reference)
                    .first()
                )
                if txn is None or txn.status != "pending":
                    # Référence inconnue, ou déjà traitée par un appel webhook précédent :
                    # idempotent dans les deux cas.
                    return Response(status=200)

                if (
                    txn.provider_reference
                    and txn.provider_reference != provider_reference
                ):
                    # `provider_reference` reçu dans le webhook comparé à celui stocké à la
                    # CRÉATION du paiement (initiate_payment), pas à un champ dérivé du
                    # payload webhook lui-même. Sans ce garde-fou, un attaquant disposant d'un
                    # paiement réellement complété chez lui (même minime, même sur une autre
                    # commande) pourrait rejouer sa `provider_reference` (réelle, donc
                    # `verify_payment` réussirait) contre le `reference`/`merchant_reference`
                    # d'une AUTRE transaction pour la créditer sans paiement correspondant —
                    # il ne peut pas deviner à l'avance la `provider_reference` que le
                    # fournisseur a attribuée à CETTE transaction précise. Vide (transactions
                    # créées avant ce correctif) ne bloque pas : la re-vérification live via
                    # `verify_payment` reste la seule mitigation pour ces lignes-là.
                    logger.warning(
                        "Webhook %s : provider_reference reçu (%s) ne correspond pas à "
                        "celui enregistré à la création pour la transaction %s ; rejeté",
                        client.name,
                        provider_reference,
                        txn.id,
                    )
                    return Response(status=400)

                if txn.provider and txn.provider != client.name:
                    logger.warning(
                        "Webhook %s reçu pour la transaction %s, traitée par %s ; ignoré",
                        client.name,
                        txn.id,
                        txn.provider,
                    )
                    return Response(status=200)

                try:
                    verified = client.verify_payment(provider_reference)
                except PaymentProviderError:
                    logger.exception(
                        "Échec de vérification %s pour %s", client.name, reference
                    )
                    return Response(status=502)

                txn.raw_response = verified.raw

                if verified.status == STATUS_SUCCESSFUL:
                    txn.status = "successful"
                    txn.is_success = True
                    txn.save(update_fields=["status", "is_success", "raw_response"])

                    if txn.transaction_type == "collect":
                        order = txn.order
                        order.status = "paid_escrow"
                        order.paid_at = timezone.now()
                        order.save(update_fields=["status", "paid_at"])
                        on_order_paid(order)
                elif verified.status == STATUS_FAILED:
                    txn.status = "failed"
                    txn.save(update_fields=["status", "raw_response"])
                else:
                    txn.save(update_fields=["raw_response"])
        except Exception:
            logger.exception(
                "Erreur inattendue lors du traitement du webhook %s", client.name
            )
            return Response(status=500)

        return Response(status=200)


class MobileMoneyWebhookView(_CollectWebhookView):
    """Webhook NotchPay (fournisseur historique) — endpoint conservé tel quel :
    `/mobile-money-webhook/`."""

    @staticmethod
    def _signature_ok(request):
        secret = getattr(settings, "NOTCHPAY_WEBHOOK_HASH", "")
        if not secret:
            return True
        received = request.headers.get("x-notch-signature", "")
        if not received:
            return False
        expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(received, expected)

    def post(self, request):
        if not self._signature_ok(request):
            logger.warning("Webhook NotchPay rejeté : signature absente ou invalide")
            return Response(status=401)

        data = request.data.get("data", {})
        return self._process(
            reference=data.get("merchant_reference"),
            provider_reference=data.get("reference"),
            client=NotchPayClient(),
        )


class KPayWebhookView(_CollectWebhookView):

    @staticmethod
    def _signature_ok(request):
        secret = getattr(settings, "KPAY_WEBHOOK_SECRET", "")
        if not secret:
            return True
        received = request.headers.get("x-kpay-signature", "")
        if not received:
            return False
        expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(received, expected)

    def post(self, request):
        if not self._signature_ok(request):
            logger.warning("Webhook KPay rejeté : signature absente ou invalide")
            return Response(status=401)

        event = request.data.get("event", "")
        if not event.startswith("payment."):
            return Response(status=200)

        return self._process(
            reference=request.data.get("externalId"),
            provider_reference=request.data.get("paymentId"),
            client=KPayClient(),
        )
