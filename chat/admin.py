from django.contrib import admin

from chat.models import Conversation
from chat.models import Message


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "listing", "order", "created_at")
    filter_horizontal = ("participants",)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "sender", "body", "is_read", "created_at")
    list_filter = ("is_read",)
    readonly_fields = ("conversation", "sender", "body", "created_at")
