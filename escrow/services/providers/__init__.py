"""
Architecture multi-provider Mobile Money. Un seul fournisseur est actif à la fois, choisi via
la variable d'env `PAYMENT_PROVIDER` (`settings.PAYMENT_PROVIDER`) et instancié par
`get_payment_client()` — jamais en instanciant `NotchPayClient()`/`KPayClient()` directement
dans `escrow/views.py`, `escrow/tasks.py` ou `escrow/services/payouts.py` (sauf dans les
webhooks, qui sont par construction propres à UN fournisseur : voir `escrow/views.py`).

Ajouter un fournisseur : créer `<nom>.py` implémentant `PaymentProviderClient` (voir
`.base`), puis l'enregistrer dans `_PROVIDERS` ci-dessous et dans
`PaymentTransaction.PROVIDERS` (escrow/models.py).
"""

from django.conf import settings

from .base import FLOW_REDIRECT
from .base import FLOW_USSD
from .base import PaymentProviderClient
from .base import PaymentProviderError
from .base import ProviderResult
from .base import STATUS_FAILED
from .base import STATUS_PENDING
from .base import STATUS_SUCCESSFUL
from .kpay import KPayClient
from .kpay import KPayError
from .notchpay import NotchPayClient
from .notchpay import NotchPayError

_PROVIDERS = {
    "notchpay": NotchPayClient,
    "kpay": KPayClient,
}


def get_payment_client(provider_name=None):
    """
    Fabrique du client de paiement actif. Sans argument, lit `settings.PAYMENT_PROVIDER`
    (fournisseur actif pour toute nouvelle opération). `provider_name` explicite sert à la
    réconciliation (escrow/tasks.py) : chaque `PaymentTransaction` garde le fournisseur qui
    l'a réellement traitée (`PaymentTransaction.provider`), pour qu'un changement de
    `PAYMENT_PROVIDER` en cours de route n'envoie jamais une réconciliation vers le mauvais
    fournisseur. Une valeur vide/inconnue (lignes créées avant l'ajout du champ, voir
    escrow/models.py) retombe sur le fournisseur actif.
    """
    name = provider_name or settings.PAYMENT_PROVIDER
    try:
        provider_cls = _PROVIDERS[name]
    except KeyError:
        raise PaymentProviderError(
            f"PAYMENT_PROVIDER={name!r} inconnu (valeurs valides : "
            f"{', '.join(sorted(_PROVIDERS))})"
        ) from None
    return provider_cls()


__all__ = [
    "PaymentProviderClient",
    "PaymentProviderError",
    "ProviderResult",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_SUCCESSFUL",
    "FLOW_REDIRECT",
    "FLOW_USSD",
    "KPayClient",
    "KPayError",
    "NotchPayClient",
    "NotchPayError",
    "get_payment_client",
]
