from rest_framework import serializers

from notifications.models import DeviceToken


class DeviceTokenRegisterSerializer(serializers.ModelSerializer):
    # Déclaré explicitement (sans passer par l'auto-génération de ModelSerializer) pour ne
    # PAS hériter du `UniqueValidator` que DRF poserait sinon sur ce champ `unique=True` :
    # register-device fait volontairement un upsert par token (un token existant, même
    # rattaché à un autre utilisateur, est réattribué — voir RegisterDeviceView), un rejet
    # "déjà pris" casserait justement ce cas d'usage.
    token = serializers.CharField(max_length=255)

    class Meta:
        model = DeviceToken
        fields = ["token", "platform", "device_id"]
        extra_kwargs = {"device_id": {"required": False}}


class DeviceTokenUnregisterSerializer(serializers.Serializer):
    token = serializers.CharField()
