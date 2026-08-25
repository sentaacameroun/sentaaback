from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from rest_framework import permissions
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from jobs.models import JobApplication
from jobs.models import JobOffer
from jobs.models import JobOfferFavorite
from jobs.permissions import IsApplicationRecruiter
from jobs.permissions import IsOwnerRecruiter
from jobs.permissions import IsVerifiedRecruiter
from jobs.serializers import JobApplicationSerializer
from jobs.serializers import JobOfferSerializer


class JobOfferViewSet(viewsets.ModelViewSet):
    serializer_class = JobOfferSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["recruiter"]
    search_fields = ["title", "company_name", "description"]

    def get_queryset(self):
        # Un recruteur consultant ses PROPRES offres (Espace Entreprise > Mes offres,
        # `?recruiter=<son_id>`) doit voir aussi celles qu'il a désactivées, pas seulement
        # les actives visibles du grand public.
        user = self.request.user
        recruiter_param = self.request.query_params.get("recruiter")
        if user.is_authenticated and recruiter_param == str(user.id):
            return JobOffer.objects.all()
        return JobOffer.objects.filter(is_active=True)

    def get_permissions(self):
        if self.action in ("toggle_favorite", "favorites"):
            return [permissions.IsAuthenticated()]
        if self.action == "create":
            return [IsVerifiedRecruiter()]
        if self.action in ("update", "partial_update", "destroy"):
            return [IsOwnerRecruiter()]
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

    def get_permissions(self):
        if self.action in ("accept", "reject", "mark_reviewed"):
            return [permissions.IsAuthenticated(), IsApplicationRecruiter()]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save(applicant=self.request.user)

    # `JobApplicationSerializer.status` est en lecture seule pour tout le monde (y compris
    # le recruteur) — jusqu'ici aucun endpoint ne permettait à un recruteur de répondre à une
    # candidature reçue. Même pattern que `marketplace.OfferViewSet` (accept/reject).
    def _respond(self, request, pk, new_status):
        application = self.get_object()
        application.status = new_status
        application.save(update_fields=["status"])
        return Response(JobApplicationSerializer(application).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._respond(request, pk, "accepted")

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._respond(request, pk, "rejected")

    @action(detail=True, methods=["post"], url_path="mark_reviewed")
    def mark_reviewed(self, request, pk=None):
        return self._respond(request, pk, "reviewed")
