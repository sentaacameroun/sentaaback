from django.urls import include
from django.urls import path
from rest_framework.routers import DefaultRouter

from escrow.views import KPayWebhookView
from escrow.views import MobileMoneyWebhookView
from escrow.views import OrderViewSet

router = DefaultRouter()
router.register(r"orders", OrderViewSet, basename="order")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "mobile-money-webhook/",
        MobileMoneyWebhookView.as_view(),
        name="mobile-money-webhook",
    ),
    path(
        "kpay-webhook/",
        KPayWebhookView.as_view(),
        name="kpay-webhook",
    ),
]
