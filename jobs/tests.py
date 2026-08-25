from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from jobs.models import JobOffer
from users.models import User


class JobTests(APITestCase):
    def setUp(self):
        self.recruiter = User.objects.create_user(
            phone_number="+237699",
            first_name="Boss",
            last_name="Recruiter",
            is_recruiter=True,
        )
        self.talent = User.objects.create_user(
            phone_number="+237688775521",
            first_name="Dev",
            last_name="Python",
            password="test",
        )
        self.job = JobOffer.objects.create(
            recruiter=self.recruiter, title="Dev Python", company_name="Sentaa Corp"
        )

    def test_only_recruiter_can_post(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.talent)
        response = self.client.post("/api/job-offers/", {"title": "Test"})
        self.assertEqual(response.status_code, 403)

    def test_apply_to_job(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.talent)
        # Un vrai PDF commence par la signature `%PDF-` (la validation de contenu, pas
        # seulement l'extension, l'exige désormais).
        dummy_cv = SimpleUploadedFile(
            "cv.pdf",
            b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nendobj\n%%EOF",
            content_type="application/pdf",
        )

        response = self.client.post(
            "/api/job-applications/",
            {"job": self.job.id, "cv_file": dummy_cv, "message": "Je suis motivé"},
        )
        self.assertEqual(response.status_code, 201)

    def test_rejects_cv_that_is_not_a_real_pdf(self):
        # Régression : le `.pdf` dans le nom ne suffit pas. Un fichier renommé (ici un
        # faux binaire) passait le `FileExtensionValidator` mais n'est pas un vrai PDF.
        self.client = APIClient()
        self.client.force_authenticate(user=self.talent)
        fake_cv = SimpleUploadedFile(
            "cv.pdf", b"MZ\x90\x00 pas un pdf", content_type="application/pdf"
        )
        response = self.client.post(
            "/api/job-applications/",
            {"job": self.job.id, "cv_file": fake_cv, "message": "x"},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_oversized_cv(self):
        # Régression : aucune limite de taille n'existait, un upload pouvait saturer le
        # stockage. Le fichier a bien la signature PDF pour isoler le contrôle de taille.
        from jobs.serializers import MAX_CV_BYTES

        self.client = APIClient()
        self.client.force_authenticate(user=self.talent)
        oversized = SimpleUploadedFile(
            "cv.pdf",
            b"%PDF-1.4\n" + b"0" * (MAX_CV_BYTES + 1),
            content_type="application/pdf",
        )
        response = self.client.post(
            "/api/job-applications/",
            {"job": self.job.id, "cv_file": oversized, "message": "x"},
        )
        self.assertEqual(response.status_code, 400)

    def test_toggle_favorite_job_offer(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.talent)

        response = self.client.post(f"/api/job-offers/{self.job.id}/toggle_favorite/")
        self.assertEqual(response.data, {"favorited": True})

        favorites = self.client.get("/api/job-offers/favorites/")
        self.assertEqual(len(favorites.data["results"]), 1)

        response = self.client.post(f"/api/job-offers/{self.job.id}/toggle_favorite/")
        self.assertEqual(response.data, {"favorited": False})
