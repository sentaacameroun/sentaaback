import logging
import secrets

from django.conf import settings
from django.core.cache import cache

from users.services.emailservice import EmailDeliveryError
from users.services.emailservice import OTPEmailService
from users.services.smsservice import SMSDeliveryError
from users.services.smsservice import TwilioSMSService

logger = logging.getLogger(__name__)


class OTPSendError(Exception):
    """Raised when the OTP was generated but could not be delivered to the user."""


class OTPService:
    @staticmethod
    def generate_otp(identifier, channel="sms"):
        """
        Génère un code, le stocke en cache pendant 5 min, puis l'envoie par SMS ou email selon
        `channel`. `identifier` est le numéro de téléphone (channel='sms') ou l'email
        (channel='email') servant de clé de cache — les deux espaces ne se recoupent jamais
        (un numéro et une adresse email ne peuvent pas être des chaînes identiques).
        """
        # Générateur cryptographique (secrets) plutôt que random.randint (MINEUR #13),
        # cohérent avec logistics/models.py. randbelow(1_000_000) → 0..999999, zéro-paddé
        # sur 6 chiffres (les codes à zéros de tête restent valides).
        otp = f"{secrets.randbelow(1_000_000):06d}"
        cache.set(f"otp_{identifier}", otp, timeout=300)

        if channel == "email":
            # Pas de switch dédié comme SMS_BACKEND : EMAIL_BACKEND (settings.py, piloté par
            # EMAIL_HOST) gère déjà "console en dev/tests, SMTP réel en prod".
            try:
                OTPEmailService.send_otp_email(identifier, otp)
            except EmailDeliveryError as exc:
                raise OTPSendError(str(exc)) from exc
            return otp

        backend = getattr(settings, "SMS_BACKEND", "console")
        if backend == "twilio":
            try:
                TwilioSMSService.send_sms(identifier, f"Votre code Senta'a : {otp}")
            except SMSDeliveryError as exc:
                raise OTPSendError(str(exc)) from exc
        else:
            # Backend "console" : utilisé en dev/tests, aucun appel réseau.
            logger.info("DEBUG: Code OTP pour %s est %s", identifier, otp)

        return otp

    @staticmethod
    def verify_otp(identifier, otp_code):
        """Vérifie si le code en cache correspond (identifier = phone_number ou email)."""
        cached_otp = cache.get(f"otp_{identifier}")
        if cached_otp and cached_otp == otp_code:
            cache.delete(f"otp_{identifier}")  # Code à usage unique
            return True
        return False
