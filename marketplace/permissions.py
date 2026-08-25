# apps/marketplace/permissions.py
from rest_framework import permissions


class IsOwnerSeller(permissions.BasePermission):
    """
    Update/destroy d'une annonce : jusqu'ici `ListingViewSet` n'appliquait que
    le défaut global `IsAuthenticated`, sans jamais comparer `obj.seller` à
    l'utilisateur — n'importe quel compte connecté pouvait modifier/supprimer
    l'annonce d'un autre vendeur par son id (IDOR). Même pattern que
    `jobs/permissions.py::IsOwnerRecruiter`.
    """

    def has_object_permission(self, request, view, obj):
        return obj.seller_id == request.user.id
