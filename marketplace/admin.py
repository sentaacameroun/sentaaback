from django.contrib import admin

from .models import Category
from .models import Listing
from .models import ListingFavorite
from .models import ListingImage
from .models import Offer


class ListingImageInline(admin.TabularInline):
    model = ListingImage
    extra = 1


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "seller",
        "company",
        "category",
        "price",
        "city",
        "status",
        "is_promoted",
        "created_at",
    )
    list_filter = ("status", "city", "category", "is_promoted")
    search_fields = ("title", "description", "seller__phone_number")
    list_editable = ("status", "is_promoted")
    inlines = [ListingImageInline]

    def view_on_site(self, obj):
        return f"/marketplace/listings/{obj.id}/"


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = (
        "listing",
        "buyer",
        "proposed_price",
        "status",
        "last_offered_by",
        "updated_at",
    )
    list_filter = ("status",)
    readonly_fields = (
        "listing",
        "buyer",
        "proposed_price",
        "last_offered_by",
        "created_at",
    )


@admin.register(ListingFavorite)
class ListingFavoriteAdmin(admin.ModelAdmin):
    list_display = ("user", "listing", "created_at")
    readonly_fields = ("user", "listing", "created_at")
