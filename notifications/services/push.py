"""Service d'envoi de notifications push via Firebase Cloud Messaging (API HTTP v1).

Point d'entrée unique côté application : `send_push`. Mais `send_push` elle-même ne doit
JAMAIS être appelée de façon synchrone depuis une vue ou un service métier — le seul appelant
légitime est la tâche Celery `notifications.tasks.send_push_notification_task` (voir
.claude/rules/push-notifications.md, "Jamais dans le chemin critique d'une transaction").

Utilise exclusivement l'API v1 (`firebase_admin.messaging`) : l'ancienne API legacy
(`fcm.googleapis.com/fcm/send`) est désactivée par Google depuis juin 2024.
"""

import json
import logging

import firebase_admin
from django.conf import settings
from firebase_admin import credentials
from firebase_admin import exceptions as firebase_exceptions
from firebase_admin import messaging

from notifications.models import DeviceToken

logger = logging.getLogger(__name__)

# Erreurs FCM (API v1) qui signifient que le token est mort — désinstallé, expiré, ou
# jamais valide. `UnregisteredError` est l'exception dédiée exposée par le SDK pour ce cas
# (cf. doc Firebase, remplace le code `NotRegistered` de l'ancienne API legacy). On traite en
# plus `InvalidArgumentError` : un token malformé ne redeviendra jamais valide non plus.
_DEAD_TOKEN_EXCEPTIONS = (
    messaging.UnregisteredError,
    firebase_exceptions.InvalidArgumentError,
)


def _ensure_initialized():
    """Initialise l'app firebase-admin par défaut si ce n'est pas déjà fait.

    Singleton process-wide (comme `cloudinary.config()`) : un seul `initialize_app()` par
    process, peu importe combien de fois `send_push` est appelée.

    `GOOGLE_APPLICATION_CREDENTIALS` contient ici le JSON complet du compte de service
    (pas un chemin de fichier) : évite d'avoir à déposer ce fichier sur le VPS, la variable
    d'environnement suffit. `credentials.Certificate` accepte un dict représentant le
    contenu du fichier aussi bien qu'un chemin — on lui passe le dict parsé.
    """
    if firebase_admin._apps:
        return
    creds_json = settings.GOOGLE_APPLICATION_CREDENTIALS
    if not creds_json:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS n'est pas configuré : impossible d'initialiser "
            "firebase-admin."
        )
    try:
        cred_info = json.loads(creds_json)
    except ValueError as exc:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS doit contenir le JSON du compte de service "
            "Firebase (pas un chemin de fichier) : contenu invalide."
        ) from exc
    firebase_admin.initialize_app(credentials.Certificate(cred_info))


def _deactivate(device_token):
    device_token.active = False
    device_token.save(update_fields=["active", "updated_at"])
    logger.info(
        "DeviceToken désactivé (token invalide/désinscrit) : user=%s token=%s…",
        device_token.user_id,
        device_token.token[:12],
    )


def send_push(user, title, body, data=None):
    """Envoie une notification push à tous les appareils actifs de `user`.

    - N'échoue jamais pour l'appelant : toute erreur d'envoi est journalisée, jamais levée.
    - Chaque token est traité indépendamment : l'échec d'un appareil n'empêche pas l'envoi
      aux autres appareils de l'utilisateur.
    - Un token dont l'envoi échoue avec une erreur "mort" (désinstallé/désinscrit/invalide)
      est automatiquement désactivé (`active=False`), jamais retenté.

    Retourne le nombre d'envois réussis.
    """
    tokens = list(DeviceToken.objects.filter(user=user, active=True))
    if not tokens:
        return 0

    _ensure_initialized()

    sent = 0
    for device_token in tokens:
        # `messaging.Message(token=...)` est marqué deprecated par le SDK au profit de `fid`
        # (Firebase installation ID) — mais `fid` est une notion distincte qui suppose le SDK
        # Firebase Installations côté client, pas celui utilisé ici. `DeviceToken.token` est
        # bien un token d'enregistrement FCM classique (`firebase_messaging.getToken()` côté
        # Flutter, voir PUSH_NOTIFICATIONS_PLAN.md FE-PUSH-1) : `token=` reste le bon paramètre.
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={str(key): str(value) for key, value in (data or {}).items()},
            token=device_token.token,
        )
        try:
            messaging.send(message)
        except _DEAD_TOKEN_EXCEPTIONS:
            _deactivate(device_token)
        except firebase_exceptions.FirebaseError:
            logger.exception(
                "Échec envoi push : user=%s token=%s…", user.id, device_token.token[:12]
            )
        else:
            sent += 1
    return sent
