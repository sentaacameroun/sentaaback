from django.urls import re_path

from logistics.consumers import CourierDispatchConsumer
from logistics.consumers import DeliveryTrackingConsumer

websocket_urlpatterns = [
    re_path(
        r"^ws/deliveries/(?P<delivery_id>\d+)/$", DeliveryTrackingConsumer.as_asgi()
    ),
    re_path(r"^ws/courier/dispatch/$", CourierDispatchConsumer.as_asgi()),
]
