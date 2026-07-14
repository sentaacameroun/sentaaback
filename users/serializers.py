from django.contrib.auth import get_user_model
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    # Déclaré explicitement (allow_blank=False, contrairement au défaut DRF pour un champ
    # blank=True) : un email doit être omis/null ou valide, jamais une chaîne vide — sinon
    # deux comptes sans email entrent en conflit sous la contrainte unique du modèle. Un champ
    # déclaré manuellement n'hérite pas automatiquement du UniqueValidator que DRF ajoute aux
    # champs auto-générés depuis un modèle : on le rajoute donc explicitement (skip
    # automatiquement pour value=None, donc inoffensif pour les comptes sans email — et exclut
    # l'instance courante lors d'un PATCH grâce à serializer_field.parent.instance).
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
            "location_updated_at",
        ]


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
    """
    Connexion par téléphone (SMS) ou email — l'un des deux, pas les deux. L'email n'est
    utilisable que s'il a été renseigné sur le compte (voir /api/me/ ou l'inscription).
    """

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
