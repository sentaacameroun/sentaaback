from django.conf import settings
from django.contrib import admin
from django.urls import include
from django.urls import path
from drf_spectacular.views import SpectacularAPIView
from drf_spectacular.views import SpectacularSwaggerView

urlpatterns = [
    path("administration/", admin.site.urls),
    path("api/", include("users.urls")),
    path("api/", include("marketplace.urls")),
    path("api/", include("jobs.urls")),
    path("api/", include("escrow.urls")),
    path("api/", include("logistics.urls")),
    path("api/", include("chat.urls")),
    path("api/", include("companies.urls")),
    path("api/", include("notifications.urls")),
]

if settings.DEBUG:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path(
            "api/docs/",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger-ui",
        ),
    ]
