from django.urls import include
from django.urls import path
from rest_framework.routers import DefaultRouter

from marketplace.views import CategoryViewSet
from marketplace.views import ListingViewSet
from marketplace.views import OfferViewSet

router = DefaultRouter()
router.register(r"categories", CategoryViewSet, basename="category")
router.register(r"listings", ListingViewSet, basename="listing")
router.register(r"offers", OfferViewSet, basename="offer")

urlpatterns = [
    path("", include(router.urls)),
]
