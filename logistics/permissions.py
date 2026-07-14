from rest_framework import permissions


class IsAssignedCourier(permissions.BasePermission):
    """Autorise uniquement le coursier assigné à cette livraison."""

    def has_object_permission(self, request, view, obj):
        return bool(
            request.user
            and request.user.is_authenticated
            and obj.courier_id == request.user.id
        )


class IsCourier(permissions.BasePermission):
    """Autorise n'importe quel coursier (utilisé par `claim`, avant assignation)."""

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_courier
        )
