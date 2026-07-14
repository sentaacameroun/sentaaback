from django.contrib import admin
from django.utils.html import format_html

from .models import Order
from .models import PaymentTransaction


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("buyer", "listing", "total_amount", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("buyer__phone_number", "listing__title")

    readonly_fields = (
        "item_price",
        "service_fee",
        "total_amount",
        "buyer",
        "listing",
        "created_at",
    )

    def get_status_display(self, obj):
        colors = {
            "pending": "orange",
            "paid_escrow": "blue",
            "completed": "green",
            "disputed": "red",
        }
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            colors.get(obj.status, "black"),
            obj.get_status_display(),
        )


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "amount",
        "transaction_type",
        "channel",
        "status",
        "external_ref",
        "created_at",
    )
    list_filter = ("transaction_type", "status", "channel")
    readonly_fields = (
        "order",
        "amount",
        "transaction_type",
        "channel",
        "phone_number",
        "status",
        "is_success",
        "external_ref",
        "raw_response",
        "created_at",
    )
