from rest_framework import viewsets, permissions
from drf_spectacular.utils import extend_schema
from jobs.models import JobOffer, JobApplication, TalentProfile
from jobs.serializers import JobOfferSerializer, JobApplicationSerializer, TalentProfileSerializer
from jobs.permissions import IsRecruiter

class JobOfferViewSet(viewsets.ModelViewSet):
    queryset = JobOffer.objects.filter(is_active=True)
    serializer_class = JobOfferSerializer

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsRecruiter()]
        return [permissions.AllowAny()]

    def perform_create(self, serializer):
        serializer.save(recruiter=self.request.user)

class JobApplicationViewSet(viewsets.ModelViewSet):
    serializer_class = JobApplicationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Un talent voit ses candidatures, un recruteur voit les candidatures à SES offres
        user = self.request.user
        if user.is_recruiter:
            return JobApplication.objects.filter(job__recruiter=user)
        return JobApplication.objects.filter(applicant=user)

    def perform_create(self, serializer):
        serializer.save(applicant=self.request.user)