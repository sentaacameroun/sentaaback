import logging

from django.http import HttpResponse
from django.template.loader import render_to_string
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import OpenApiResponse
from rest_framework import permissions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from notifications.emails import logo_url
from notifications.models import DeviceToken
from notifications.serializers import DeviceTokenRegisterSerializer
from notifications.serializers import DeviceTokenUnregisterSerializer
from notifications.services.unsubscribe import resolve_unsubscribe_token
from users.models import User

logger = logging.getLogger(__name__)


class RegisterDeviceView(APIView):
    """Crée ou met à jour (par `token`) l'entrée `DeviceToken` de l'utilisateur courant.

    Le token FCM est la clé de recherche (unique) plutôt que le couple user+device_id : un
    même appareil peut se réinscrire (refresh de token côté Firebase) ou changer de compte
    (logout/login d'un autre utilisateur sur le même appareil) — dans les deux cas on
    réattribue simplement l'entrée à l'utilisateur courant et on la réactive.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Enregistre ou met à jour le token FCM de l'appareil courant",
        request=DeviceTokenRegisterSerializer,
        responses={200: DeviceTokenRegisterSerializer},
        tags=["Notifications"],
    )
    def post(self, request):
        serializer = DeviceTokenRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        device_token, _ = DeviceToken.objects.update_or_create(
            token=data["token"],
            defaults={
                "user": request.user,
                "platform": data["platform"],
                "device_id": data.get("device_id", ""),
                "active": True,
            },
        )
        return Response(
            DeviceTokenRegisterSerializer(device_token).data, status=status.HTTP_200_OK
        )


class UnregisterDeviceView(APIView):
    """Marque `active=False` pour le token fourni (appelé au logout).

    Scope explicite sur `request.user` : seul le propriétaire du token peut le désactiver.
    Idempotent — un token déjà inactif ou introuvable (ou appartenant à quelqu'un d'autre)
    renvoie 200 sans erreur, le logout ne doit jamais échouer sur ce détail.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Désenregistre le token FCM de l'appareil courant (logout)",
        request=DeviceTokenUnregisterSerializer,
        responses={
            200: OpenApiResponse(description="Token désactivé (ou déjà inactif)"),
        },
        tags=["Notifications"],
    )
    def post(self, request):
        serializer = DeviceTokenUnregisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        DeviceToken.objects.filter(
            user=request.user, token=serializer.validated_data["token"]
        ).update(active=False)
        return Response(status=status.HTTP_200_OK)


def _unsubscribe_page(title, message, http_status):
    html = render_to_string(
        "emails/unsubscribe_result.html",
        {"title": title, "message": message, "logo_url": logo_url()},
    )
    return HttpResponse(html, status=http_status, content_type="text/html")


class UnsubscribeNewsletterView(APIView):
    """Désinscription newsletter en un clic, sans connexion (RFC 8058 — voir
    notifications/services/unsubscribe.py et le header `List-Unsubscribe-Post` ajouté sur
    l'email dans notifications/emails.py::send_newsletter).

    Volontairement hors DRF standard (pas de JSON, `AllowAny`) : ce lien est cliqué depuis un
    client mail ou posté automatiquement par lui, jamais par l'app. Exclu du schéma OpenAPI
    (n'a pas sa place dans la doc API grand public).
    """

    permission_classes = [permissions.AllowAny]

    @extend_schema(exclude=True)
    def get(self, request):
        return self._unsubscribe(request, render_page=True)

    @extend_schema(exclude=True)
    def post(self, request):
        # One-click (RFC 8058) : le client mail poste en arrière-plan, sans afficher de page.
        return self._unsubscribe(request, render_page=False)

    def _unsubscribe(self, request, render_page):
        token = request.query_params.get("token") or request.data.get("token")
        user_id = resolve_unsubscribe_token(token) if token else None

        if not user_id:
            if not render_page:
                return Response(status=status.HTTP_400_BAD_REQUEST)
            return _unsubscribe_page(
                "Lien invalide",
                "Ce lien de désinscription est invalide ou a expiré.",
                status.HTTP_400_BAD_REQUEST,
            )

        # `.update()` plutôt que `get()+save()` : idempotent par construction (un utilisateur
        # déjà désinscrit, ou un id qui ne correspond à personne, ne doit jamais faire échouer
        # le clic — même philosophie que UnregisterDeviceView).
        User.objects.filter(pk=user_id).update(newsletter_opt_in=False)

        if not render_page:
            return Response(status=status.HTTP_200_OK)
        return _unsubscribe_page(
            "Désinscription confirmée",
            "Vous ne recevrez plus la newsletter Senta'a. Vous pouvez vous réinscrire à "
            "tout moment depuis les paramètres de votre compte.",
            status.HTTP_200_OK,
        )
