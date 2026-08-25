from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import ApplyCourierView
from .views import LoginOTPRequestView
from .views import LoginOTPVerifyView
from .views import LogoutView
from .views import MeView
from .views import RegisterView

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("otp-request/", LoginOTPRequestView.as_view(), name="otp-request"),
    path("otp-verify/", LoginOTPVerifyView.as_view(), name="otp-verify"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("me/apply-courier/", ApplyCourierView.as_view(), name="apply-courier"),
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
    *static(settings.STATIC_URL, document_root=settings.STATIC_ROOT),
]
