from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible
from PIL import Image

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
DEFAULT_MAX_PIXELS = (
    24_000_000  # ~ un JPEG 6000x4000 (24 Mpx) : au-delà, aucun usage mobile légitime
)


@deconstructible
class ImageFileValidator:

    def __init__(self, max_bytes, max_pixels=DEFAULT_MAX_PIXELS):
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels

    def __call__(self, file):
        _check_size(file, self.max_bytes)
        _check_image_content(file, self.max_pixels)

    def __eq__(self, other):
        return (
            isinstance(other, ImageFileValidator)
            and self.max_bytes == other.max_bytes
            and self.max_pixels == other.max_pixels
        )


def _check_size(file, max_bytes):
    size = getattr(file, "size", None)
    if size is None:  # fichier générique sans attribut .size (rare, défensif)
        file.seek(0, 2)
        size = file.tell()
        file.seek(0)
    if size > max_bytes:
        raise ValidationError(
            "Image trop lourde (%.1f Mo). Maximum autorisé : %.0f Mo."
            % (size / 1_000_000, max_bytes / 1_000_000),
            code="image_too_large",
        )


def _check_image_content(file, max_pixels):
    file.seek(0)
    try:
        with Image.open(file) as img:
            img.verify()  # intégrité du fichier, sans décoder les pixels
    except Exception as exc:
        raise ValidationError(
            "Fichier image invalide, corrompu ou trop volumineux à décoder.",
            code="invalid_image",
        ) from exc
    finally:
        file.seek(0)
    try:
        with Image.open(file) as img:
            img_format = img.format
            width, height = img.size
    except Exception as exc:
        raise ValidationError(
            "Fichier image invalide, corrompu ou trop volumineux à décoder.",
            code="invalid_image",
        ) from exc
    finally:
        file.seek(0)

    if img_format not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError(
            "Format d'image non supporté (%s). Formats acceptés : JPEG, PNG, WEBP."
            % img_format,
            code="unsupported_format",
        )
    if width * height > max_pixels:
        raise ValidationError(
            "Dimensions d'image excessives (%dx%d)." % (width, height),
            code="image_dimensions_too_large",
        )
