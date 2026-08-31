"""Désinscription newsletter en un clic (RFC 8058 — "List-Unsubscribe=One-Click").

Gmail/Yahoo exigent depuis 2024, pour tout envoi en volume, un lien qui désinscrit
SANS connexion ni confirmation supplémentaire — voir la vue
`notifications.views.unsubscribe_newsletter`. On ne peut donc pas s'appuyer sur la
session applicative (JWT) : un token scellé (signature + expiration), indépendant de
toute authentification, sert de preuve suffisante et n'autorise que la désinscription
de l'utilisateur qu'il désigne.
"""

from django.conf import settings
from django.core import signing

_SALT = "notifications.newsletter-unsubscribe"
# Un lien de désinscription doit rester valable même si l'email traîne longtemps dans une
# boîte de réception avant d'être ouvert — 90 jours, largement au-delà du rythme hebdomadaire
# de la newsletter (voir notifications/tasks.py::send_weekly_newsletter).
_MAX_AGE_SECONDS = 60 * 60 * 24 * 90


def generate_unsubscribe_token(user):
    return signing.TimestampSigner(salt=_SALT).sign(str(user.id))


def resolve_unsubscribe_token(token):
    """Retourne l'UUID utilisateur (str) si `token` est valide et non expiré, sinon None.

    Ne lève jamais : un lien invalide/expiré/trafiqué est un cas attendu (vieux mail, lien
    corrompu par un client mail), pas une erreur serveur.
    """
    try:
        return signing.TimestampSigner(salt=_SALT).unsign(
            token, max_age=_MAX_AGE_SECONDS
        )
    except signing.BadSignature:
        return None


def build_unsubscribe_url(user):
    token = generate_unsubscribe_token(user)
    return f"{settings.BACKEND_PUBLIC_URL}/api/notifications/unsubscribe-newsletter/?token={token}"
