from django.contrib import admin
from .models import Order, PaymentTransaction
from django.utils.html import format_html

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('buyer', 'listing', 'total_amount', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('buyer__phone_number', 'listing__title')
    
    readonly_fields = ('item_price', 'service_fee', 'total_amount', 'buyer', 'listing', 'created_at')
    
    def get_status_display(self, obj):
        colors = {
            'pending': 'orange',
            'paid_escrow': 'blue',
            'completed': 'green',
            'disputed': 'red',
        }
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            colors.get(obj.status, 'black'),
            obj.get_status_display()
        )

@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ('order', 'amount', 'method', 'is_success', 'external_ref', 'created_at')
    readonly_fields = ('order', 'amount', 'method', 'is_success', 'external_ref', 'raw_response', 'created_at')