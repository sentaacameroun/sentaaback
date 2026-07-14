from django.urls import include
from django.urls import path
from rest_framework.routers import DefaultRouter

from logistics.views import DeliveryViewSet

router = DefaultRouter()
router.register(r"deliveries", DeliveryViewSet, basename="delivery")

urlpatterns = [
    path("", include(router.urls)),
]
