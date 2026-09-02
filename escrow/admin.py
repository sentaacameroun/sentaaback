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
        "provider",
        "channel",
        "status",
        "external_ref",
        "created_at",
    )
    # `provider` : fournisseur ayant réellement traité la transaction (architecture
    # multi-provider, voir escrow/services/providers/) — filtrable pour distinguer les
    # transactions NotchPay des transactions KPay, utile lors d'un changement de
    # PAYMENT_PROVIDER.
    list_filter = ("transaction_type", "status", "provider", "channel")
    readonly_fields = (
        "order",
        "amount",
        "transaction_type",
        "provider",
        "channel",
        "phone_number",
        "status",
        "is_success",
        "external_ref",
        "raw_response",
        "created_at",
    )
