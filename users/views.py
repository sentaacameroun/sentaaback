from django.contrib.auth import get_user_model
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import OpenApiResponse
from rest_framework import permissions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from users.serializers import OTPRequestSerializer
from users.serializers import OTPVerifySerializer
from users.serializers import RegisterSerializer
from users.serializers import UserSerializer
from users.services.otpservice import OTPSendError
from users.services.otpservice import OTPService

User = get_user_model()


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
                OTPService.generate_otp(str(user.phone_number))
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

            if User.objects.filter(**lookup).exists():
                try:
                    OTPService.generate_otp(identifier, channel=channel)
                except OTPSendError:
                    return Response(
                        {"error": "Échec de l'envoi du code OTP, réessayez."},
                        status=502,
                    )
                return Response({"message": "OTP envoyé."})
            return Response({"error": "Utilisateur non trouvé."}, status=404)
        return Response(serializer.errors, status=400)


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

        user.courier_application_status = "pending"
        user.save(update_fields=["courier_application_status"])
        return Response(
            {"detail": "Candidature envoyée, en attente de validation."}, status=201
        )
