from django.urls import include
from django.urls import path
from rest_framework.routers import DefaultRouter

from chat.views import ConversationViewSet

router = DefaultRouter()
router.register(r"conversations", ConversationViewSet, basename="conversation")

urlpatterns = [
    path("chat/", include(router.urls)),
]
