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
        dummy_cv = SimpleUploadedFile(
            "cv.pdf", b"file_content", content_type="application/pdf"
        )

        response = self.client.post(
            "/api/job-applications/",
            {"job": self.job.id, "cv_file": dummy_cv, "message": "Je suis motivé"},
        )
        self.assertEqual(response.status_code, 201)

    def test_toggle_favorite_job_offer(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.talent)

        response = self.client.post(f"/api/job-offers/{self.job.id}/toggle_favorite/")
        self.assertEqual(response.data, {"favorited": True})

        favorites = self.client.get("/api/job-offers/favorites/")
        self.assertEqual(len(favorites.data["results"]), 1)

        response = self.client.post(f"/api/job-offers/{self.job.id}/toggle_favorite/")
        self.assertEqual(response.data, {"favorited": False})
