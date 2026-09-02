import requests
from django.conf import settings

from .base import FLOW_REDIRECT
from .base import PaymentProviderClient
from .base import PaymentProviderError
from .base import ProviderResult
from .base import STATUS_FAILED
from .base import STATUS_PENDING
from .base import STATUS_SUCCESSFUL

_SUCCESS_STATUSES = {"complete", "completed", "successful", "success"}
_FAILURE_STATUSES = {"failed", "canceled", "cancelled", "rejected", "error"}


class NotchPayError(PaymentProviderError):
    """Raised when a call to the NotchPay API fails or times out."""


class NotchPayClient(PaymentProviderClient):
    name = "notchpay"

    def __init__(self):
        self.base_url = settings.NOTCHPAY_BASE_URL.rstrip("/")
        self.public_key = settings.NOTCHPAY_PUBLIC_KEY
        self.private_key = settings.NOTCHPAY_PRIVATE_KEY

    def _request(self, method, path, auth_key, json=None, params=None):
        url = f"{self.base_url}{path}"
        headers = {"Authorization": auth_key}
        try:
            response = requests.request(
                method, url, json=json, params=params, headers=headers, timeout=15
            )
        except requests.RequestException as exc:
            raise NotchPayError(f"NotchPay request failed: {exc}") from exc

        if not response.ok:
            raise NotchPayError(
                f"NotchPay error {response.status_code}: {response.text}"
            )
        return response.json()

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
        # NotchPay n'a qu'un seul mode (toujours page hébergée) : `channel`/`return_url`/
        # `cancel_url` sont ignorés — seul KPay distingue USSD direct / mode GATEWAY (voir
        # kpay.py). `phone` reste optionnel : un appelant demandant explicitement le mode
        # page hébergée (aucun channel fourni, voir InitiatePaymentSerializer) peut aussi ne
        # pas fournir de téléphone ; on omet alors `customer` plutôt que d'envoyer une valeur
        # nulle non vérifiée côté NotchPay.
        payload = {
            "amount": int(amount),
            "currency": currency,
            "reference": reference,
            "description": description,
            "metadata": metadata or {},
            "callback": return_url,  # NotchPay n'a qu'un seul callback, on prend le premier non nul
        }
        if phone:
            payload["customer"] = {"phone": phone}
        result = self._request("POST", "/payments", self.public_key, json=payload)
        transaction = result.get("transaction") or {}
        return ProviderResult(
            provider_reference=transaction.get("reference"),
            status=self._normalize_status(transaction.get("status")),
            payment_flow=FLOW_REDIRECT,
            checkout_url=transaction.get("authorization_url"),
            raw=result,
        )

    def verify_payment(self, provider_reference):
        result = self._request(
            "GET", f"/payments/{provider_reference}", self.private_key
        )
        transaction = result.get("transaction") or {}
        return ProviderResult(
            provider_reference=transaction.get("reference", provider_reference),
            status=self._normalize_status(transaction.get("status")),
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
            "currency": currency,
            "description": description,
            "recipient": {
                "account_number": account_number,
                "channel": channel,
            },
        }
        result = self._request("POST", "/transfers", self.private_key, json=payload)
        transaction = result.get("transaction") or {}
        return ProviderResult(
            provider_reference=transaction.get("reference"),
            status=self._normalize_status(transaction.get("status")),
            raw=result,
        )

    def verify_transfer(self, provider_reference):
        result = self._request(
            "GET", f"/transfers/{provider_reference}", self.private_key
        )
        transfer = result.get("transfer") or result.get("transaction") or {}
        return ProviderResult(
            provider_reference=transfer.get("reference", provider_reference),
            status=self._normalize_status(transfer.get("status")),
            raw=result,
        )
