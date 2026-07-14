from django.contrib import admin

from companies.models import CompanyProfile


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "sector", "is_verified", "created_at")
    list_filter = ("is_verified", "sector")
    search_fields = ("name", "owner__phone_number", "rccm_number")
