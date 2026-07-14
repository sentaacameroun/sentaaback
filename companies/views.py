from rest_framework import permissions
from rest_framework.generics import RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from companies.models import CompanyProfile
from companies.serializers import CompanyProfilePublicSerializer
from companies.serializers import CompanyProfileSerializer


class MyCompanyProfileView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profile = getattr(request.user, "company_profile", None)
        if profile is None:
            return Response({"detail": "Aucun profil entreprise."}, status=404)
        return Response(CompanyProfileSerializer(profile).data)

    def patch(self, request):
        profile = getattr(request.user, "company_profile", None)
        is_creation = profile is None
        serializer = CompanyProfileSerializer(
            profile, data=request.data, partial=not is_creation
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(owner=request.user)

        if is_creation and not request.user.is_recruiter:
            request.user.is_recruiter = True
            request.user.save(update_fields=["is_recruiter"])

        return Response(serializer.data, status=200 if profile else 201)


class CompanyProfilePublicView(RetrieveAPIView):
    queryset = CompanyProfile.objects.all()
    serializer_class = CompanyProfilePublicSerializer
    permission_classes = [permissions.AllowAny]
