"""
Service de cycle de vie de la commande escrow — point d'entrée UNIQUE des transitions
terminales (BLOQUANT #1 de l'audit : double reversement possible).

Avant ce service, deux chemins indépendants clôturaient la commande et libéraient les fonds
(`escrow.confirm_reception` et `logistics.confirm_delivery`), chacun avec sa propre garde,
sans idempotence → le vendeur pouvait être payé deux fois. Toute la logique de clôture vit
désormais ici, derrière un flux en deux étapes séquentielles décidé avec le développeur
(question ouverte #2 de l'audit) :

    shipped ──mark_delivered (coursier a saisi le code)──▶ delivered
    delivered ──complete_and_release (acheteur confirme)──▶ completed + fonds au vendeur

Un SEUL chemin libère les fonds au vendeur (`complete_and_release`), à la confirmation de
l'acheteur. Chaque méthode est atomique (`transaction.atomic` + `select_for_update`) ; la
libération des fonds est idempotente via une contrainte unique en base
(`PaymentTransaction.idempotency_key = release:{order.id}`), pas une simple garde
applicative (voir .claude/rules/escrow-core.md, règle #2).
"""

import logging

from django.db import transaction
from django.utils import timezone

from escrow.models import Order
from escrow.models import PaymentTransaction
from escrow.services.payouts import release_escrow_funds
from notifications.tasks import send_push_notification_task

logger = logging.getLogger(__name__)


class OrderLifecycleError(Exception):
    """Transition de cycle de vie invalide (statut source inattendu)."""


class OrderLifecycleService:
    @staticmethod
    def _release_key(order_id):
        return f"release:{order_id}"

    @staticmethod
    def mark_delivered(order_id, actor):
        """
        `shipped` → `delivered`. Marque la commande comme physiquement livrée (le coursier
        a saisi le code de confirmation). Ne libère PAS les fonds au vendeur : seule la
        confirmation acheteur (`complete_and_release`) le fait.

        Idempotent : un second appel sur une commande déjà `delivered` est un no-op.
        Lève `OrderLifecycleError` si la commande n'est ni `shipped` ni `delivered`.
        """
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)

            if order.status == "delivered":
                return order
            if order.status != "shipped":
                raise OrderLifecycleError(
                    f"Passage à 'delivered' impossible depuis le statut '{order.status}'"
                )

            order.status = "delivered"
            order.save(update_fields=["status", "updated_at"])

            transaction.on_commit(
                lambda: send_push_notification_task.delay(
                    user_id=order.buyer_id,
                    title="Commande livrée",
                    body="Ta commande est arrivée — confirme la réception",
                    data={"type": "order", "id": str(order.id)},
                )
            )

        logger.info(
            "Order %s marquée 'delivered' par l'acteur %s",
            order.id,
            getattr(actor, "id", actor),
        )
        return order

    @staticmethod
    def complete_and_release(order_id, actor):
        """
        `delivered` → `completed` + libération des fonds au vendeur.

        Idempotent : la ligne de reversement porte `idempotency_key = release:{order_id}`
        (unique en base). Deux appels concurrents sont sérialisés par `select_for_update`
        (le second lit `completed` et fait un no-op) ; deux appels séquentiels sont bloqués
        par la même garde de statut, et la contrainte unique reste le garde-fou dur contre
        tout double virement. Lève `OrderLifecycleError` si la commande n'est ni `delivered`
        ni déjà `completed`.
        """
        release_key = OrderLifecycleService._release_key(order_id)

        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)

            already_released = PaymentTransaction.objects.filter(
                idempotency_key=release_key
            ).exists()
            if order.status == "completed" or already_released:
                # Déjà clôturée / fonds déjà libérés : no-op sûr.
                return order

            if order.status != "delivered":
                raise OrderLifecycleError(
                    f"Clôture impossible depuis le statut '{order.status}' "
                    "(la commande doit d'abord être 'delivered')"
                )

            order.status = "completed"
            order.payout_at = timezone.now()
            order.save(update_fields=["status", "payout_at", "updated_at"])

            transaction.on_commit(
                lambda: send_push_notification_task.delay(
                    user_id=order.listing.seller_id,
                    title="Paiement reçu",
                    body="Tu as été payé pour ton annonce",
                    data={"type": "order", "id": str(order.id)},
                )
            )

        # Appel réseau NotchPay hors du verrou DB (pas de lock tenu pendant l'I/O). La
        # sérialisation par select_for_update ci-dessus garantit qu'un seul appel atteint ce
        # point ; `release_escrow_funds` est en plus idempotent via `release_key`.
        release_escrow_funds(order, idempotency_key=release_key)

        logger.info(
            "Order %s clôturée et fonds libérés par l'acteur %s",
            order.id,
            getattr(actor, "id", actor),
        )
        return order
