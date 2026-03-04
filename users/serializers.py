from rest_framework import serializers
from django.contrib.auth import get_user_model
from phonenumber_field.serializerfields import PhoneNumberField

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        # on expose les prénoms et noms séparément, pas de champ "name" dans le modèle
        fields = ['id', 'phone_number', 'first_name', 'last_name',
                  'is_seller', 'is_recruiter', 'is_courier']
        read_only_fields = ['id']

class RegisterSerializer(serializers.ModelSerializer):
    """Serializer pour la création initiale du compte"""
    phone_number = PhoneNumberField()

    class Meta:
        model = User
        fields = ['phone_number', 'first_name', 'last_name', 'password']
        extra_kwargs = {'password': {'write_only': True}}

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)

class OTPRequestSerializer(serializers.Serializer):
    """Valide juste le numéro de téléphone pour l'envoi de l'OTP"""
    phone_number = PhoneNumberField()

class OTPVerifySerializer(serializers.Serializer):
    """Valide le couple téléphone/code pour la connexion"""
    phone_number = PhoneNumberField()
    otp_code = serializers.CharField(max_length=6, min_length=6)

