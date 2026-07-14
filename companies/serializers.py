from rest_framework import serializers

from companies.models import CompanyProfile


class CompanyProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyProfile
        fields = [
            "id",
            "owner",
            "name",
            "description",
            "logo",
            "sector",
            "website",
            "rccm_number",
            "is_verified",
            "created_at",
        ]
        read_only_fields = ["id", "owner", "is_verified", "created_at"]


class CompanyProfilePublicSerializer(serializers.ModelSerializer):
    listings = serializers.SerializerMethodField()
    job_offers = serializers.SerializerMethodField()

    class Meta:
        model = CompanyProfile
        fields = [
            "id",
            "name",
            "description",
            "logo",
            "sector",
            "website",
            "is_verified",
            "listings",
            "job_offers",
        ]

    def get_listings(self, obj):
        from marketplace.serializers import ListingSerializer

        return ListingSerializer(obj.listings.filter(status="active"), many=True).data

    def get_job_offers(self, obj):
        from jobs.serializers import JobOfferSerializer

        return JobOfferSerializer(obj.job_offers.filter(is_active=True), many=True).data
