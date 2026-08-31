import logging

from django.db import transaction

from notifications.tasks import send_push_notification_task

logger = logging.getLogger(__name__)


def on_order_paid(order):
    """
    Called once escrow confirms a collect payment (Order.status -> paid_escrow).
    Delivery auto-creation is wired in by the logistics app.
    """
    transaction.on_commit(
        lambda: send_push_notification_task.delay(
            user_id=order.listing.seller_id,
            title="Nouvelle commande payée",
            body="Nouvelle commande payée — prépare l'envoi",
            data={"type": "order", "id": str(order.id)},
        )
    )
    try:
        from logistics.services import create_delivery_for_order
    except ImportError:
        logger.info("Order %s paid; logistics module not wired yet.", order.id)
        return
    create_delivery_for_order(order)
