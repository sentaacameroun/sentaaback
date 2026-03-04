from django.contrib import admin
from django.utils.html import format_html
from .models import Category, Listing, ListingImage

class ListingImageInline(admin.TabularInline):
    model = ListingImage
    extra = 1 

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)} # Remplissage auto du slug

@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ('title', 'seller', 'category', 'price', 'city', 'status', 'is_promoted', 'created_at')
    list_filter = ('status', 'city', 'category', 'is_promoted')
    search_fields = ('title', 'description', 'seller__phone_number')
    list_editable = ('status', 'is_promoted') 
    inlines = [ListingImageInline] 
    
    # Optionnel: Afficher une miniature de l'image principale
    def view_on_site(self, obj):
        return f"/marketplace/listings/{obj.id}/"