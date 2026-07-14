import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


class AuthTests(APITestCase):
    def setUp(self):
        # Le throttle scope 'otp' (ScopedRateThrottle, 5/min) partage le cache entre méthodes
        # de test — sans ce clear, l'ordre d'exécution peut faire dépasser la limite et
        # renvoyer 429 sur des tests qui n'ont rien à voir avec le rate-limiting lui-même.
        cache.clear()
        self.register_url = reverse("register")
        self.otp_request_url = reverse("otp-request")
        self.user_data = {
            "phone_number": "+237670000000",
            "first_name": "Test",
            "last_name": "User",
            "password": "securepassword123",
        }

    # le paquet racine s'appelle simplement `users` dans ce projet (pas `apps`).
    # on cible donc le module réel où la classe est définie.
    @patch("users.services.otpservice.OTPService.generate_otp")
    def test_registration_success(self, mock_otp):
        """Vérifie la création d'utilisateur et l'appel de l'OTP"""
        mock_otp.return_value = "123456"
        response = self.client.post(self.register_url, self.user_data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(phone_number="+237670000000").exists())
        mock_otp.assert_called_once()

    def test_otp_verification_flow(self):
        """Test complet du flux de connexion via OTP (Mocké en cache)"""
        # 1. Créer l'utilisateur
        # `username` n'existe pas sur notre modèle personnalisé
        User.objects.create_user(
            phone_number="+237680000000",
            first_name="Verify User",
            last_name="Test",
            password="anothersecurepassword",
        )

        # 2. Simuler un code en cache (concept d'injection de dépendance)
        from django.core.cache import cache

        cache.set("otp_+237680000000", "654321", timeout=300)

        # 3. Vérifier le code via l'API
        verify_url = reverse("otp-verify")
        response = self.client.post(
            verify_url, {"phone_number": "+237680000000", "otp_code": "654321"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)  # Le token JWT est présent

    @patch("users.services.otpservice.TwilioSMSService.send_sms")
    @patch("users.services.otpservice.settings")
    def test_registration_returns_502_when_sms_send_fails(
        self, mock_settings, mock_send_sms
    ):
        from users.services.smsservice import SMSDeliveryError

        mock_settings.SMS_BACKEND = "twilio"
        mock_send_sms.side_effect = SMSDeliveryError("boom")

        response = self.client.post(
            self.register_url,
            {
                "phone_number": "+237690000000",
                "first_name": "Echec",
                "last_name": "Sms",
                "password": "securepassword123",
            },
        )

        self.assertEqual(response.status_code, 502)
        # L'utilisateur est bien créé (l'échec SMS n'annule pas l'inscription), juste non notifié.
        self.assertTrue(User.objects.filter(phone_number="+237690000000").exists())


class MeViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+237611600000",
            first_name="Coursier",
            last_name="Test",
            is_courier=True,
        )
        self.client.force_authenticate(user=self.user)

    def test_get_own_profile(self):
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["phone_number"], "+237611600000")

    def test_courier_can_update_availability_and_position(self):
        response = self.client.patch(
            "/api/me/",
            {
                "is_available": True,
                "latitude": "4.05",
                "longitude": "9.70",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_available)
        self.assertIsNotNone(self.user.location_updated_at)

    def test_role_flags_are_read_only(self):
        response = self.client.patch(
            "/api/me/", {"is_recruiter": True, "is_staff": True}
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_recruiter)
        self.assertFalse(self.user.is_staff)


class ApplyCourierTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+237611700000", first_name="A", last_name="T"
        )
        self.client.force_authenticate(user=self.user)

    def test_apply_sets_pending_status(self):
        response = self.client.post("/api/me/apply-courier/")
        self.assertEqual(response.status_code, 201)
        self.user.refresh_from_db()
        self.assertEqual(self.user.courier_application_status, "pending")
        self.assertFalse(self.user.is_courier)

    def test_applying_twice_does_not_error(self):
        self.client.post("/api/me/apply-courier/")
        response = self.client.post("/api/me/apply-courier/")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.courier_application_status, "pending")

    def test_already_courier_short_circuits(self):
        self.user.is_courier = True
        self.user.save(update_fields=["is_courier"])
        response = self.client.post("/api/me/apply-courier/")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.courier_application_status, "none")

    def test_admin_action_approves_courier(self):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory

        from users.admin import CustomUserAdmin

        self.client.post("/api/me/apply-courier/")
        request = RequestFactory().post("/administration/users/user/")
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        admin_instance = CustomUserAdmin(User, AdminSite())
        admin_instance.approve_courier_applications(
            request, User.objects.filter(id=self.user.id)
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_courier)
        self.assertEqual(self.user.courier_application_status, "approved")


class EmailLoginTests(APITestCase):
    """SMS non finançable pour l'instant : l'email est le canal OTP pratique en attendant,
    en plus du téléphone (pas à sa place) — même compte, deux façons de se connecter."""

    def setUp(self):
        cache.clear()  # même raison que AuthTests.setUp : throttle scope 'otp' partagé.
        self.user = User.objects.create_user(
            phone_number="+237611800000",
            first_name="Mail",
            last_name="User",
            email="mailuser@example.com",
        )

    def test_otp_request_by_email_sends_email(self):
        response = self.client.post(
            reverse("otp-request"), {"email": "mailuser@example.com"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("mailuser@example.com", mail.outbox[0].to)

    def test_full_email_otp_login_flow(self):
        self.client.post(reverse("otp-request"), {"email": "mailuser@example.com"})
        code = re.search(r"\d{6}", mail.outbox[0].body).group()

        response = self.client.post(
            reverse("otp-verify"), {"email": "mailuser@example.com", "otp_code": code}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertEqual(
            response.data["user"]["phone_number"], str(self.user.phone_number)
        )

    def test_otp_request_rejects_when_neither_identifier_given(self):
        response = self.client.post(reverse("otp-request"), {})
        self.assertEqual(response.status_code, 400)

    def test_otp_request_rejects_when_both_identifiers_given(self):
        response = self.client.post(
            reverse("otp-request"),
            {
                "phone_number": str(self.user.phone_number),
                "email": "mailuser@example.com",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_otp_verify_rejects_when_both_identifiers_given(self):
        response = self.client.post(
            reverse("otp-verify"),
            {
                "phone_number": str(self.user.phone_number),
                "email": "mailuser@example.com",
                "otp_code": "123456",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicate_email_rejected_at_registration(self):
        response = self.client.post(
            reverse("register"),
            {
                "phone_number": "+237611800099",
                "first_name": "Dup",
                "last_name": "User",
                "email": "mailuser@example.com",
                "password": "securepassword123",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data)

    def test_multiple_users_can_have_no_email(self):
        User.objects.create_user(
            phone_number="+237611800001", first_name="NoMail1", last_name="T"
        )
        User.objects.create_user(
            phone_number="+237611800002", first_name="NoMail2", last_name="T"
        )
        # Aucune IntegrityError : les deux ont email=None (pas ""), autorisé sous la contrainte
        # unique — c'est précisément pourquoi la migration convertit "" en NULL.
        self.assertEqual(User.objects.filter(email__isnull=True).count(), 2)
