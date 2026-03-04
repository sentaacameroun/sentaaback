from rest_framework import serializers
from jobs.models import TalentProfile, JobOffer, JobApplication

class TalentProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = TalentProfile
        fields = '__all__'
        read_only_fields = ['user']

class JobOfferSerializer(serializers.ModelSerializer):
    recruiter_name = serializers.ReadOnlyField(source='recruiter.first_name')

    class Meta:
        model = JobOffer
        fields = '__all__'
        read_only_fields = ['recruiter', 'is_active']

class JobApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobApplication
        fields = ['id', 'job', 'cv_file', 'message', 'status', 'applied_at']
        read_only_fields = ['status', 'applied_at']

    def validate_job(self, value):
        if not value.is_active:
            raise serializers.ValidationError("Cette offre n'est plus active.")
        return value