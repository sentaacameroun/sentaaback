from rest_framework import serializers
from marketplace.models import Category, Listing, ListingImage

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'

class ListingImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ListingImage
        fields = ['id', 'image', 'is_main']

class ListingSerializer(serializers.ModelSerializer):
    images = ListingImageSerializer(many=True, read_only=True)
    seller_name = serializers.ReadOnlyField(source='seller.first_name')
    category_name = serializers.ReadOnlyField(source='category.name')

    class Meta:
        model = Listing
        fields = [
            'id', 'title', 'description', 'price', 'city', 
            'category', 'category_name', 'seller', 'seller_name', 
            'status', 'images', 'created_at'
        ]
        read_only_fields = ['seller', 'status']

    def validate_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("Le prix doit être supérieur à zéro.")
        return value