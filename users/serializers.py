from django.contrib.auth import get_user_model
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from common.images.fields import CloudinaryImageField

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    courier_id_document = CloudinaryImageField(
        read_only=True, variant="full", signed=True
    )
    email = serializers.EmailField(
        required=False,
        allow_null=True,
        allow_blank=False,
        validators=[
            UniqueValidator(
                queryset=User.objects.all(), message="Cet email est déjà utilisé."
            )
        ],
    )

    class Meta:
        model = User

        fields = [
            "id",
            "phone_number",
            "first_name",
            "last_name",
            "email",
            "newsletter_opt_in",
            "is_seller",
            "is_recruiter",
            "is_courier",
            "courier_application_status",
            "courier_vehicle_type",
            "courier_id_document",
            "is_available",
            "latitude",
            "longitude",
            "location_updated_at",
        ]

        read_only_fields = [
            "id",
            "phone_number",
            "is_seller",
            "is_recruiter",
            "is_courier",
            "courier_application_status",
            "courier_vehicle_type",
            "location_updated_at",
        ]

    def validate_is_available(self, value):
        # Seul un coursier peut se déclarer disponible (audit MINEUR #16).
        # `is_courier` étant read-only, on se fie à l'état persisté de l'instance.
        if value and not (self.instance and self.instance.is_courier):
            raise serializers.ValidationError(
                "Seuls les coursiers peuvent se déclarer disponibles."
            )
        return value


class RegisterSerializer(serializers.ModelSerializer):
    phone_number = PhoneNumberField()
    email = serializers.EmailField(
        required=False,
        allow_null=True,
        validators=[
            UniqueValidator(
                queryset=User.objects.all(), message="Cet email est déjà utilisé."
            )
        ],
    )

    class Meta:
        model = User
        fields = ["phone_number", "first_name", "last_name", "email", "password"]
        extra_kwargs = {"password": {"write_only": True}}

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class OTPRequestSerializer(serializers.Serializer):
    phone_number = PhoneNumberField(required=False)
    email = serializers.EmailField(required=False)

    def validate(self, data):
        phone = data.get("phone_number")
        email = data.get("email")
        if not phone and not email:
            raise serializers.ValidationError("Fournir phone_number ou email.")
        if phone and email:
            raise serializers.ValidationError(
                "Fournir phone_number OU email, pas les deux."
            )
        return data


class OTPVerifySerializer(serializers.Serializer):
    phone_number = PhoneNumberField(required=False)
    email = serializers.EmailField(required=False)
    otp_code = serializers.CharField(max_length=6, min_length=6)

    def validate(self, data):
        phone = data.get("phone_number")
        email = data.get("email")
        if not phone and not email:
            raise serializers.ValidationError("Fournir phone_number ou email.")
        if phone and email:
            raise serializers.ValidationError(
                "Fournir phone_number OU email, pas les deux."
            )
        return data
