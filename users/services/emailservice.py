import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when an OTP email could not be sent."""


class OTPEmailService:
    @staticmethod
    def send_otp_email(to_email, otp):
        try:
            send_mail(
                subject="Votre code de connexion Senta'a",
                message=f"Votre code de connexion est : {otp}\n\nCe code expire dans 5 minutes.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[to_email],
                fail_silently=False,
            )
        except Exception as exc:
            logger.exception("Échec d'envoi de l'OTP par email à %s", to_email)
            raise EmailDeliveryError(str(exc)) from exc
