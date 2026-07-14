from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from users.models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = (
        "phone_number",
        "first_name",
        "last_name",
        "email",
        "is_seller",
        "is_recruiter",
        "is_courier",
        "courier_application_status",
        "is_staff",
        "is_active",
    )

    list_filter = (
        "is_seller",
        "is_recruiter",
        "is_courier",
        "courier_application_status",
        "is_staff",
        "is_active",
        "newsletter_opt_in",
    )

    search_fields = ("phone_number", "first_name", "last_name", "email")
    readonly_fields = ("date_joined", "last_login")
    fieldsets = (
        (None, {"fields": ("phone_number", "password")}),
        (
            "Infos Personnelles",
            {"fields": ("first_name", "last_name", "email", "newsletter_opt_in")},
        ),
        (
            "Rôles & Permissions",
            {
                "fields": (
                    "is_seller",
                    "is_recruiter",
                    "is_courier",
                    "courier_application_status",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                ),
            },
        ),
        ("Dates importantes", {"fields": ("last_login", "date_joined")}),
    )

    ordering = ("-date_joined",)
    actions = ["approve_courier_applications", "reject_courier_applications"]

    @admin.action(description="Approuver comme coursier (is_courier=True)")
    def approve_courier_applications(self, request, queryset):
        updated = queryset.update(
            is_courier=True, courier_application_status="approved"
        )
        self.message_user(
            request, f"{updated} utilisateur(s) approuvé(s) comme coursier."
        )

    @admin.action(description="Refuser la candidature coursier")
    def reject_courier_applications(self, request, queryset):
        updated = queryset.exclude(is_courier=True).update(
            courier_application_status="rejected"
        )
        self.message_user(request, f"{updated} candidature(s) refusée(s).")
