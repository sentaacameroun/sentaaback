import hashlib
import hmac
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError
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
from escrow.services.collect import apply_verified_collect_result
from escrow.services.order_lifecycle import OrderLifecycleError
from escrow.services.order_lifecycle import OrderLifecycleService
from escrow.services.providers import get_payment_client
from escrow.services.providers import KPayClient
from escrow.services.providers import NotchPayClient
from escrow.services.providers import PaymentProviderError

logger = logging.getLogger(__name__)


def _payment_response_payload(txn):
    """
    Forme de réponse d'une collecte `pending` — partagée entre `initiate_payment` (nouvelle
    session ou reprise d'une session existante) et `pending_payment` (lecture seule). Ne
    renvoie jamais `raw_response` (spécifique au fournisseur, voir PR 7) : uniquement les
    champs normalisés dont le frontend a besoin pour compléter le paiement.
    """
    payload = {
        "reference": txn.external_ref,
        "status": txn.status,
        "payment_flow": txn.payment_flow,
    }
    if txn.checkout_url:
        payload["checkout_url"] = txn.checkout_url
    return payload


def _reserve_collect_slot(order, reference, channel, phone_number):
    """
    Réserve, de façon atomique, le slot "collecte `pending`" de la commande (contrainte
    unique `unique_pending_collect_per_order`, escrow/models.py) AVANT tout appel fournisseur.

    Sans ce point d'entrée, deux requêtes concurrentes passant toutes les deux le check de
    lecture d'`initiate_payment` déclencheraient chacune un VRAI appel fournisseur (ex. deux
    push USSD envoyés au même acheteur pour la même commande) avant que la contrainte ne
    tranche seulement sur l'écriture — la session perdante, jamais persistée, resterait un
    paiement fantôme si l'acheteur la complète quand même (le webhook correspondant ne
    trouverait alors aucune transaction à créditer). Trouvé par `escrow-reviewer` sur la
    première version de cette PR.

    Renvoie la ligne réservée (`status="pending"`, sans `provider`/`provider_reference` —
    l'appelant les complète une fois l'appel fournisseur résolu, succès ou échec), ou `None`
    si le slot est resté occupé (course concurrente résiduelle, très rare).
    """
    try:
        with transaction.atomic():
            return PaymentTransaction.objects.create(
                order=order,
                transaction_type="collect",
                external_ref=reference,
                amount=order.total_amount,
                channel=channel,
                phone_number=phone_number,
                status="pending",
            )
    except IntegrityError:
        logger.warning(
            "Collecte concurrente détectée pour order %s ; slot déjà réservé par une autre "
            "requête, aucun appel fournisseur déclenché pour celle-ci",
            order.id,
        )
        return None


def _resolve_stale_pending(pending):
    """
    `pending` a dépassé `COLLECT_RECONCILIATION_MINUTES` sans résolution locale (pas de
    webhook reçu). Revérifie TOUJOURS auprès du fournisseur avant de la clôturer plutôt que de
    présumer un échec : les webhooks ne sont pas fiables à 100 % (fiabilité NotchPay en prod
    documentée en PR 7, REFACTOR_PLAN.md) — déclarer `failed` un paiement en réalité réussi
    ferait ignorer silencieusement le webhook tardif correspondant (garde
    `txn.status != "pending"` de `_CollectWebhookView._process`), et l'argent capté par le
    fournisseur ne serait jamais crédité à l'escrow.

    Renvoie `pending` à jour. Statut résultant à interpréter par l'appelant :
    - `successful` : la commande vient d'être créditée (l'appelant doit le refléter) ;
    - `pending` : toujours en cours côté fournisseur malgré le délai local, réutilisable ;
    - `failed` : confirmé (ou non vérifiable), le slot est libre pour une nouvelle tentative.
    """
    if not pending.provider_reference:
        pending.status = "failed"
        pending.save(update_fields=["status"])
        return pending

    client = get_payment_client(pending.provider or None)
    try:
        verified = client.verify_payment(pending.provider_reference)
    except PaymentProviderError:
        logger.warning(
            "Impossible de revérifier la collecte %s (order %s) avant de la clôturer ; "
            "clôturée en failed sans confirmation fournisseur",
            pending.id,
            pending.order_id,
        )
        pending.status = "failed"
        pending.save(update_fields=["status"])
        return pending

    with transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().get(pk=pending.pk)
        if locked.status == "pending":
            apply_verified_collect_result(locked, verified)
    pending.refresh_from_db()
    return pending


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

        # Reprise d'un paiement déjà commencé : au plus une collecte `pending` à la fois par
        # commande (contrainte unique `unique_pending_collect_per_order`, escrow/models.py).
        # Lecture peu coûteuse — filtrée sur `order` (FK indexée), donc bornée au nombre de
        # transactions de CETTE commande, indépendamment du volume total de la table — voir
        # échange avec le développeur avant cette PR.
        pending = (
            order.transactions.filter(transaction_type="collect", status="pending")
            .order_by("-created_at")
            .first()
        )
        if pending is not None:
            still_fresh = pending.created_at >= timezone.now() - timedelta(
                minutes=settings.COLLECT_RECONCILIATION_MINUTES
            )
            if still_fresh:
                # Session récente encore valide côté fournisseur : on la renvoie telle quelle
                # plutôt que d'en ouvrir une seconde — c'est la reprise à proprement parler.
                return Response(_payment_response_payload(pending), status=202)
            # Trop ancienne localement : peut n'être qu'un webhook en retard/perdu plutôt
            # qu'un paiement réellement abandonné — `_resolve_stale_pending` revérifie
            # toujours auprès du fournisseur avant de trancher (voir sa docstring).
            resolved = _resolve_stale_pending(pending)
            if resolved.status == "successful":
                return Response(
                    {"error": "Cette commande n'est pas en attente de paiement"},
                    status=400,
                )
            if resolved.status == "pending":
                return Response(_payment_response_payload(resolved), status=202)
            # "failed" : confirmé (ou non vérifiable) — le slot est libre, on continue.

        # Suffixe aléatoire (pas un timestamp à la seconde près) : deux appels à
        # initiate_payment pour la même commande dans la même seconde — précisément le
        # scénario que cette PR rend possible (reprise, retry frontend) — généraient
        # auparavant la même `reference`, en collision sur la contrainte unique
        # `PaymentTransaction.external_ref` (bug latent, révélé en écrivant
        # `test_initiate_payment_replaces_expired_pending_transaction`).
        reference = (
            f"SENTAA-{order.id.hex[:12].upper()}-{uuid.uuid4().hex[:10].upper()}"
        )

        # Réservation du slot AVANT tout appel fournisseur (voir docstring de
        # `_reserve_collect_slot`) : la contrainte unique tranche ici, jamais après un appel
        # réseau déjà effectué.
        txn = _reserve_collect_slot(order, reference, channel, phone_number)
        if txn is None:
            return Response(
                {
                    "error": "Un paiement est déjà en cours pour cette commande, réessayez"
                },
                status=409,
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
            # Le slot réservé ne doit pas rester `pending` indéfiniment pour un appel qui n'a
            # jamais abouti côté fournisseur : le libérer immédiatement plutôt que d'attendre
            # la réconciliation (`reconcile_pending_collects` ne revérifie que les lignes
            # ayant une `provider_reference`, absente ici).
            txn.status = "failed"
            txn.raw_response = {"error": "initialize_payment_failed"}
            txn.save(update_fields=["status", "raw_response"])
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
        txn.provider = client.name
        txn.provider_reference = result.provider_reference or ""
        txn.payment_flow = result.payment_flow
        txn.checkout_url = result.checkout_url or ""
        txn.raw_response = result.raw
        txn.save(
            update_fields=[
                "provider",
                "provider_reference",
                "payment_flow",
                "checkout_url",
                "raw_response",
            ]
        )

        # `payment_flow` est le signal explicite que le frontend doit tester ("redirect" =
        # ouvrir/rediriger vers `checkout_url` ; "ussd" = rien à afficher, l'acheteur reçoit
        # un push direct sur son téléphone) — jamais deviner à partir de la seule présence de
        # `checkout_url` : un futur fournisseur/mode pourrait ajouter un 3e cas (QR code...).
        return Response(_payment_response_payload(txn), status=202)

    @action(detail=True, methods=["get"])
    def pending_payment(self, request, pk=None):
        """
        Lecture seule de la collecte `pending` en cours pour cette commande, si elle existe —
        permet au frontend de retrouver une session de paiement déjà commencée (l'utilisateur
        a fermé l'app, perdu la connexion, etc.) sans déclencher de nouvel appel fournisseur.
        Même forme de réponse que `initiate_payment`. 404 s'il n'y a rien en cours.
        """
        order = self.get_object()
        if order.buyer != request.user:
            return Response({"error": "Action interdite"}, status=403)
        txn = (
            order.transactions.filter(transaction_type="collect", status="pending")
            .order_by("-created_at")
            .first()
        )
        if txn is None:
            return Response(status=404)
        return Response(_payment_response_payload(txn))

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

                apply_verified_collect_result(txn, verified)
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
