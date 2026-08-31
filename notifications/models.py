from django.conf import settings
from django.db import models


class DeviceToken(models.Model):
    """Token FCM d'un appareil, pour l'envoi de push (voir notifications/services/push.py).

    Un utilisateur peut avoir plusieurs tokens actifs (plusieurs appareils). Un token
    invalide/désinscrit est désactivé (`active=False`), jamais supprimé, pour garder un
    historique (voir .claude/rules/push-notifications.md "Nettoyage des tokens").
    """

    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(max_length=10, choices=Platform.choices)
    device_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Identifiant local de l'appareil (optionnel), pour distinguer plusieurs "
        "appareils du même utilisateur au-delà du seul token.",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user", "active"])]

    def __str__(self):
        return f"{self.user_id} · {self.platform} · {self.token[:12]}…"
