# apps/jobs/permissions.py
from rest_framework import permissions


class IsRecruiter(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_recruiter
        )


class IsVerifiedRecruiter(permissions.BasePermission):
    """
    Un compte Entreprise a accès à son espace dès la création du profil
    (`is_recruiter=True`), mais ne peut publier une offre que si un admin a
    vérifié `company_profile.is_verified` — sinon `IsRecruiter` seul
    permettait à n'importe quelle entreprise non vérifiée de publier.
    """

    message = "Votre profil entreprise doit être vérifié avant de publier une offre."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_recruiter):
            return False
        company = getattr(user, "company_profile", None)
        return bool(company and company.is_verified)


class IsApplicationRecruiter(permissions.BasePermission):
    """Autorise uniquement le recruteur propriétaire de l'offre concernée par la candidature."""

    def has_object_permission(self, request, view, obj):
        return bool(
            request.user
            and request.user.is_authenticated
            and obj.job.recruiter_id == request.user.id
        )


class IsOwnerRecruiter(permissions.BasePermission):
    """
    Update/destroy d'une offre : jusqu'ici `IsRecruiter` ne vérifiait que
    `is_recruiter`, sans jamais comparer `obj.recruiter` à l'utilisateur —
    n'importe quel recruteur pouvait modifier/supprimer l'offre d'un autre
    par son id. Ce garde-fou d'objet manquait.
    """

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_recruiter
        )

    def has_object_permission(self, request, view, obj):
        return obj.recruiter_id == request.user.id
