from django.urls import include
from django.urls import path
from rest_framework.routers import DefaultRouter

from jobs.views import JobApplicationViewSet
from jobs.views import JobOfferViewSet

router = DefaultRouter()
router.register(r"job-offers", JobOfferViewSet, basename="job-offer")
router.register(r"job-applications", JobApplicationViewSet, basename="job-application")
urlpatterns = [
    path("", include(router.urls)),
]
