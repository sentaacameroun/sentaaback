from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from common.images.testing import fake_cloudinary_upload_result
from common.images.testing import make_test_image_file
from common.images.testing import use_test_cloudinary_credentials
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

    @patch("cloudinary.uploader.upload")
    def test_upload_logo_via_me_endpoint(self, mock_upload):
        # La réponse sérialise `logo` (voir CompanyProfileSerializer) : construit une URL de
        # délivrance (voir common/images/delivery.py), qui a besoin d'un cloud_name — absent
        # de l'environnement de test (voir CLOUDINARY dans back_sentaa/settings.py).
        self.addCleanup(use_test_cloudinary_credentials())
        mock_upload.return_value = fake_cloudinary_upload_result(
            public_id="sentaa/companies/logos/x"
        )
        response = self.client.patch(
            "/api/companies/me/",
            {"name": "Kmer Fashion", "logo": make_test_image_file()},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertIsInstance(response.data["logo"], str)
        self.assertTrue(response.data["logo"])
        mock_upload.assert_called_once()

    def test_rejects_invalid_logo_without_calling_cloudinary(self):
        bad_file = SimpleUploadedFile(
            "logo.jpg", b"pas une image", content_type="image/jpeg"
        )
        with patch("cloudinary.uploader.upload") as mock_upload:
            response = self.client.patch(
                "/api/companies/me/",
                {"name": "Kmer Fashion", "logo": bad_file},
                format="multipart",
            )
        self.assertEqual(response.status_code, 400)
        mock_upload.assert_not_called()
        self.assertFalse(CompanyProfile.objects.filter(owner=self.user).exists())

    @patch("cloudinary.uploader.upload")
    def test_deleting_profile_cleans_up_cloudinary_logo(self, mock_upload):
        self.addCleanup(use_test_cloudinary_credentials())
        mock_upload.return_value = fake_cloudinary_upload_result(
            public_id="sentaa/companies/logos/x"
        )
        self.client.patch(
            "/api/companies/me/",
            {"name": "Kmer Fashion", "logo": make_test_image_file()},
            format="multipart",
        )
        profile = CompanyProfile.objects.get(owner=self.user)

        with patch("cloudinary.uploader.destroy") as mock_destroy:
            profile.delete()
        mock_destroy.assert_called_once_with(
            "sentaa/companies/logos/x", resource_type="image", type="upload"
        )

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
