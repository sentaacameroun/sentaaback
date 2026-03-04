from django.contrib import admin
from django.urls import path, include
from django.views import defaults as default_views
from drf_spectacular.views import SpectacularAPIView
from drf_spectacular.views import SpectacularSwaggerView
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions

schema_view = get_schema_view(
    openapi.Info(
        title="SENTAA API Documentation",
        default_version="v1",
        description="API pour la gestion du marketplace sécurisé Senta'a au Cameroun",
        contact=openapi.Contact(email="contact@ekila.com"),
        license=openapi.License(name="License Propriétaire"),
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)


urlpatterns = [
    path('administration/', admin.site.urls),
    path(
        "api/docs/swagger/",
        schema_view.with_ui("swagger", cache_timeout=0),
        name="swagger-ui",
    ),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    # applications exposées sous /api/ ; n'oubliez pas d'ajouter/mettre à jour
    # chaque module urls.py correspondant.
    path('api/', include('users.urls')),
    path('api/', include('marketplace.urls')),
    path('api/', include('jobs.urls')),
    path('api/', include('escrow.urls')),
]
