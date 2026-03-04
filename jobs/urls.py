from django.urls import path, include
from rest_framework.routers import DefaultRouter
from jobs.views import JobOfferViewSet, JobApplicationViewSet

router = DefaultRouter()
router.register(r'job-offers', JobOfferViewSet, basename='job-offer')
router.register(r'job-applications', JobApplicationViewSet, basename='job-application')
urlpatterns = [
    path('', include(router.urls)),
]