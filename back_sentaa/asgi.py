"""
ASGI config for back_sentaa project.

Exposes the ASGI callable as a module-level variable named ``application``.
Routes HTTP to the classic Django app and WebSockets to Channels
(chat + suivi coursier temps réel), authentifiés via JWT en query-string.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

from channels.routing import ProtocolTypeRouter
from channels.routing import URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "back_sentaa.settings")

# django.setup() doit être terminé (via get_asgi_application()) avant d'importer quoi que ce
# soit qui touche aux modèles/apps Django (routing, middleware, consumers) : ces imports
# restent volontairement après cet appel, et volontairement hors du bloc trié par
# reorder-python-imports (voir .pre-commit-config.yaml, exclude) qui les remonterait sinon
# en tête de fichier et réintroduirait l'AppRegistryNotReady sous gunicorn/uvicorn.
django_asgi_app = get_asgi_application()

from chat.middleware import JWTAuthMiddleware  # noqa: E402
from chat.routing import (  # noqa: E402
    websocket_urlpatterns as chat_websocket_urlpatterns,
)
from logistics.routing import (  # noqa: E402
    websocket_urlpatterns as logistics_websocket_urlpatterns,
)

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JWTAuthMiddleware(
            URLRouter(chat_websocket_urlpatterns + logistics_websocket_urlpatterns)
        ),
    }
)
