from rest_framework import serializers

from jobs.models import JobApplication
from jobs.models import JobOffer
from jobs.models import TalentProfile


class TalentProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = TalentProfile
        fields = "__all__"
        read_only_fields = ["user"]


class JobOfferSerializer(serializers.ModelSerializer):
    recruiter_name = serializers.ReadOnlyField(source="recruiter.first_name")
    company_verified = serializers.ReadOnlyField(source="company.is_verified")
    is_favorited = serializers.SerializerMethodField()

    class Meta:
        model = JobOffer
        fields = "__all__"
        read_only_fields = ["recruiter", "is_active"]

    def get_is_favorited(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return obj.favorited_by.filter(user=user).exists()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Si un profil entreprise (vérifié ou non) est lié, son nom prime sur le texte libre.
        if instance.company_id:
            data["company_name"] = instance.company.name
        return data


class JobApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobApplication
        fields = ["id", "job", "cv_file", "message", "status", "applied_at"]
        read_only_fields = ["status", "applied_at"]

    def validate_job(self, value):
        if not value.is_active:
            raise serializers.ValidationError("Cette offre n'est plus active.")
        return value
