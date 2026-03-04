from django.urls import path, include
from rest_framework.routers import DefaultRouter
from escrow.views import OrderViewSet, MobileMoneyWebhookView
router = DefaultRouter()
router.register(r'orders', OrderViewSet, basename='order')
router.register(r'mobile-money-webhook', MobileMoneyWebhookView, basename='mobile-money-webhook')

urlpatterns = [
    path('', include(router.urls)),
]