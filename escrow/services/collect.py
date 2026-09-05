"""
Application d'un résultat fournisseur vérifié (`ProviderResult`) à une transaction `collect`.

Point d'entrée UNIQUE de la transition `pending` -> `successful`/`failed` (+ `Order.status` ->
`paid_escrow` sur succès) — appelé à la fois par `escrow.views._CollectWebhookView._process`
(webhook fournisseur) et par `escrow.tasks.reconcile_pending_collects` (réconciliation Celery
d'une collecte `pending` restée bloquée, ex. utilisateur qui a perdu le fil du paiement et dont
le webhook n'est jamais arrivé). Factorisé ici pour ne JAMAIS dupliquer cette logique entre les
deux chemins (même principe que la règle #3 de .claude/rules/escrow-core.md, appliquée ici à la
transition `paid_escrow` plutôt qu'à la clôture de commande).
"""

from django.utils import timezone

from escrow.services.delivery_hooks import on_order_paid
from escrow.services.providers import STATUS_FAILED
from escrow.services.providers import STATUS_SUCCESSFUL


def apply_verified_collect_result(txn, verified):
    """
    Écrit `verified` sur `txn`. L'appelant est responsable d'avoir déjà verrouillé `txn`
    (`select_for_update`) dans un `transaction.atomic()` et vérifié `txn.status == "pending"`
    AVANT d'appeler cette fonction (idempotence : la garde d'entrée reste chez l'appelant, dont
    les payloads/critères diffèrent — payload webhook vs boucle de réconciliation).

    `txn.transaction_type == "collect"` gate la transition `Order.status -> paid_escrow` +
    `on_order_paid` : comportement historique de `_CollectWebhookView._process` (préservé tel
    quel), où `txn` est retrouvée par `external_ref` sans filtrer sur le type — un webhook
    fournisseur portant sur un `withdraw`/`courier_payout` peut donc, en théorie, atteindre
    cette fonction ; seule une collecte fait progresser la commande.
    """
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
