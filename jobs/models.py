import uuid
from django.db import models
from django.conf import settings
from django.core.validators import FileExtensionValidator

class TalentProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='talent_profile')
    bio = models.TextField(blank=True)
    skills = models.CharField(max_length=512, help_text="Liste de compétences séparées par des virgules")
    portfolio_url = models.URLField(blank=True, null=True)
    experience_years = models.PositiveIntegerField(default=0)
    
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profil de {self.user.first_name}"

class JobOffer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recruiter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='posted_jobs')
    title = models.CharField(max_length=255, db_index=True)
    company_name = models.CharField(max_length=255)
    description = models.TextField()
    location = models.CharField(max_length=100, default="Douala")
    is_remote = models.BooleanField(default=False)
    salary_range = models.CharField(max_length=100, blank=True)
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

class JobApplication(models.Model):

    STATUS_CHOICES = (
        ('pending', 'En attente'),
        ('reviewed', 'Consulté'),
        ('accepted', 'Accepté'),
        ('rejected', 'Refusé'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(JobOffer, on_delete=models.CASCADE, related_name='applications')
    applicant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='my_job_apps')
    
    cv_file = models.FileField(
        null=True, blank=True,
        upload_to='cvs/%Y/%m/', 
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])]
    )
    message = models.TextField(help_text="Lettre de motivation courte")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('job', 'applicant') # On ne postule qu'une fois