import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Count
from django.db.models import Q
from django.utils import timezone

from escrow.models import Order
from jobs.models import JobApplication
from jobs.models import JobOffer
from marketplace.models import Listing
from notifications.emails import send_application_reminder
from notifications.emails import send_newsletter
from notifications.emails import send_payment_reminder
from notifications.emails import send_reception_reminder
from users.models import User

logger = logging.getLogger(__name__)


@shared_task
def check_pending_escrow_payments():
    threshold = timezone.now() - timedelta(hours=settings.ESCROW_PENDING_REMINDER_HOURS)
    orders = Order.objects.filter(
        status="pending",
        payment_reminder_sent_at__isnull=True,
        created_at__lt=threshold,
    ).select_related("buyer", "listing")
    count = 0
    for order in orders:
        try:
            send_payment_reminder(order)
        except Exception:
            logger.exception("Échec envoi rappel paiement pour order %s", order.id)
            continue
        order.payment_reminder_sent_at = timezone.now()
        order.save(update_fields=["payment_reminder_sent_at"])
        count += 1
    return count


@shared_task
def check_pending_reception_confirmations():
    threshold = timezone.now() - timedelta(days=settings.RECEPTION_REMINDER_DAYS)
    orders = Order.objects.filter(
        status="shipped",
        reception_reminder_sent_at__isnull=True,
        updated_at__lt=threshold,
    ).select_related("buyer", "listing")
    count = 0
    for order in orders:
        try:
            send_reception_reminder(order)
        except Exception:
            logger.exception("Échec envoi rappel réception pour order %s", order.id)
            continue
        order.reception_reminder_sent_at = timezone.now()
        order.save(update_fields=["reception_reminder_sent_at"])
        count += 1
    return count


@shared_task
def check_pending_job_applications():
    threshold = timezone.now() - timedelta(days=settings.APPLICATION_REMINDER_DAYS)
    job_offers = (
        JobOffer.objects.filter(
            applications__status="pending",
            applications__reminder_sent_at__isnull=True,
            applications__applied_at__lt=threshold,
        )
        .annotate(
            pending_count=Count(
                "applications",
                filter=Q(
                    applications__status="pending",
                    applications__reminder_sent_at__isnull=True,
                ),
            )
        )
        .distinct()
        .select_related("recruiter")
    )
    count = 0
    for job_offer in job_offers:
        try:
            send_application_reminder(job_offer, job_offer.pending_count)
        except Exception:
            logger.exception(
                "Échec envoi rappel candidatures pour job %s", job_offer.id
            )
            continue
        JobApplication.objects.filter(
            job=job_offer,
            status="pending",
            reminder_sent_at__isnull=True,
            applied_at__lt=threshold,
        ).update(reminder_sent_at=timezone.now())
        count += 1
    return count


@shared_task
def send_weekly_newsletter():
    since = timezone.now() - timedelta(days=7)
    listings = list(
        Listing.objects.filter(status="active", created_at__gte=since).order_by(
            "-created_at"
        )[:10]
    )
    job_offers = list(
        JobOffer.objects.filter(is_active=True, created_at__gte=since).order_by(
            "-created_at"
        )[:10]
    )

    if not listings and not job_offers:
        return 0

    # email est maintenant NULL (pas "") quand absent, pour autoriser plusieurs comptes sans
    # email sous la contrainte unique (users/models.py) — exclude(email='') ne les filtrerait
    # plus, d'où le exclude(email__isnull=True) explicite en plus.
    recipients = (
        User.objects.filter(is_active=True, newsletter_opt_in=True)
        .exclude(email="")
        .exclude(email__isnull=True)
    )
    count = 0
    for user in recipients:
        try:
            send_newsletter(user, listings, job_offers)
        except Exception:
            logger.exception("Échec envoi newsletter à %s", user.id)
            continue
        count += 1
    return count
