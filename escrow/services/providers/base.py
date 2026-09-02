"""
Interface commune aux fournisseurs Mobile Money (NotchPay, KPay, ...).

Un seul fournisseur est actif à la fois, choisi via `settings.PAYMENT_PROVIDER`
(variable d'env `PAYMENT_PROVIDER`) et instancié par
`escrow.services.providers.get_payment_client`. Chaque client traduit les codes
propres à son fournisseur (statuts, erreurs) vers le vocabulaire normalisé défini
ici, afin que le reste du code (`escrow/views.py`, `escrow/tasks.py`,
`escrow/services/payouts.py`) n'ait jamais à connaître le fournisseur actif — voir
.claude/rules/escrow-core.md.
"""

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field


class PaymentProviderError(Exception):
    """Erreur générique de communication avec un fournisseur de paiement Mobile Money.

    Chaque client fournisseur (NotchPayError, KPayError, ...) sous-classe celle-ci, pour que
    les appelants puissent choisir de capturer soit l'erreur générique (chemin provider-
    agnostique : views/tasks/payouts), soit l'erreur spécifique (tests).
    """


# Statuts normalisés, alignés sur `PaymentTransaction.STATUS_CHOICES` — seul vocabulaire que
# les appelants doivent connaître, quel que soit le fournisseur ayant traité l'opération.
STATUS_PENDING = "pending"
STATUS_SUCCESSFUL = "successful"
STATUS_FAILED = "failed"

# Façon dont l'acheteur complète une collecte, normalisée pour que le frontend n'ait jamais à
# deviner (ex. sur la seule présence/absence de `checkout_url`) — voir `ProviderResult` :
# - FLOW_REDIRECT : page hébergée fournisseur, `checkout_url` à ouvrir/rediriger.
# - FLOW_USSD : push direct sur le téléphone de l'acheteur, rien à afficher/ouvrir.
FLOW_REDIRECT = "redirect"
FLOW_USSD = "ussd"


@dataclass
class ProviderResult:
    """
    Résultat normalisé d'un appel fournisseur (initiation/vérification de paiement ou de
    transfert), indépendant du fournisseur l'ayant traité.

    - `provider_reference` : référence propre au fournisseur, à repasser tel quel à un futur
      appel `verify_payment`/`verify_transfer` (distincte de notre propre référence interne,
      stockée séparément par les appelants — voir `PaymentTransaction.external_ref`). Stockée
      dès la création (`PaymentTransaction.provider_reference`) pour que le webhook puisse la
      comparer à celle reçue dans son payload avant de faire confiance à `verify_payment` —
      voir `escrow/views.py::_CollectWebhookView._process`.
    - `status` : normalisé (STATUS_PENDING / STATUS_SUCCESSFUL / STATUS_FAILED).
    - `payment_flow` : uniquement pertinent pour `initialize_payment` — FLOW_REDIRECT (page
      hébergée fournisseur, `checkout_url` à ouvrir) ou FLOW_USSD (push direct, rien à
      afficher). Le frontend doit se fier à CE champ, jamais deviner à partir de la présence
      ou non de `checkout_url` — plusieurs fournisseurs, plusieurs modes.
    - `checkout_url` : rempli uniquement si `payment_flow == FLOW_REDIRECT`.
    - `raw` : réponse brute du fournisseur, conservée pour l'audit
      (`PaymentTransaction.raw_response`) — **jamais renvoyée telle quelle au client** (elle
      peut contenir des identifiants internes du fournisseur sans utilité pour le frontend).
      Seuls `reference`/`status`/`payment_flow`/`checkout_url` sont utiles au frontend — voir
      `escrow/views.py::initiate_payment`.
    """

    provider_reference: str | None
    status: str
    payment_flow: str = FLOW_USSD
    checkout_url: str | None = None
    raw: dict = field(default_factory=dict)


class PaymentProviderClient(ABC):
    """Un client par fournisseur. Voir `escrow/services/providers/notchpay.py` et
    `escrow/services/providers/kpay.py` pour les implémentations concrètes."""

    #: Identifiant court du fournisseur — doit correspondre à une valeur de
    #: `PaymentTransaction.PROVIDERS` (stocké sur chaque transaction pour la traçabilité et
    #: pour que la réconciliation interroge le bon fournisseur — voir escrow/tasks.py).
    name: str

    @abstractmethod
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
    ) -> ProviderResult:
        """Initie une collecte (paiement acheteur). `reference` est NOTRE référence interne
        (idempotence côté Senta'a) ; certains fournisseurs (KPay) l'exigent aussi côté
        fournisseur (`externalId`) pour leur propre idempotence — défense en profondeur.

        `channel` fourni → paiement direct (KPay : push USSD). `channel` absent → mode page
        hébergée explicite (KPay : mode GATEWAY, nécessite alors `return_url` — fournie par
        le frontend, voir `escrow/serializers.py::InitiatePaymentSerializer`) ; les
        fournisseurs qui n'ont qu'un seul mode (NotchPay, toujours page hébergée) ignorent
        ces deux derniers arguments.
        """

    @abstractmethod
    def verify_payment(self, provider_reference) -> ProviderResult:
        """Statut à jour d'une collecte, via la référence FOURNISSEUR (pas la nôtre)."""

    @abstractmethod
    def initialize_transfer(
        self,
        *,
        amount,
        account_number,
        channel,
        currency,
        reference=None,
        description="",
    ) -> ProviderResult:
        """Initie un reversement (vendeur ou coursier)."""

    @abstractmethod
    def verify_transfer(self, provider_reference) -> ProviderResult:
        """Statut à jour d'un reversement, via la référence FOURNISSEUR."""
