from django.urls import path

from companies.views import CompanyProfilePublicView
from companies.views import MyCompanyProfileView

urlpatterns = [
    path("companies/me/", MyCompanyProfileView.as_view(), name="company-profile-me"),
    path(
        "companies/<uuid:pk>/",
        CompanyProfilePublicView.as_view(),
        name="company-profile-public",
    ),
]
