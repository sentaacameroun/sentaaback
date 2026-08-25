from rest_framework import serializers

from common.images.fields import CloudinaryImageField
from escrow.models import Order
from marketplace.models import Category
from marketplace.models import Listing
from marketplace.models import ListingImage
from marketplace.models import Offer


class CategorySerializer(serializers.ModelSerializer):
    # Champ modèle `CloudinaryField` (voir marketplace/models.py) : `ModelSerializer` ne sait
    # pas le mapper automatiquement, d'où la déclaration explicite. Lecture seule : l'icône
    # n'est éditable que via l'admin (`CategoryViewSet` est en lecture seule, voir
    # marketplace/views.py), donc pas besoin de valider un upload ici.
    icon = CloudinaryImageField(read_only=True, variant="thumbnail")

    class Meta:
        model = Category
        fields = "__all__"


class ListingImageSerializer(serializers.ModelSerializer):
    # Idem : upload géré explicitement dans `ListingViewSet._attach_uploaded_images` (voir
    # marketplace/views.py), pas via ce serializer — lecture seule ici.
    image = CloudinaryImageField(read_only=True, variant="card")

    class Meta:
        model = ListingImage
        fields = ["id", "image", "is_main"]


class ListingSerializer(serializers.ModelSerializer):
    images = ListingImageSerializer(many=True, read_only=True)
    seller_name = serializers.ReadOnlyField(source="seller.first_name")
    category_name = serializers.ReadOnlyField(source="category.name")
    company_name = serializers.ReadOnlyField(source="company.name")
    company_verified = serializers.ReadOnlyField(source="company.is_verified")
    is_favorited = serializers.SerializerMethodField()
    # Signal de confiance pour un vendeur PARTICULIER (les entreprises ont déjà
    # `company_verified`, mais rien n'existe côté `users.User` pour un particulier — pas de
    # KYC/vérification réelle). Plutôt qu'un faux badge "vérifié", on expose un compteur réel :
    # le nombre de commandes déjà menées à terme par ce vendeur, tous listings confondus.
    seller_completed_sales_count = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = [
            "id",
            "title",
            "description",
            "price",
            "city",
            "category",
            "category_name",
            "seller",
            "seller_name",
            "seller_completed_sales_count",
            "company",
            "company_name",
            "company_verified",
            "status",
            "is_promoted",
            "images",
            "is_favorited",
            "created_at",
        ]
        read_only_fields = ["seller", "status", "is_promoted"]

    def validate_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("Le prix doit être supérieur à zéro.")
        return value

    def get_is_favorited(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return obj.favorited_by.filter(user=user).exists()

    def get_seller_completed_sales_count(self, obj):
        return Order.objects.filter(
            listing__seller_id=obj.seller_id, status="completed"
        ).count()


class OfferSerializer(serializers.ModelSerializer):
    listing_title = serializers.ReadOnlyField(source="listing.title")
    seller = serializers.ReadOnlyField(source="listing.seller_id")
    seller_name = serializers.ReadOnlyField(source="listing.seller.first_name")
    buyer_name = serializers.ReadOnlyField(source="buyer.first_name")

    class Meta:
        model = Offer
        fields = [
            "id",
            "listing",
            "listing_title",
            "buyer",
            "buyer_name",
            "seller",
            "seller_name",
            "proposed_price",
            "status",
            "last_offered_by",
            "message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "buyer",
            "status",
            "last_offered_by",
            "created_at",
            "updated_at",
        ]

    def validate_listing(self, value):
        if value.status != "active":
            raise serializers.ValidationError("Cette annonce n'est plus disponible.")
        return value

    def validate(self, data):
        buyer = self.context["request"].user
        listing = data.get("listing") or (
            self.instance.listing if self.instance else None
        )
        if listing and listing.seller == buyer:
            raise serializers.ValidationError(
                "Vous ne pouvez pas négocier votre propre annonce."
            )
        return data


class OfferRespondSerializer(serializers.Serializer):
    proposed_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False
    )
