from rest_framework import serializers

from marketplace.models import Category
from marketplace.models import Listing
from marketplace.models import ListingImage
from marketplace.models import Offer


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = "__all__"


class ListingImageSerializer(serializers.ModelSerializer):
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
            "company",
            "company_name",
            "company_verified",
            "status",
            "images",
            "is_favorited",
            "created_at",
        ]
        read_only_fields = ["seller", "status"]

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


class OfferSerializer(serializers.ModelSerializer):
    class Meta:
        model = Offer
        fields = [
            "id",
            "listing",
            "buyer",
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
