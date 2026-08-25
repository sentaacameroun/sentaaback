"""
Aides de test réutilisables par les apps qui exercent un champ `CloudinaryField` (users,
marketplace, companies) — génèrent un fichier image valide en mémoire (passe la validation
Pillow, voir common/images/validators.py) et un faux résultat d'upload Cloudinary, pour
mocker `cloudinary.uploader.upload` sans appel réseau réel dans les tests.

Volontairement PAS nommé `tests.py` : Django/pytest le prendrait pour un module de tests à
exécuter lui-même plutôt qu'un simple utilitaire importé par les vrais fichiers de tests.
"""

import io

import cloudinary
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image


def make_test_image_file(
    name="test.jpg", size=(64, 64), image_format="JPEG", content_type="image/jpeg"
):
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(120, 140, 160)).save(buffer, format=image_format)
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type=content_type)


def use_test_cloudinary_credentials():
    """
    Configure des identifiants Cloudinary factices (mais non vides) et renvoie un callable de
    restauration, à utiliser dans un test ainsi : `self.addCleanup(use_test_cloudinary_credentials())`.

    Nécessaire pour tester la construction d'URLs SIGNÉES (`sign_url=True`, voir
    common/images/delivery.py) : le SDK Cloudinary refuse de signer (`ValueError: Must supply
    api_secret`) sans `api_secret` non vide, et l'environnement de test de ce projet n'en a
    pas de réel configuré (voir CLOUDINARY dans back_sentaa/settings.py).
    """
    original = {
        "cloud_name": cloudinary.config().cloud_name,
        "api_key": cloudinary.config().api_key,
        "api_secret": cloudinary.config().api_secret,
    }
    cloudinary.config(
        cloud_name="test-cloud", api_key="123456789", api_secret="test-secret"
    )
    return lambda: cloudinary.config(**original)


def fake_cloudinary_upload_result(
    public_id="sentaa/test/public_id",
    resource_type="image",
    type="upload",
    format="jpg",
):
    """
    Forme minimale attendue par `cloudinary.uploader.upload_resource` (appelé en interne par
    `CloudinaryField.pre_save`, voir cloudinary/models.py du package) pour reconstruire un
    `CloudinaryResource` — à passer comme `return_value` d'un mock sur
    `cloudinary.uploader.upload`.
    """
    return {
        "public_id": public_id,
        "version": "1",
        "format": format,
        "type": type,
        "resource_type": resource_type,
    }
