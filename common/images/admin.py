"""
Aperçu image réutilisable pour les `ModelAdmin` exposant un champ `CloudinaryField` en
lecture (voir users/admin.py, marketplace/admin.py, companies/admin.py).

Le widget par défaut de `cloudinary.forms.CloudinaryFileField` n'affiche pas de vignette et,
pour une ressource en delivery type "authenticated" (pièce d'identité coursier), produit un
lien NON signé donc inutilisable (401 côté Cloudinary) — d'où ce helper plutôt que de
s'appuyer sur le rendu par défaut.
"""

from django.utils.html import format_html

from common.images.delivery import build_url


def image_preview_html(
    resource,
    *,
    signed=False,
    thumb_variant="thumbnail",
    link_variant="full",
    height=120,
    empty_text="—",
):
    thumb_url = build_url(resource, variant=thumb_variant, signed=signed)
    if not thumb_url:
        return empty_text
    link_url = build_url(resource, variant=link_variant, signed=signed) or thumb_url
    return format_html(
        '<a href="{0}" target="_blank" rel="noopener">'
        '<img src="{1}" style="max-height:{2}px; border-radius:4px;" /></a>',
        link_url,
        thumb_url,
        height,
    )
