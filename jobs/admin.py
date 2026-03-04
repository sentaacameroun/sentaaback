from django.contrib import admin
from .models import TalentProfile, JobOffer, JobApplication
from django.utils.html import format_html

@admin.register(JobOffer)
class JobOfferAdmin(admin.ModelAdmin):
    list_display = ('title', 'company_name', 'recruiter', 'is_active', 'applications_count', 'created_at')
    list_filter = ('is_active', 'location')
    
    def applications_count(self, obj):
        return obj.applications.count()
    applications_count.short_description = "Candidatures"

@admin.register(JobApplication)
class JobApplicationAdmin(admin.ModelAdmin):
    list_display = ('job', 'applicant', 'status', 'applied_at')
    list_filter = ('status',)
    
    def cv_link(self, obj):
        if obj.cv_file:
            return format_html('<a href="{}" target="_blank">Voir le CV</a>', obj.cv_file.url)
        return "Aucun CV"
    
    readonly_fields = ('applied_at',)