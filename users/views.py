import logging

from cloudinary.exceptions import Error as CloudinaryError
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import OpenApiResponse
from rest_framework import permissions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from common.images.validators import ImageFileValidator
from users.serializers import OTPRequestSerializer
from users.serializers import OTPVerifySerializer
from users.serializers import RegisterSerializer
from users.serializers import UserSerializer
from users.services.otpservice import OTPSendError
from users.services.otpservice import OTPService

User = get_user_model()
logger = logging.getLogger(__name__)

# Même plafond de poids que le champ modèle User.courier_id_document (voir users/models.py) :
# revalidé ici car cette vue assigne/sauvegarde directement le champ sans passer par un
# serializer/ModelForm, donc sans jamais déclencher `Model.full_clean()` (le seul moment où
# le validateur posé sur le champ modèle s'exécuterait autrement).
_validate_courier_document = ImageFileValidator(max_bytes=5 * 1024 * 1024)


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(
        summary="Inscription d'un nouvel utilisateur",
        request=RegisterSerializer,
        responses={
            201: UserSerializer,
            400: OpenApiResponse(description="Erreur de validation"),
        },
        tags=["Authentification"],
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            try:
                OTPService.generate_otp(str(user.email), channel="email")
            except OTPSendError:
                return Response(
                    {
                        "error": "Compte créé mais l'envoi du code OTP a échoué, réessayez."
                    },
                    status=502,
                )
            return Response(
                {
                    "message": "Utilisateur créé. Code OTP envoyé.",
                    "user": UserSerializer(user).data,
                },
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    summary="Vérification du code OTP pour l'inscription",
    request=OTPRequestSerializer,
    responses={
        200: UserSerializer,
        400: OpenApiResponse(description="Code invalide ou expiré"),
        404: OpenApiResponse(description="Utilisateur non trouvé"),
    },
    tags=["Authentification"],
)
class LoginOTPRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        serializer = OTPRequestSerializer(data=request.data)
        if serializer.is_valid():
            phone = serializer.validated_data.get("phone_number")
            email = serializer.validated_data.get("email")

            if phone:
                identifier, channel, lookup = (
                    str(phone),
                    "sms",
                    {"phone_number": str(phone)},
                )
            else:
                identifier, channel, lookup = email, "email", {"email": email}

            # Réponse identique que le compte existe ou non : empêche
            # l'énumération de comptes (audit MINEUR #15). L'OTP n'est
            # généré/envoyé que si un compte correspond, mais le client ne
            # peut pas distinguer les deux cas depuis la réponse.
            if User.objects.filter(**lookup).exists():
                try:
                    OTPService.generate_otp(identifier, channel=channel)
                except OTPSendError:
                    return Response(
                        {"error": "Échec de l'envoi du code OTP, réessayez."},
                        status=502,
                    )
            return Response(
                {"message": "Si un compte correspond, un code OTP a été envoyé."}
            )
        return Response(serializer.errors, status=400)


@extend_schema(
    summary="Vérification du code OTP pour la connexion",
    request=OTPVerifySerializer,
    responses={
        200: UserSerializer,
        400: OpenApiResponse(description="Code invalide ou expiré"),
        404: OpenApiResponse(description="Utilisateur non trouvé"),
    },
    tags=["Authentification"],
)
class LoginOTPVerifyView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        if serializer.is_valid():
            phone = serializer.validated_data.get("phone_number")
            email = serializer.validated_data.get("email")
            code = serializer.validated_data["otp_code"]

            if phone:
                identifier, lookup = str(phone), {"phone_number": str(phone)}
            else:
                identifier, lookup = email, {"email": email}

            if OTPService.verify_otp(identifier, code):
                try:
                    user = User.objects.get(**lookup)
                except User.DoesNotExist:
                    return Response({"error": "Utilisateur non trouvé."}, status=404)
                refresh = RefreshToken.for_user(user)
                return Response(
                    {
                        "refresh": str(refresh),
                        "access": str(refresh.access_token),
                        "user": UserSerializer(user).data,
                    }
                )
            return Response({"error": "Code invalide ou expiré."}, status=400)
        return Response(serializer.errors, status=400)


class LogoutView(APIView):
    """Révoque un refresh token en le blacklistant (nécessite l'app
    token_blacklist + BLACKLIST_AFTER_ROTATION, voir settings.SIMPLE_JWT).
    Une fois blacklisté, le token est refusé par TokenRefreshView."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Déconnexion (révocation du refresh token)",
        responses={
            205: OpenApiResponse(description="Token révoqué"),
            400: OpenApiResponse(description="Refresh token manquant ou invalide"),
        },
        tags=["Authentification"],
    )
    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"error": "Le refresh token est requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            return Response(
                {"error": "Token invalide ou déjà révoqué."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_205_RESET_CONTENT)


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        if "latitude" in request.data or "longitude" in request.data:
            user.location_updated_at = timezone.now()
            user.save(update_fields=["location_updated_at"])
        return Response(UserSerializer(user).data)


class ApplyCourierView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        if user.is_courier:
            return Response({"detail": "Vous êtes déjà coursier."}, status=200)
        if user.courier_application_status == "pending":
            return Response(
                {"detail": "Candidature déjà en attente de validation."}, status=200
            )

        vehicle_type = request.data.get("vehicle_type")
        if vehicle_type not in dict(User.COURIER_VEHICLE_CHOICES):
            return Response(
                {"error": "Type de véhicule invalide (moto, velo ou voiture)."},
                status=400,
            )
        id_document = request.data.get("id_document")
        if not id_document:
            return Response(
                {"error": "Une photo de pièce d'identité est requise."}, status=400
            )
        try:
            _validate_courier_document(id_document)
        except DjangoValidationError as exc:
            return Response({"error": " ".join(exc.messages)}, status=400)

        user.courier_vehicle_type = vehicle_type
        user.courier_id_document = id_document
        user.courier_application_status = "pending"
        try:
            with transaction.atomic():
                user.save(
                    update_fields=[
                        "courier_vehicle_type",
                        "courier_id_document",
                        "courier_application_status",
                    ]
                )
        except CloudinaryError:
            logger.exception(
                "Échec upload Cloudinary pour la pièce d'identité de l'utilisateur %s",
                user.id,
            )
            return Response(
                {
                    "error": (
                        "Service de stockage d'images momentanément indisponible, réessayez."
                    )
                },
                status=502,
            )
        return Response(
            {"detail": "Candidature envoyée, en attente de validation."}, status=201
        )
