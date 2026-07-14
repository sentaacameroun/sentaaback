import logging

from django.conf import settings
from twilio.base.exceptions import TwilioRestException


logger = logging.getLogger(__name__)


class SMSDeliveryError(Exception):
    """Raised when an SMS could not be sent through the configured provider."""


class TwilioSMSService:
    @staticmethod
    def send_sms(to, body):
        from twilio.rest import Client

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        try:
            client.messages.create(body=body, from_=settings.TWILIO_FROM_NUMBER, to=to)
        except TwilioRestException as exc:
            logger.exception("Échec d'envoi SMS Twilio vers %s", to)
            raise SMSDeliveryError(str(exc)) from exc
