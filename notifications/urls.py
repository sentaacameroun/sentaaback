from django.urls import path

from notifications.views import RegisterDeviceView
from notifications.views import UnregisterDeviceView
from notifications.views import UnsubscribeNewsletterView

urlpatterns = [
    path(
        "notifications/register-device/",
        RegisterDeviceView.as_view(),
        name="register-device",
    ),
    path(
        "notifications/unregister-device/",
        UnregisterDeviceView.as_view(),
        name="unregister-device",
    ),
    path(
        "notifications/unsubscribe-newsletter/",
        UnsubscribeNewsletterView.as_view(),
        name="unsubscribe-newsletter",
    ),
]
