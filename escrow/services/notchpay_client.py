import requests
from django.conf import settings


class NotchPayError(Exception):
    """Raised when a call to the NotchPay API fails or times out."""


class NotchPayClient:
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

    def initialize_payment(
        self, amount, phone, currency, reference, description="", metadata=None
    ):
        payload = {
            "amount": int(amount),
            "currency": currency,
            "reference": reference,
            "description": description,
            "customer": {"phone": phone},
            "metadata": metadata or {},
        }
        return self._request("POST", "/payments", self.public_key, json=payload)

    def verify_payment(self, reference):
        return self._request("GET", f"/payments/{reference}", self.private_key)

    def initialize_transfer(
        self, amount, account_number, channel, currency, description=""
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
        return self._request("POST", "/transfers", self.private_key, json=payload)

    def verify_transfer(self, reference):
        """
        Statut d'un reversement (transfer/payout). Endpoint distinct de `verify_payment`
        (`/payments/{ref}`, réservé aux collectes) : un transfert Mobile Money est
        asynchrone et peut rester `pending` après l'appel `initialize_transfer` — seul
        `/transfers/{ref}` en donne le statut final, ce qui permet la réconciliation des
        reversements bloqués (escrow/tasks.py::reconcile_pending_payouts).
        """
        return self._request("GET", f"/transfers/{reference}", self.private_key)
