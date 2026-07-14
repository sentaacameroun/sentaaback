from rest_framework import permissions
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from jobs.models import JobApplication
from jobs.models import JobOffer
from jobs.models import JobOfferFavorite
from jobs.permissions import IsRecruiter
from jobs.serializers import JobApplicationSerializer
from jobs.serializers import JobOfferSerializer


class JobOfferViewSet(viewsets.ModelViewSet):
    queryset = JobOffer.objects.filter(is_active=True)
    serializer_class = JobOfferSerializer

    def get_permissions(self):
        if self.action in ("toggle_favorite", "favorites"):
            return [permissions.IsAuthenticated()]
        if self.action in ["create", "update", "partial_update", "destroy"]:
            return [IsRecruiter()]
        return [permissions.AllowAny()]

    def perform_create(self, serializer):
        serializer.save(recruiter=self.request.user)

    @action(detail=True, methods=["post"])
    def toggle_favorite(self, request, pk=None):
        job = self.get_object()
        favorite, created = JobOfferFavorite.objects.get_or_create(
            user=request.user, job=job
        )
        if not created:
            favorite.delete()
            return Response({"favorited": False})
        return Response({"favorited": True})

    @action(detail=False)
    def favorites(self, request):
        queryset = JobOffer.objects.filter(favorited_by__user=request.user).distinct()
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(
            page if page is not None else queryset, many=True
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


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
