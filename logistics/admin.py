from django.contrib import admin

from logistics.models import Delivery


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("tracking_number", "order", "courier", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("tracking_number", "order__id")
