"""
Réconciliation des reversements Mobile Money (BLOQUANT #12 de l'audit).

Un transfert Mobile Money (`withdraw` vendeur, `courier_payout` livreur) est **asynchrone**,
quel que soit le fournisseur (NotchPay, KPay — voir escrow/services/providers/) :
`initialize_transfer` renvoie une ligne `pending` et le statut final n'arrive pas forcément par
le webhook. Sans réconciliation, un reversement reste `pending` indéfiniment (audit §6.5).
Cette tâche Celery planifiée :

  1. resout les reversements `pending` (avec référence fournisseur) en interrogeant TOUJOURS le
     fournisseur qui a réellement traité la ligne (`PaymentTransaction.provider`), pas
     forcément celui actuellement actif (`PAYMENT_PROVIDER` a pu changer entre-temps) ;
  2. re-tente les reversements `failed` jamais initiés (fail-soft), de façon **idempotente**
     et **bornée** (pas de retry infini silencieux) grâce aux clés d'idempotence de PR 3/PR 5.

Elle n'appelle jamais `initialize_transfer` sur un reversement déjà couvert par une clé
d'idempotence (`release:{order}` / `courier_payout:{delivery}`) : aucun double effet de bord.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from escrow.models import Order
from escrow.models import PaymentTransaction
from escrow.services.payouts import pay_courier_for_delivery
from escrow.services.payouts import release_escrow_funds
from escrow.services.providers import get_payment_client
from escrow.services.providers import PaymentProviderError
from escrow.services.providers import STATUS_FAILED
from escrow.services.providers import STATUS_SUCCESSFUL

logger = logging.getLogger(__name__)

_PAYOUT_TYPES = ["withdraw", "courier_payout"]


@shared_task
def reconcile_pending_payouts():
    """
    Fait progresser les reversements bloqués. Renvoie (résolus_pending, re-tentés_failed).
    """
    minutes = settings.PAYOUT_RECONCILIATION_MINUTES
    max_attempts = settings.PAYOUT_RECONCILIATION_MAX_ATTEMPTS
    threshold = timezone.now() - timedelta(minutes=minutes)

    resolved = _reconcile_pending(threshold, max_attempts)
    reinitiated = _reinitiate_failed(threshold, max_attempts)
    return resolved, reinitiated


def _reconcile_pending(threshold, max_attempts):
    """
    Reversements `pending` avec référence fournisseur : interroge `/transfers/{ref}` et met le
    statut à jour. `reconciliation_attempts__lt=max_attempts` borne les re-vérifications — une
    ligne ayant épuisé ses tentatives sort définitivement de la file (pas de retry infini).
    """
    stuck = PaymentTransaction.objects.filter(
        transaction_type__in=_PAYOUT_TYPES,
        status="pending",
        external_ref__isnull=False,
        created_at__lt=threshold,
        reconciliation_attempts__lt=max_attempts,
    )
    resolved = 0
    for txn in stuck:
        # Chaque ligne garde le fournisseur qui l'a réellement traitée
        # (`PaymentTransaction.provider`) : on interroge TOUJOURS ce fournisseur-là, jamais
        # celui actuellement actif (`PAYMENT_PROVIDER` a pu changer depuis) — sinon on
        # réconcilierait une référence NotchPay contre l'API KPay (ou l'inverse). Une ligne
        # sans provider (créée avant l'ajout du champ) retombe sur le fournisseur actif.
        client = get_payment_client(txn.provider or None)
        try:
            result = client.verify_transfer(txn.external_ref)
        except PaymentProviderError:
            attempts = _bump_attempts(txn.pk)
            logger.warning(
                "Réconciliation : verify_transfer (%s) a échoué pour %s (ref %s, "
                "tentative %s/%s)",
                client.name,
                txn.id,
                txn.external_ref,
                attempts,
                max_attempts,
            )
            if attempts >= max_attempts:
                logger.error(
                    "Réconciliation : transfert %s injoignable après %s tentatives "
                    "(ref %s) — intervention manuelle requise",
                    txn.id,
                    attempts,
                    txn.external_ref,
                )
            continue

        with transaction.atomic():
            locked = PaymentTransaction.objects.select_for_update().get(pk=txn.pk)
            if locked.status != "pending":
                # Résolu entre-temps par un autre passage : idempotent, on n'y touche plus.
                continue
            locked.reconciliation_attempts += 1
            locked.raw_response = result.raw
            if result.status == STATUS_SUCCESSFUL:
                locked.status = "successful"
                locked.is_success = True
                locked.save(
                    update_fields=[
                        "status",
                        "is_success",
                        "raw_response",
                        "reconciliation_attempts",
                    ]
                )
                resolved += 1
            elif result.status == STATUS_FAILED:
                locked.status = "failed"
                locked.save(
                    update_fields=["status", "raw_response", "reconciliation_attempts"]
                )
                logger.error(
                    "Réconciliation : transfert %s marqué FAILED par le fournisseur "
                    "(type %s, ref %s) — reversement à re-tenter / intervention manuelle",
                    locked.id,
                    locked.transaction_type,
                    locked.external_ref,
                )
                resolved += 1
            else:
                # Toujours `pending` côté fournisseur : on re-tentera au prochain passage.
                locked.save(update_fields=["raw_response", "reconciliation_attempts"])
                if locked.reconciliation_attempts >= max_attempts:
                    logger.error(
                        "Réconciliation : transfert %s toujours 'pending' après %s "
                        "tentatives (ref %s) — intervention manuelle requise",
                        locked.id,
                        locked.reconciliation_attempts,
                        locked.external_ref,
                    )
    return resolved


def _reinitiate_failed(threshold, max_attempts):
    """
    Reversements `failed` jamais initiés (fail-soft : ni référence ni clé d'idempotence) :
    re-tente l'appel fournisseur de façon idempotente. `release_escrow_funds` /
    `pay_courier_for_delivery` no-op si une ligne portant la clé existe déjà → aucun double
    paiement. Borné : au-delà de `max_attempts` échecs cumulés pour la même cible, on cesse de
    re-tenter et on loggue pour intervention manuelle (pas de retry infini silencieux).
    """
    reinitiated = 0

    # --- Reversements vendeur ---
    order_ids = (
        PaymentTransaction.objects.filter(
            transaction_type="withdraw",
            status="failed",
            external_ref__isnull=True,
            created_at__lt=threshold,
        )
        .values_list("order_id", flat=True)
        .distinct()
    )
    for order_id in order_ids:
        release_key = f"release:{order_id}"
        if PaymentTransaction.objects.filter(idempotency_key=release_key).exists():
            # Déjà initié (pending/successful) : rien à re-tenter.
            continue
        failures = PaymentTransaction.objects.filter(
            order_id=order_id,
            transaction_type="withdraw",
            status="failed",
            external_ref__isnull=True,
        ).count()
        if failures >= max_attempts:
            logger.error(
                "Réconciliation : reversement vendeur pour order %s bloqué après %s "
                "échecs — intervention manuelle requise",
                order_id,
                failures,
            )
            continue
        order = Order.objects.get(pk=order_id)
        release_escrow_funds(order, idempotency_key=release_key)
        reinitiated += 1

    # --- Reversements livreur ---
    courier_order_ids = (
        PaymentTransaction.objects.filter(
            transaction_type="courier_payout",
            status="failed",
            external_ref__isnull=True,
            created_at__lt=threshold,
        )
        .values_list("order_id", flat=True)
        .distinct()
    )
    for order_id in courier_order_ids:
        try:
            delivery = Order.objects.get(pk=order_id).delivery
        except (Order.DoesNotExist, ObjectDoesNotExist):
            # Commande sans livraison associée : rien à re-tenter côté coursier.
            continue
        courier_key = f"courier_payout:{delivery.id}"
        if PaymentTransaction.objects.filter(idempotency_key=courier_key).exists():
            continue
        failures = PaymentTransaction.objects.filter(
            order_id=order_id,
            transaction_type="courier_payout",
            status="failed",
            external_ref__isnull=True,
        ).count()
        if failures >= max_attempts:
            logger.error(
                "Réconciliation : reversement livreur pour delivery %s bloqué après %s "
                "échecs — intervention manuelle requise",
                delivery.id,
                failures,
            )
            continue
        pay_courier_for_delivery(delivery)
        reinitiated += 1

    return reinitiated


def _bump_attempts(pk):
    """Incrémente atomiquement `reconciliation_attempts` et renvoie la nouvelle valeur."""
    PaymentTransaction.objects.filter(pk=pk).update(
        reconciliation_attempts=F("reconciliation_attempts") + 1
    )
    return PaymentTransaction.objects.values_list(
        "reconciliation_attempts", flat=True
    ).get(pk=pk)
