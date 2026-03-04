from django.urls import path
from .views import RegisterView, LoginOTPRequestView, LoginOTPVerifyView
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('otp-request/', LoginOTPRequestView.as_view(), name='otp-request'),
    path('otp-verify/', LoginOTPVerifyView.as_view(), name='otp-verify'),
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
    *static(settings.STATIC_URL, document_root=settings.STATIC_ROOT),
]