from django.urls import path, include
from rest_framework.routers import DefaultRouter
from marketplace.views import CategoryViewSet, ListingViewSet

router = DefaultRouter()
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'listings', ListingViewSet, basename='listing')

urlpatterns = [
    path('', include(router.urls)),
]