"""Configuration pytest partagée à la racine du projet.

Les tests tournent avec `DEBUG=False` pour rester proches de la production, mais dans ce
mode `settings.py` active `SECURE_SSL_REDIRECT` : `SecurityMiddleware` répond alors 301
(http→https) à chaque requête, ce qui casse le client de test (qui poste en HTTP). On
désactive la redirection **uniquement pendant les tests**, sans toucher au comportement
réel de production. Voir REFACTOR_PLAN.md (PR 4, blocages CI).
"""

import pytest


@pytest.fixture(autouse=True)
def _disable_ssl_redirect(settings):
    settings.SECURE_SSL_REDIRECT = False


@pytest.fixture(autouse=True)
def _isolate_media_root(settings, tmp_path):
    """Redirige `MEDIA_ROOT` vers un dossier temporaire par test.

    Les tests d'upload (CV, etc.) écrivent via `FileSystemStorage` ; sans ça ils polluent
    le `media/` du dépôt et échouent si un sous-dossier a été créé root par le conteneur
    Docker (PermissionError). Un tmp par test rend la suite hermétique et déterministe.
    """
    settings.MEDIA_ROOT = str(tmp_path / "media")


@pytest.fixture(autouse=True)
def _isolate_cloudinary_config():
    """Empêche un test de laisser fuiter son `cloudinary.config()` global vers les suivants.

    Hygiène : `cloudinary.config()` est un singleton process-wide ; les tests qui appellent
    `use_test_cloudinary_credentials()` (common/images/testing.py) le mutent. On restaure
    l'état d'avant-test après chaque test → déterminisme quel que soit l'ordre d'exécution.
    (NB : la cause des tests cleanup Cloudinary rouges n'était PAS ici mais la non-inscription
    des récepteurs post_delete dans companies/marketplace apps.py — corrigée par ailleurs.)
    """
    import cloudinary

    saved = {
        "cloud_name": cloudinary.config().cloud_name,
        "api_key": cloudinary.config().api_key,
        "api_secret": cloudinary.config().api_secret,
        "secure": cloudinary.config().secure,
    }
    yield
    cloudinary.config(**saved)
