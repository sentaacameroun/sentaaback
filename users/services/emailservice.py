import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from notifications.emails import email_base_context

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when an OTP email could not be sent."""


class OTPEmailService:
    @staticmethod
    def send_otp_email(to_email, otp):
        # Le corps texte reste la source de vérité inchangée (extrait par regex dans les
        # tests de connexion par email, voir users/tests.py::EmailLoginTests) — seule une
        # alternative HTML (template partagé, voir notifications/emails.py) s'y ajoute.
        text_body = (
            f"Votre code de connexion est : {otp}\n\nCe code expire dans 5 minutes."
        )
        try:
            html_body = render_to_string(
                "emails/otp.html", {**email_base_context(), "otp": otp}
            )
            message = EmailMultiAlternatives(
                subject="Votre code de connexion Senta'a",
                body=text_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[to_email],
            )
            message.attach_alternative(html_body, "text/html")
            message.send(fail_silently=False)
        except Exception as exc:
            logger.exception("Échec d'envoi de l'OTP par email à %s", to_email)
            raise EmailDeliveryError(str(exc)) from exc
