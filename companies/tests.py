from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from companies.models import CompanyProfile
from jobs.models import JobOffer
from marketplace.models import Category
from marketplace.models import Listing
from users.models import User


class CompanyProfileTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+237611500000", first_name="Owner", last_name="Test"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_own_profile_via_me_endpoint(self):
        response = self.client.patch(
            "/api/companies/me/", {"name": "Kmer Fashion", "sector": "Mode"}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            CompanyProfile.objects.get(owner=self.user).name, "Kmer Fashion"
        )

    def test_creating_profile_grants_is_recruiter(self):
        self.assertFalse(self.user.is_recruiter)
        self.client.patch("/api/companies/me/", {"name": "Kmer Fashion"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_recruiter)

        # Une mise à jour du profil existant ne doit pas re-déclencher/casser quoi que ce soit.
        response = self.client.patch("/api/companies/me/", {"sector": "Mode"})
        self.assertEqual(response.status_code, 200)

    def test_is_verified_not_settable_by_owner(self):
        self.client.patch("/api/companies/me/", {"name": "Kmer Fashion"})
        response = self.client.patch("/api/companies/me/", {"is_verified": True})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CompanyProfile.objects.get(owner=self.user).is_verified)

    def test_public_page_lists_active_listings_and_jobs(self):
        company = CompanyProfile.objects.create(
            owner=self.user, name="Kmer Fashion", is_verified=True
        )
        category = Category.objects.create(name="Mode", slug="mode")
        Listing.objects.create(
            seller=self.user,
            company=company,
            title="Robe",
            description="d",
            price=5000,
            category=category,
        )
        JobOffer.objects.create(
            recruiter=self.user,
            company=company,
            title="Vendeur",
            company_name="Kmer Fashion",
        )

        client = APIClient()  # page publique, non authentifié
        response = client.get(f"/api/companies/{company.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_verified"])
        self.assertEqual(len(response.data["listings"]), 1)
        self.assertEqual(len(response.data["job_offers"]), 1)
