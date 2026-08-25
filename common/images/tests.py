from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from common.images.testing import make_test_image_file
from common.images.validators import ImageFileValidator


class ImageFileValidatorTests(SimpleTestCase):
    def test_accepts_valid_small_image(self):
        validator = ImageFileValidator(max_bytes=1024 * 1024)
        file = make_test_image_file()
        validator(file)  # ne doit pas lever

    def test_rejects_file_over_size_limit(self):
        validator = ImageFileValidator(
            max_bytes=10
        )  # plus petit que n'importe quelle image
        file = make_test_image_file()
        with self.assertRaises(ValidationError) as ctx:
            validator(file)
        self.assertEqual(ctx.exception.code, "image_too_large")

    def test_rejects_non_image_content(self):
        validator = ImageFileValidator(max_bytes=1024 * 1024)
        file = make_test_image_file(name="fake.jpg")
        file.write(b"ceci n'est pas une image" * 10)
        file.seek(0)
        with self.assertRaises(ValidationError) as ctx:
            validator(file)
        self.assertEqual(ctx.exception.code, "invalid_image")

    def test_rejects_disallowed_format(self):
        validator = ImageFileValidator(max_bytes=1024 * 1024)
        file = make_test_image_file(
            name="test.bmp", image_format="BMP", content_type="image/bmp"
        )
        with self.assertRaises(ValidationError) as ctx:
            validator(file)
        self.assertEqual(ctx.exception.code, "unsupported_format")

    def test_rejects_excessive_dimensions(self):
        # max_pixels volontairement très bas pour ne pas avoir à générer une vraie image
        # énorme dans le test.
        validator = ImageFileValidator(max_bytes=1024 * 1024, max_pixels=100)
        file = make_test_image_file(size=(64, 64))
        with self.assertRaises(ValidationError) as ctx:
            validator(file)
        self.assertEqual(ctx.exception.code, "image_dimensions_too_large")

    def test_leaves_file_pointer_readable_after_validation(self):
        # L'upload Cloudinary qui suit la validation (voir common/images/fields.py) doit
        # pouvoir relire le fichier depuis le début.
        validator = ImageFileValidator(max_bytes=1024 * 1024)
        file = make_test_image_file()
        validator(file)
        self.assertEqual(file.tell(), 0)
        self.assertTrue(file.read())

    def test_equality_for_migration_autodetector(self):
        # Les validateurs déconstructibles doivent supporter __eq__ pour que
        # `makemigrations` ne détecte pas un changement fantôme d'une run à l'autre.
        self.assertEqual(
            ImageFileValidator(max_bytes=100, max_pixels=200),
            ImageFileValidator(max_bytes=100, max_pixels=200),
        )
        self.assertNotEqual(
            ImageFileValidator(max_bytes=100), ImageFileValidator(max_bytes=200)
        )
