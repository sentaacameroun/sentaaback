from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters
from rest_framework import mixins
from rest_framework import permissions
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from marketplace.models import Category
from marketplace.models import Listing
from marketplace.models import ListingFavorite
from marketplace.models import Offer
from marketplace.serializers import CategorySerializer
from marketplace.serializers import ListingSerializer
from marketplace.serializers import OfferRespondSerializer
from marketplace.serializers import OfferSerializer


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.AllowAny]


class ListingViewSet(viewsets.ModelViewSet):
    queryset = (
        Listing.objects.filter(status="active")
        .select_related("seller", "category", "company")
        .prefetch_related("images")
    )
    serializer_class = ListingSerializer
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["category", "city", "is_promoted"]
    search_fields = ["title", "description"]
    ordering_fields = ["price", "created_at"]

    def perform_create(self, serializer):
        serializer.save(seller=self.request.user)
        # Rôle dérivé du comportement réel plutôt qu'un flag qu'aucun endpoint ne permettait
        # jusqu'ici de positionner soi-même (voir users/views.py pour is_recruiter/is_courier).
        if not self.request.user.is_seller:
            self.request.user.is_seller = True
            self.request.user.save(update_fields=["is_seller"])

    @extend_schema(summary="Liste des annonces actives avec filtres")
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_permissions(self):
        if self.action in ("toggle_favorite", "favorites"):
            return [permissions.IsAuthenticated()]
        return super().get_permissions()

    @action(detail=True, methods=["post"])
    def toggle_favorite(self, request, pk=None):
        listing = self.get_object()
        favorite, created = ListingFavorite.objects.get_or_create(
            user=request.user, listing=listing
        )
        if not created:
            favorite.delete()
            return Response({"favorited": False})
        return Response({"favorited": True})

    @action(detail=False)
    def favorites(self, request):
        queryset = (
            Listing.objects.filter(favorited_by__user=request.user)
            .select_related("seller", "category", "company")
            .prefetch_related("images")
            .distinct()
        )
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(
            page if page is not None else queryset, many=True
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


class OfferViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Négociation structurée sur une annonce. status='pending' : le buyer attend une réponse
    du vendeur. status='countered' : le vendeur a contre-proposé, le buyer doit répondre.
    """

    serializer_class = OfferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return Offer.objects.filter(
            models.Q(buyer=user) | models.Q(listing__seller=user)
        ).select_related("listing", "buyer", "last_offered_by")

    def perform_create(self, serializer):
        serializer.save(
            buyer=self.request.user, last_offered_by=self.request.user, status="pending"
        )

    def _respond(self, request, pk, new_status, require_price=False):
        offer = self.get_object()
        user = request.user

        if offer.status in ("accepted", "rejected"):
            return Response(
                {"error": "Cette négociation est déjà terminée."}, status=400
            )

        # Seule la partie qui N'A PAS proposé le prix courant peut répondre.
        if user == offer.last_offered_by:
            return Response(
                {"error": "En attente de la réponse de l'autre partie."}, status=403
            )
        if user != offer.buyer and user != offer.listing.seller:
            return Response({"error": "Action interdite"}, status=403)

        serializer = OfferRespondSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if require_price:
            offer.proposed_price = serializer.validated_data["proposed_price"]
            offer.last_offered_by = user

        offer.status = new_status
        offer.save()
        return Response(OfferSerializer(offer).data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._respond(request, pk, new_status="accepted")

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._respond(request, pk, new_status="rejected")

    @action(detail=True, methods=["post"])
    def counter(self, request, pk=None):
        return self._respond(request, pk, new_status="countered", require_price=True)
