import logging

logger = logging.getLogger(__name__)


def on_order_paid(order):
    """
    Called once escrow confirms a collect payment (Order.status -> paid_escrow).
    Delivery auto-creation is wired in by the logistics app.
    """
    try:
        from logistics.services import create_delivery_for_order
    except ImportError:
        logger.info("Order %s paid; logistics module not wired yet.", order.id)
        return
    create_delivery_for_order(order)
