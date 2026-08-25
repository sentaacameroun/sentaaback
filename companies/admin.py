from django.contrib import admin

from common.images.admin import image_preview_html
from companies.models import CompanyProfile


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "sector", "is_verified", "created_at")
    list_filter = ("is_verified", "sector")
    search_fields = ("name", "owner__phone_number", "rccm_number")
    readonly_fields = ("logo_preview",)

    @admin.display(description="Aperçu")
    def logo_preview(self, obj):
        return image_preview_html(obj.logo, height=80)
