import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models


class JobOffer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recruiter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posted_jobs"
    )
    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_offers",
    )
    title = models.CharField(max_length=255, db_index=True)
    company_name = models.CharField(max_length=255)
    description = models.TextField()
    location = models.CharField(max_length=100, default="Douala")
    is_remote = models.BooleanField(default=False)
    salary_range = models.CharField(max_length=100, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class JobApplication(models.Model):
    STATUS_CHOICES = (
        ("pending", "En attente"),
        ("reviewed", "Consulté"),
        ("accepted", "Accepté"),
        ("rejected", "Refusé"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        JobOffer, on_delete=models.CASCADE, related_name="applications"
    )
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="my_job_apps"
    )

    cv_file = models.FileField(
        null=True,
        blank=True,
        upload_to="cvs/%Y/%m/",
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
    )
    message = models.TextField(help_text="Lettre de motivation courte")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    applied_at = models.DateTimeField(auto_now_add=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("job", "applicant")  # On ne postule qu'une fois


class JobOfferFavorite(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorite_jobs"
    )
    job = models.ForeignKey(
        JobOffer, on_delete=models.CASCADE, related_name="favorited_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "job")
