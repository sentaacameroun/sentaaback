import random
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)

class OTPService:
    @staticmethod
    def generate_otp(phone_number):
        """Génère un code, le stocke dans Redis pendant 5 min"""
        otp = str(random.randint(100000, 999999))
        # Clé unique dans Redis : "otp_237670000000"
        cache.set(f"otp_{phone_number}", otp, timeout=300) 
        
        # Simulation d'envoi SMS
        print(f"DEBUG: Code OTP pour {phone_number} est {otp}")
        # Ici on intègrera l'API SMS (ex: Orange, MTN ou Twilio)
        return otp

    @staticmethod
    def verify_otp(phone_number, otp_code):
        """Vérifie si le code en cache correspond"""
        cached_otp = cache.get(f"otp_{phone_number}")
        if cached_otp and cached_otp == otp_code:
            cache.delete(f"otp_{phone_number}") # Code à usage unique
            return True
        return False