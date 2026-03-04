from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from users.models import User

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('phone_number', 'first_name','last_name', 'is_seller', 'is_recruiter', 'is_courier', 'is_staff', 'is_active')
    
    list_filter = ('is_seller', 'is_recruiter', 'is_courier', 'is_staff', 'is_active')
    
    search_fields = ('phone_number', 'first_name','last_name')
    readonly_fields = ('date_joined', 'last_login')
    fieldsets = (
        (None, {'fields': ('phone_number', 'password')}),
        ('Infos Personnelles', {'fields': ('first_name','last_name',)}),
        ('Rôles & Permissions', {'fields': ('is_seller', 'is_recruiter', 'is_courier', 'is_active', 'is_staff', 'is_superuser')}),
        ('Dates importantes', {'fields': ('last_login', 'date_joined')}),
    )
    
    ordering = ('-date_joined',)