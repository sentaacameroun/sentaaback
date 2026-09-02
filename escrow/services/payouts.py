import logging

from django.db import IntegrityError
from django.db import transaction

from escrow.models import PaymentTransaction
from escrow.services.providers import get_payment_client
from escrow.services.providers import PaymentProviderError

logger = logging.getLogger(__name__)


def release_escrow_funds(order, idempotency_key=None):
    """
    Reverse item_price - service_fee au vendeur. No-op sûr si appelée deux fois pour la
    même commande : quand `idempotency_key` est fourni (ex. `release:{order.id}` par
    OrderLifecycleService), l'idempotence est garantie **au niveau base** par la
    contrainte unique sur PaymentTransaction.idempotency_key — pas seulement par une
    garde applicative (voir .claude/rules/escrow-core.md, règle #2).
    """
    # Garde applicative rapide : si une ligne de reversement porte déjà cette clé, ne pas
    # rappeler le fournisseur. La contrainte unique ci-dessous reste le garde-fou dur.
    if (
        idempotency_key
        and PaymentTransaction.objects.filter(idempotency_key=idempotency_key).exists()
    ):
        logger.info(
            "Reversement déjà enregistré pour order %s (clé %s) ; no-op idempotent",
            order.id,
            idempotency_key,
        )
        return

    payout_amount = order.item_price - order.service_fee
    seller = order.listing.seller
    last_collect = (
        order.transactions.filter(transaction_type="collect", status="successful")
        .order_by("-created_at")
        .first()
    )
    # `last_collect.channel` peut être vide : un paiement collecté en mode page hébergée
    # KPay par carte/PayPal (voir escrow/services/providers/kpay.py, InitiatePaymentSerializer)
    # n'a pas d'opérateur Mobile Money associé. On retombe alors sur le même défaut que
    # l'absence totale de collecte — un choix arbitraire (pas de moyen fiable de connaître
    # l'opérateur du vendeur) qui peut faire échouer ce reversement précis ; il rejoint alors
    # le circuit existant de reversements `failed` bornés + intervention manuelle
    # (escrow/tasks.py::reconcile_pending_payouts), pas une régression.
    channel = (
        last_collect.channel
        if last_collect and last_collect.channel
        else PaymentTransaction.CHANNELS[0][0]
    )
    phone_number = (
        last_collect.phone_number if last_collect else str(seller.phone_number)
    )

    client = get_payment_client()
    try:
        result = client.initialize_transfer(
            amount=payout_amount,
            account_number=str(seller.phone_number),
            channel=channel,
            currency="XAF",
            reference=idempotency_key,
            description=f"Reversement vendeur commande {order.id}",
        )
        try:
            # Savepoint (`atomic`) autour du create : si un appel concurrent a déjà
            # enregistré ce reversement, la violation de contrainte unique ne casse que le
            # savepoint, pas la transaction englobante — on peut alors l'ignorer proprement.
            with transaction.atomic():
                PaymentTransaction.objects.create(
                    order=order,
                    transaction_type="withdraw",
                    external_ref=result.provider_reference,
                    idempotency_key=idempotency_key,
                    provider=client.name,
                    amount=payout_amount,
                    channel=channel,
                    phone_number=phone_number,
                    status="pending",
                    raw_response=result.raw,
                )
        except IntegrityError:
            # La contrainte unique a fait son travail : on n'enregistre pas de doublon.
            logger.warning(
                "Reversement concurrent détecté pour order %s (clé %s) ; ligne ignorée",
                order.id,
                idempotency_key,
            )
    except PaymentProviderError:
        logger.exception(
            "Échec du reversement vendeur (%s) pour order %s ; réconciliation manuelle "
            "nécessaire",
            client.name,
            order.id,
        )
        # Ligne d'échec créée SANS idempotency_key : un reversement échoué ne doit pas
        # consommer la clé `release:{order.id}`. Sinon la garde d'idempotence (exists() +
        # already_released) considérerait la commande comme « déjà libérée » et bloquerait
        # toute réconciliation ultérieure — la commande resterait `completed` sans que le
        # vendeur soit jamais payé (argent bloqué). La clé n'est posée que sur un reversement
        # effectivement initié auprès du fournisseur (chemin succès → `pending`).
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="withdraw",
            provider=client.name,
            amount=payout_amount,
            channel=channel,
            phone_number=phone_number,
            status="failed",
            raw_response={"error": "payout_call_failed"},
        )


def pay_courier_for_delivery(delivery):
    """
    Reverse order.shipping_fee au coursier assigné. Déplacé depuis
    `logistics.views.DeliveryViewSet._payout_courier` vers `escrow.services` : `logistics`
    importe déjà `escrow.services.payouts` (donc `escrow.services.providers`) en top-level,
    alors qu'`escrow` ne référence `logistics` que via un import différé (voir
    `delivery_hooks.on_order_paid`) — ce module partagé doit donc vivre côté escrow pour
    respecter ce sens d'import et éviter tout risque de cycle.

    Idempotent (PR 5, point B tracké par escrow-reviewer en PR 3) : la ligne de payout porte
    `idempotency_key = courier_payout:{delivery.id}`, unique en base — un double appel ne peut
    pas payer le coursier deux fois. Le garde-fou dur est la contrainte unique (règle #2,
    .claude/rules/escrow-core.md), pas seulement la garde applicative. La clé est calculée en
    interne (pas passée par l'appelant `logistics.confirm_delivery`, hors scope). Même soin
    qu'en PR 3 sur l'échec : un reversement réellement échoué ne consomme PAS la clé, pour que
    la réconciliation puisse re-tenter (escrow/tasks.py::reconcile_pending_payouts).
    """
    courier = delivery.courier
    order = delivery.order
    payout_amount = order.shipping_fee
    if courier is None or not payout_amount:
        return

    idempotency_key = f"courier_payout:{delivery.id}"
    # Garde applicative rapide : si un payout porte déjà cette clé, ne pas rappeler le
    # fournisseur. La contrainte unique ci-dessous reste le garde-fou dur.
    if PaymentTransaction.objects.filter(idempotency_key=idempotency_key).exists():
        logger.info(
            "Payout coursier déjà enregistré pour delivery %s (clé %s) ; no-op idempotent",
            delivery.id,
            idempotency_key,
        )
        return

    last_collect = (
        order.transactions.filter(transaction_type="collect", status="successful")
        .order_by("-created_at")
        .first()
    )
    # Voir le commentaire équivalent dans release_escrow_funds ci-dessus : `channel` vide
    # (collecte par carte/PayPal via la page hébergée KPay) retombe sur le même défaut que
    # l'absence de collecte.
    channel = (
        last_collect.channel
        if last_collect and last_collect.channel
        else PaymentTransaction.CHANNELS[0][0]
    )

    client = get_payment_client()
    try:
        result = client.initialize_transfer(
            amount=payout_amount,
            account_number=str(courier.phone_number),
            channel=channel,
            currency="XAF",
            reference=idempotency_key,
            description=f"Reversement livreur commande {order.id}",
        )
        try:
            # Savepoint autour du create : un appel concurrent portant la même clé casse le
            # savepoint (violation d'unicité) sans casser la transaction englobante.
            with transaction.atomic():
                PaymentTransaction.objects.create(
                    order=order,
                    transaction_type="courier_payout",
                    external_ref=result.provider_reference,
                    idempotency_key=idempotency_key,
                    provider=client.name,
                    amount=payout_amount,
                    channel=channel,
                    phone_number=str(courier.phone_number),
                    status="pending",
                    raw_response=result.raw,
                )
        except IntegrityError:
            logger.warning(
                "Payout coursier concurrent détecté pour delivery %s (clé %s) ; ligne ignorée",
                delivery.id,
                idempotency_key,
            )
    except PaymentProviderError:
        logger.exception(
            "Échec du reversement livreur (%s) pour delivery %s ; réconciliation nécessaire",
            client.name,
            delivery.id,
        )
        # Ligne d'échec créée SANS idempotency_key : un payout échoué ne doit pas consommer
        # la clé `courier_payout:{delivery.id}`, sinon la garde `exists()` bloquerait toute
        # reprise et le coursier ne serait jamais payé (argent bloqué). La clé n'est posée que
        # sur un reversement effectivement initié auprès du fournisseur (chemin succès →
        # `pending`). La réconciliation re-tentera à partir de cette ligne `failed`.
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="courier_payout",
            provider=client.name,
            amount=payout_amount,
            channel=channel,
            phone_number=str(courier.phone_number),
            status="failed",
            raw_response={"error": "payout_call_failed"},
        )
