import logging

import cloudinary.uploader
from cloudinary.exceptions import Error as CloudinaryError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import exceptions
from rest_framework import filters
from rest_framework import mixins
from rest_framework import permissions
from rest_framework import serializers
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from common.images.validators import ImageFileValidator
from marketplace.models import Category
from marketplace.models import Listing
from marketplace.models import ListingFavorite
from marketplace.models import ListingImage
from marketplace.models import Offer
from marketplace.permissions import IsOwnerSeller
from marketplace.serializers import CategorySerializer
from marketplace.serializers import ListingSerializer
from marketplace.serializers import OfferRespondSerializer
from marketplace.serializers import OfferSerializer

logger = logging.getLogger(__name__)

# Plafond par annonce : sans lui, un vendeur pourrait faire grossir indéfiniment le stockage
# Cloudinary (plan gratuit à 25 crédits/mois, stockage+bande passante+transformations
# confondus — voir common/images/validators.py) sur une seule annonce.
MAX_IMAGES_PER_LISTING = 8

# Même plafond de poids que le champ modèle ListingImage.image (voir marketplace/models.py) :
# revalidé ici car un serializer/vue DRF ne déclenche pas `Model.full_clean()`, qui est le
# seul moment où le validateur posé sur le champ modèle s'exécuterait autrement.
_validate_listing_image = ImageFileValidator(max_bytes=5 * 1024 * 1024)


class ImageUploadUnavailable(exceptions.APIException):
    """Échec d'infrastructure Cloudinary (réseau, 5xx) — distinct d'un fichier invalide (400)."""

    status_code = 502
    default_detail = (
        "Service de stockage d'images momentanément indisponible, réessayez."
    )
    default_code = "image_upload_unavailable"


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
    filterset_fields = ["category", "city", "is_promoted", "seller"]
    search_fields = ["title", "description"]
    ordering_fields = ["price", "created_at"]

    def perform_create(self, serializer):
        # Atomique : si l'upload des images échoue après coup (validation ou panne
        # Cloudinary), l'annonce elle-même ne doit pas rester créée sans image derrière une
        # réponse d'erreur — voir _attach_uploaded_images.
        with transaction.atomic():
            listing = serializer.save(seller=self.request.user)
            # Rôle dérivé du comportement réel plutôt qu'un flag qu'aucun endpoint ne
            # permettait jusqu'ici de positionner soi-même (voir users/views.py pour
            # is_recruiter/is_courier).
            if not self.request.user.is_seller:
                self.request.user.is_seller = True
                self.request.user.save(update_fields=["is_seller"])
            self._attach_uploaded_images(listing)

    def perform_update(self, serializer):
        with transaction.atomic():
            listing = serializer.save()
            self._attach_uploaded_images(listing)

    def _attach_uploaded_images(self, listing):
        # `images` est en lecture seule sur ListingSerializer (relation inverse imbriquée) :
        # le client envoie les fichiers sous la même clé `images` en multipart, on les
        # rattache ici plutôt que via le serializer. Sur update, les fichiers envoyés
        # s'ajoutent aux images existantes (pas de remplacement/suppression).
        uploaded_files = self.request.FILES.getlist("images")
        if not uploaded_files:
            return

        existing_count = listing.images.count()
        if existing_count + len(uploaded_files) > MAX_IMAGES_PER_LISTING:
            raise serializers.ValidationError(
                {
                    "images": [
                        "Maximum %d images par annonce (%d déjà présentes)."
                        % (MAX_IMAGES_PER_LISTING, existing_count)
                    ]
                }
            )

        # Validation d'abord, upload ensuite : un fichier invalide ne doit jamais déclencher
        # d'appel réseau à Cloudinary (voir common/images/validators.py).
        for file in uploaded_files:
            try:
                _validate_listing_image(file)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"images": exc.messages}) from exc

        has_main_already = listing.images.filter(is_main=True).exists()
        # Pas de bulk_create ici : c'est `ListingImage.save()` (donc `CloudinaryField.pre_save`,
        # voir le champ `image` dans marketplace/models.py) qui déclenche l'upload réel vers
        # Cloudinary — bulk_create n'appelle jamais save() par ligne et laisserait le fichier
        # brut non uploadé.
        uploaded_public_ids = []
        try:
            for index, file in enumerate(uploaded_files):
                image = ListingImage(
                    listing=listing,
                    image=file,
                    is_main=(index == 0 and not has_main_already),
                )
                image.save()
                uploaded_public_ids.append(image.image.public_id)
        except CloudinaryError:
            logger.exception(
                "Échec upload Cloudinary pour listing %s ; nettoyage des images déjà envoyées"
                " dans ce lot",
                listing.id,
            )
            for public_id in uploaded_public_ids:
                try:
                    cloudinary.uploader.destroy(
                        public_id, resource_type="image", type="upload"
                    )
                except CloudinaryError:
                    logger.exception(
                        "Échec du nettoyage Cloudinary orphelin (public_id=%s)",
                        public_id,
                    )
            raise ImageUploadUnavailable() from None

    @extend_schema(summary="Liste des annonces actives avec filtres")
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_permissions(self):
        if self.action in ("toggle_favorite", "favorites"):
            return [permissions.IsAuthenticated()]
        # Seul le vendeur propriétaire peut modifier/supprimer son annonce : le défaut
        # global `IsAuthenticated` ne comparait jamais `obj.seller` à l'utilisateur (IDOR).
        # La visibilité en lecture (list/retrieve) et la création restent au défaut global.
        if self.action in ("update", "partial_update", "destroy"):
            return [permissions.IsAuthenticated(), IsOwnerSeller()]
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
