import requests
from django.conf import settings

from .base import FLOW_REDIRECT
from .base import FLOW_USSD
from .base import PaymentProviderClient
from .base import PaymentProviderError
from .base import ProviderResult
from .base import STATUS_FAILED
from .base import STATUS_PENDING
from .base import STATUS_SUCCESSFUL

_CHANNEL_TO_KPAY_PROVIDER = {
    "mtn": "MTN_MOMO_CMR",
    "orange": "ORANGE_CMR",
}

_SUCCESS_STATUSES = {"completed"}
_FAILURE_STATUSES = {"failed", "cancelled"}


class KPayError(PaymentProviderError):
    """Raised when a call to the KPay API fails or times out."""


class KPayClient(PaymentProviderClient):
    """Intégrateur principal (voir `settings.PAYMENT_PROVIDER`). Doc :
    https://kpay.site/documentation — auth par en-têtes `X-API-Key` / `X-Secret-Key`, montants
    en unité entière de la devise (pas de sous-unité), statuts PENDING/PROCESSING/COMPLETED/
    FAILED/CANCELLED normalisés ici vers le vocabulaire commun (voir `.base`)."""

    name = "kpay"

    def __init__(self):
        self.base_url = settings.KPAY_BASE_URL.rstrip("/")
        self.api_key = settings.KPAY_API_KEY
        self.secret_key = settings.KPAY_SECRET_KEY

    def _headers(self):
        return {"X-API-Key": self.api_key, "X-Secret-Key": self.secret_key}

    def _request(self, method, path, json=None):
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method, url, json=json, headers=self._headers(), timeout=15
            )
        except requests.RequestException as exc:
            raise KPayError(f"KPay request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if not response.ok:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise KPayError(
                f"KPay error {response.status_code}: {message or response.text}"
            )
        return payload

    @staticmethod
    def _channel_to_provider(channel):
        try:
            return _CHANNEL_TO_KPAY_PROVIDER[channel]
        except KeyError:
            raise KPayError(f"Canal KPay non supporté : {channel!r}") from None

    @staticmethod
    def _normalize_status(raw_status):
        raw_status = (raw_status or "").lower()
        if raw_status in _SUCCESS_STATUSES:
            return STATUS_SUCCESSFUL
        if raw_status in _FAILURE_STATUSES:
            return STATUS_FAILED
        return STATUS_PENDING

    def initialize_payment(
        self,
        *,
        amount,
        phone,
        currency,
        reference,
        channel=None,
        description="",
        metadata=None,
        return_url=None,
        cancel_url=None,
    ):
        payload = {
            "amount": int(amount),
            "externalId": reference,
            "description": description,
        }
        if channel:
            # Mode USSD : push direct sur le téléphone de l'acheteur, rien à afficher côté
            # frontend (https://kpay.site/documentation/paiements).
            payload["provider"] = self._channel_to_provider(channel)
            payload["phoneNumber"] = phone
            flow = FLOW_USSD
        else:
            # Mode GATEWAY (aucun `provider`/`phoneNumber` envoyé) : page hébergée KPay
            # proposant Mobile Money ET cartes bancaires/PayPal
            # (https://kpay.site/documentation/cartes-paypal) — le client choisit sur place.
            # `returnUrl` est obligatoire côté KPay ; `InitiatePaymentSerializer` l'exige déjà
            # avant d'arriver ici, ce garde-fou couvre un appel direct au client hors vue.
            if not return_url:
                raise KPayError(
                    "return_url est requis pour initier un paiement KPay en mode GATEWAY "
                    "(aucun channel fourni)"
                )
            payload["returnUrl"] = return_url
            if cancel_url:
                payload["cancelUrl"] = cancel_url

            flow = FLOW_REDIRECT
        if metadata:
            payload["metadata"] = metadata
        result = self._request("POST", "/api/v1/payments/init", json=payload)
        return ProviderResult(
            provider_reference=result.get("id"),
            status=self._normalize_status(result.get("status")),
            payment_flow=flow,
            checkout_url=result.get("gatewayUrl"),
            raw=result,
        )

    def verify_payment(self, provider_reference):
        result = self._request("GET", f"/api/v1/payments/{provider_reference}")
        return ProviderResult(
            provider_reference=result.get("id", provider_reference),
            status=self._normalize_status(result.get("status")),
            raw=result,
        )

    def initialize_transfer(
        self,
        *,
        amount,
        account_number,
        channel,
        currency,
        reference=None,
        description="",
    ):
        payload = {
            "amount": int(amount),
            "provider": self._channel_to_provider(channel),
            "phoneNumber": account_number,
            "description": description,
        }
        if reference:
            payload["externalId"] = reference
        result = self._request("POST", "/api/v1/payments/withdraw", json=payload)
        return ProviderResult(
            provider_reference=result.get("id"),
            status=self._normalize_status(result.get("status")),
            raw=result,
        )

    def verify_transfer(self, provider_reference):
        result = self._request("GET", f"/api/v1/payments/withdraw/{provider_reference}")
        return ProviderResult(
            provider_reference=result.get("id", provider_reference),
            status=self._normalize_status(result.get("status")),
            raw=result,
        )
