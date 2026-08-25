import uuid

from cloudinary.models import CloudinaryField
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.models import BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField

from common.images.validators import ImageFileValidator


def courier_document_public_id(instance):
    return f"{settings.CLOUDINARY_FOLDER_PREFIX}/users/courier_docs/{instance.id}"


class CustomUserManager(BaseUserManager):
    def create_user(self, phone_number, password=None, **extra_fields):
        if not phone_number:
            raise ValueError("Le numéro de téléphone est requis")
        user = self.model(phone_number=phone_number, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(phone_number, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_number = PhoneNumberField(unique=True, db_index=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    email = models.EmailField(blank=True, null=True, unique=True)
    newsletter_opt_in = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    is_seller = models.BooleanField(default=False)
    is_recruiter = models.BooleanField(default=False)
    is_courier = models.BooleanField(default=False)

    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    location_updated_at = models.DateTimeField(null=True, blank=True)

    is_available = models.BooleanField(default=False)

    COURIER_APPLICATION_CHOICES = (
        ("none", "Aucune demande"),
        ("pending", "En attente de validation"),
        ("approved", "Approuvée"),
        ("rejected", "Refusée"),
    )
    courier_application_status = models.CharField(
        max_length=10, choices=COURIER_APPLICATION_CHOICES, default="none"
    )

    COURIER_VEHICLE_CHOICES = (
        ("moto", "Moto"),
        ("velo", "Vélo"),
        ("voiture", "Voiture"),
    )

    courier_vehicle_type = models.CharField(
        max_length=10, choices=COURIER_VEHICLE_CHOICES, blank=True
    )

    courier_id_document = CloudinaryField(
        resource_type="image",
        type="authenticated",
        public_id=courier_document_public_id,
        overwrite=True,
        invalidate=True,
        unique_filename=False,
        use_filename=False,
        allowed_formats=["jpg", "jpeg", "png", "webp"],
        validators=[ImageFileValidator(max_bytes=5 * 1024 * 1024)],
        null=True,
        blank=True,
        help_text=(
            "Pièce d'identité pour la candidature coursier. Stockée sur Cloudinary en "
            "accès restreint (non public)."
        ),
    )

    objects = CustomUserManager()

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = "Utilisateur"
        ordering = ["-date_joined"]

    def __str__(self):
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name} ({self.phone_number})"
        return str(self.phone_number)
