from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from common.images.delivery import build_url
from common.images.validators import DEFAULT_MAX_PIXELS
from common.images.validators import ImageFileValidator


class CloudinaryImageField(serializers.ImageField):
    def __init__(
        self,
        *args,
        variant="full",
        signed=False,
        max_bytes=5 * 1024 * 1024,
        max_pixels=DEFAULT_MAX_PIXELS,
        **kwargs,
    ):
        self.variant = variant
        self.signed = signed
        self._validate_extra = ImageFileValidator(
            max_bytes=max_bytes, max_pixels=max_pixels
        )
        super().__init__(*args, **kwargs)

    def to_internal_value(self, data):
        file = super().to_internal_value(data)
        try:
            self._validate_extra(file)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        file.seek(0)
        return file

    def to_representation(self, value):
        return build_url(value, variant=self.variant, signed=self.signed)
