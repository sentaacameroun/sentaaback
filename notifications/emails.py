import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils.html import strip_tags

from notifications.services.unsubscribe import build_unsubscribe_url

logger = logging.getLogger(__name__)


def logo_url():
    # URL absolue obligatoire : un client mail charge l'image depuis l'appareil du
    # destinataire, pas depuis le même hôte que la page qui a rendu le lien relatif.
    return f"{settings.BACKEND_PUBLIC_URL}{static('notifications/emails/logo.png')}"


def email_base_context():
    """Contexte commun à tous les emails HTML (voir templates/emails/base_email.html).

    Public : réutilisé hors de l'app `notifications` par `users.services.emailservice`
    (email OTP) pour éviter de dupliquer la construction de ces trois valeurs.
    """
    return {
        "logo_url": logo_url(),
        "site_url": "https://sentaa.net",
        "company_postal_address": settings.COMPANY_POSTAL_ADDRESS,
    }


def send_templated_email(subject, template_name, context, to, headers=None):
    """Envoie un email HTML standardisé (voir notifications/templates/emails/base_email.html)
    avec une alternative texte brut obligatoire (déliverabilité — un email HTML-only sans
    partie texte est un signal anti-spam classique).

    La partie texte vient d'un template `.txt` dédié du même nom si présent (rendu fidèle,
    pas de CSS qui fuite dedans) ; à défaut, repli sur un simple `strip_tags` du HTML.
    """
    if not to:
        return
    full_context = {**email_base_context(), **context}
    html_body = render_to_string(f"emails/{template_name}", full_context)

    text_template = template_name.rsplit(".", 1)[0] + ".txt"
    try:
        text_body = render_to_string(f"emails/{text_template}", full_context).strip()
    except TemplateDoesNotExist:
        text_body = strip_tags(html_body)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
        headers=headers,
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)


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
    # Seul email non transactionnel envoyé par l'app : les en-têtes One-Click
    # (RFC 8058) sont exigées par Gmail/Yahoo pour tout envoi en volume depuis 2024, et
    # attendues par la plupart des filtres anti-spam au-delà de ce seuil. `resolve_unsubscribe
    # _token` (voir notifications/services/unsubscribe.py) est vérifié indépendamment de la
    # session applicative : le lien doit marcher sans connexion.
    unsubscribe_url = build_unsubscribe_url(user)
    send_templated_email(
        subject="Senta'a — Les nouveautés de la semaine",
        template_name="newsletter.html",
        context={
            "user": user,
            "listings": listings,
            "job_offers": job_offers,
            "unsubscribe_url": unsubscribe_url,
        },
        to=user.email,
        headers={
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
