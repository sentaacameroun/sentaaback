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
from escrow.services.notchpay_client import NotchPayClient
from escrow.services.notchpay_client import NotchPayError
from escrow.services.order_lifecycle import OrderLifecycleError
from escrow.services.order_lifecycle import OrderLifecycleService

logger = logging.getLogger(__name__)


class OrderViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    # Mixins explicites plutôt que `ModelViewSet` : le verbe DELETE ne doit jamais être
    # exposé sur une commande. Le journal financier (PaymentTransaction) a une FK vers
    # Order et ne doit jamais pouvoir être supprimé, même par cascade (voir .claude/rules/
    # escrow-core.md). Aucun `DestroyModelMixin` ici → DELETE renvoie 405.
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
        # Permission objet : seul l'acheteur de CETTE commande peut confirmer la réception
        # (IsAuthenticated seul ne suffit pas — voir .claude/rules, règle #4).
        order = self.get_object()
        if order.buyer != request.user:
            return Response({"error": "Action interdite"}, status=403)

        # Clôture déléguée au point d'entrée unique du cycle de vie : delivered → completed
        # + libération idempotente des fonds au vendeur. Aucune écriture de statut terminal
        # ni de payout ici (plus de logique de clôture dupliquée dans la vue).
        try:
            OrderLifecycleService.complete_and_release(order.id, actor=request.user)
        except OrderLifecycleError:
            return Response(
                {"error": "La commande n'a pas encore été livrée"}, status=400
            )
        return Response({"status": "Fonds libérés au vendeur"})


class MobileMoneyWebhookView(APIView):
    permission_classes = [AllowAny]

    @staticmethod
    def _signature_ok(request):
        """
        Vérifie la signature NotchPay (MAJEUR #8 de l'audit). NotchPay signe le corps brut de
        la requête en HMAC-SHA256 avec le « webhook hash » configuré côté fournisseur, et la
        transmet dans l'en-tête `x-notch-signature`.

        - Si `NOTCHPAY_WEBHOOK_HASH` est configuré : la signature est exigée et vérifiée avant
          tout traitement ; une requête non signée ou mal signée est rejetée.
        - Si aucun secret n'est configuré (dev/tests, ou instance sans hash webhook) : on
          retombe sur la mitigation déjà en place — la re-vérification du statut via l'API
          NotchPay (`verify_payment`), qui rend un succès impossible à forger. Voir
          REFACTOR_PLAN.md (PR 5) pour ce choix.
        """
        secret = getattr(settings, "NOTCHPAY_WEBHOOK_HASH", "")
        if not secret:
            return True
        received = request.headers.get("x-notch-signature", "")
        if not received:
            return False
        expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(received, expected)

    def post(self, request):
        # Signature vérifiée AVANT toute lecture de `request.data` / appel réseau : une requête
        # non signée ne doit déclencher aucun traitement (ni appel sortant NotchPay).
        if not self._signature_ok(request):
            logger.warning("Webhook NotchPay rejeté : signature absente ou invalide")
            return Response(status=401)

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
