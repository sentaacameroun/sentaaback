import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def send_templated_email(subject, template_name, context, to):
    if not to:
        return
    html_body = render_to_string(f"emails/{template_name}", context)
    text_body = strip_tags(html_body)
    send_mail(
        subject=subject,
        message=text_body,
        html_message=html_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[to],
        fail_silently=False,
    )


def send_payment_reminder(order):
    if not order.buyer.email:
        return
    send_templated_email(
        subject="Senta'a — Finalisez le paiement de votre commande",
        template_name="payment_reminder.html",
        context={"order": order, "buyer": order.buyer},
        to=order.buyer.email,
    )


def send_reception_reminder(order):
    if not order.buyer.email:
        return
    send_templated_email(
        subject="Senta'a — Confirmez la réception de votre commande",
        template_name="reception_reminder.html",
        context={"order": order, "buyer": order.buyer},
        to=order.buyer.email,
    )


def send_application_reminder(job_offer, pending_count):
    if not job_offer.recruiter.email:
        return
    send_templated_email(
        subject="Senta'a — Candidatures en attente de traitement",
        template_name="application_reminder.html",
        context={
            "job_offer": job_offer,
            "pending_count": pending_count,
            "recruiter": job_offer.recruiter,
        },
        to=job_offer.recruiter.email,
    )


def send_newsletter(user, listings, job_offers):
    send_templated_email(
        subject="Senta'a — Les nouveautés de la semaine",
        template_name="newsletter.html",
        context={"user": user, "listings": listings, "job_offers": job_offers},
        to=user.email,
    )
