from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework_simplejwt.tokens import RefreshToken
from users.serializers import RegisterSerializer, OTPRequestSerializer, OTPVerifySerializer, UserSerializer
from users.services.otpservice import OTPService
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema, OpenApiResponse

User = get_user_model()

class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    @extend_schema(
        summary="Inscription d'un nouvel utilisateur",
        request=RegisterSerializer,
        responses={201: UserSerializer, 400: OpenApiResponse(description="Erreur de validation")},
        tags=['Authentification']
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            # On envoie un OTP dès l'inscription pour valider le compte
            OTPService.generate_otp(str(user.phone_number))
            return Response({
                "message": "Utilisateur créé. Code OTP envoyé.",
                "user": UserSerializer(user).data
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class LoginOTPRequestView(APIView):
    """Etape 1: Demander un code OTP"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = OTPRequestSerializer(data=request.data)
        if serializer.is_valid():
            phone = str(serializer.validated_data['phone_number'])
            if User.objects.filter(phone_number=phone).exists():
                OTPService.generate_otp(phone)
                return Response({"message": "OTP envoyé."})
            return Response({"error": "Utilisateur non trouvé."}, status=404)
        return Response(serializer.errors, status=400)

class LoginOTPVerifyView(APIView):
    """Etape 2: Vérifier l'OTP et obtenir le JWT"""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        if serializer.is_valid():
            phone = str(serializer.validated_data['phone_number'])
            code = serializer.validated_data['otp_code']
            
            if OTPService.verify_otp(phone, code):
                user = User.objects.get(phone_number=phone)
                refresh = RefreshToken.for_user(user)
                return Response({
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                    "user": UserSerializer(user).data
                })
            return Response({"error": "Code invalide ou expiré."}, status=400)
        return Response(serializer.errors, status=400)