from django.contrib import admin

from notifications.models import DeviceToken


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "platform",
        "active",
        "device_id",
        "created_at",
        "updated_at",
    )
    list_filter = ("platform", "active")
    search_fields = ("user__phone_number", "user__email", "token", "device_id")
    readonly_fields = ("token", "created_at", "updated_at")
