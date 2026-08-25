import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from common.images.testing import fake_cloudinary_upload_result
from common.images.testing import make_test_image_file
from common.images.testing import use_test_cloudinary_credentials

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

    @patch("users.services.otpservice.OTPEmailService.send_otp_email")
    def test_registration_returns_502_when_otp_send_fails(self, mock_send_otp_email):
        # L'inscription notifie l'OTP par email (RegisterView → generate_otp channel="email").
        # Si l'envoi échoue, la vue renvoie 502 mais l'utilisateur reste créé (juste non
        # notifié). L'ancien test mockait le canal SMS, obsolète depuis le passage à l'email.
        from users.services.emailservice import EmailDeliveryError

        mock_send_otp_email.side_effect = EmailDeliveryError("boom")

        response = self.client.post(
            self.register_url,
            {
                "phone_number": "+237690000000",
                "first_name": "Echec",
                "last_name": "Otp",
                "email": "echec.otp@example.com",
                "password": "securepassword123",
            },
        )

        self.assertEqual(response.status_code, 502)
        # L'utilisateur est bien créé (l'échec de notification n'annule pas l'inscription).
        self.assertTrue(User.objects.filter(phone_number="+237690000000").exists())


class JWTRefreshLogoutTests(APITestCase):
    """PR 6 : refresh token branché + révocation par blacklist au logout."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            phone_number="+237611900000", first_name="Jwt", last_name="User"
        )

    def test_valid_refresh_token_returns_new_access_token(self):
        refresh = str(RefreshToken.for_user(self.user))
        response = self.client.post(reverse("token-refresh"), {"refresh": refresh})
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_blacklisted_refresh_token_is_rejected_after_logout(self):
        refresh = str(RefreshToken.for_user(self.user))
        # Logout : révoque le refresh token (endpoint authentifié).
        self.client.force_authenticate(user=self.user)
        logout = self.client.post(reverse("logout"), {"refresh": refresh})
        self.assertEqual(logout.status_code, status.HTTP_205_RESET_CONTENT)
        # Le token blacklisté ne doit plus permettre de rafraîchir un access.
        self.client.force_authenticate(user=None)
        response = self.client.post(reverse("token-refresh"), {"refresh": refresh})
        self.assertEqual(response.status_code, 401)

    def test_logout_without_refresh_token_is_rejected(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(reverse("logout"), {})
        self.assertEqual(response.status_code, 400)

    def test_logout_requires_authentication(self):
        refresh = str(RefreshToken.for_user(self.user))
        response = self.client.post(reverse("logout"), {"refresh": refresh})
        self.assertEqual(response.status_code, 401)


class AvailabilityWriteTests(APITestCase):
    """PR 6 (MINEUR #16) : `is_available` réservé aux coursiers."""

    def test_non_courier_cannot_set_available(self):
        user = User.objects.create_user(
            phone_number="+237611910000", first_name="Non", last_name="Courier"
        )
        self.client.force_authenticate(user=user)
        response = self.client.patch("/api/me/", {"is_available": True})
        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.is_available)

    def test_courier_can_set_available(self):
        user = User.objects.create_user(
            phone_number="+237611920000",
            first_name="Est",
            last_name="Courier",
            is_courier=True,
        )
        self.client.force_authenticate(user=user)
        response = self.client.patch("/api/me/", {"is_available": True})
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_available)


class OTPEnumerationTests(APITestCase):
    """PR 6 (MINEUR #15) : otp-request ne doit pas distinguer compte existant
    d'un compte inexistant."""

    def setUp(self):
        cache.clear()  # throttle scope 'otp' partagé (voir AuthTests.setUp).
        # Préfixe mobile Cameroun valide (67…) : le PhoneNumberField du serializer
        # valide le format en entrée, contrairement à create_user sur le modèle.
        self.user = User.objects.create_user(
            phone_number="+237670000055", first_name="Exist", last_name="User"
        )

    def test_same_response_for_existing_and_missing_phone(self):
        existing = self.client.post(
            reverse("otp-request"), {"phone_number": "+237670000055"}
        )
        missing = self.client.post(
            reverse("otp-request"), {"phone_number": "+237670000066"}
        )
        self.assertEqual(existing.status_code, 200)
        self.assertEqual(missing.status_code, existing.status_code)
        self.assertEqual(existing.data, missing.data)


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

    # La candidature exige désormais une photo de pièce d'identité (voir
    # users/views.py::ApplyCourierView) : on mocke `cloudinary.uploader.upload` (appelé en
    # interne par `CloudinaryField.pre_save`, voir users/models.py) pour ne pas dépendre d'un
    # vrai compte Cloudinary dans les tests.
    @patch("cloudinary.uploader.upload")
    def test_apply_sets_pending_status(self, mock_upload):
        mock_upload.return_value = fake_cloudinary_upload_result(type="authenticated")
        response = self.client.post(
            "/api/me/apply-courier/",
            {"vehicle_type": "moto", "id_document": make_test_image_file()},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.user.refresh_from_db()
        self.assertEqual(self.user.courier_application_status, "pending")
        self.assertFalse(self.user.is_courier)
        mock_upload.assert_called_once()

    @patch("cloudinary.uploader.upload")
    def test_applying_twice_does_not_error(self, mock_upload):
        mock_upload.return_value = fake_cloudinary_upload_result(type="authenticated")
        data = {"vehicle_type": "moto", "id_document": make_test_image_file()}
        self.client.post("/api/me/apply-courier/", data, format="multipart")
        response = self.client.post(
            "/api/me/apply-courier/",
            {"vehicle_type": "moto", "id_document": make_test_image_file()},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.courier_application_status, "pending")

    def test_rejects_missing_id_document(self):
        response = self.client.post("/api/me/apply-courier/", {"vehicle_type": "moto"})
        self.assertEqual(response.status_code, 400)

    def test_rejects_invalid_image_without_calling_cloudinary(self):
        # Un fichier qui n'est pas une vraie image doit être rejeté par la validation AVANT
        # tout appel réseau à Cloudinary (voir common/images/validators.py) : ne coûte donc
        # aucun crédit du plan gratuit.
        bad_file = SimpleUploadedFile(
            "doc.jpg", b"ceci n'est pas une image", content_type="image/jpeg"
        )
        with patch("cloudinary.uploader.upload") as mock_upload:
            response = self.client.post(
                "/api/me/apply-courier/",
                {"vehicle_type": "moto", "id_document": bad_file},
                format="multipart",
            )
        self.assertEqual(response.status_code, 400)
        mock_upload.assert_not_called()

    def test_already_courier_short_circuits(self):
        self.user.is_courier = True
        self.user.save(update_fields=["is_courier"])
        response = self.client.post("/api/me/apply-courier/")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.courier_application_status, "none")

    @patch("cloudinary.uploader.upload")
    def test_returns_502_when_cloudinary_upload_fails(self, mock_upload):
        from cloudinary.exceptions import Error as CloudinaryError

        mock_upload.side_effect = CloudinaryError("panne réseau simulée")
        response = self.client.post(
            "/api/me/apply-courier/",
            {"vehicle_type": "moto", "id_document": make_test_image_file()},
            format="multipart",
        )
        self.assertEqual(response.status_code, 502)
        self.user.refresh_from_db()
        # Rien n'a été persisté : ni le statut de candidature, ni le document.
        self.assertEqual(self.user.courier_application_status, "none")

    @patch("cloudinary.uploader.upload")
    def test_courier_id_document_serializes_to_a_url_string(self, mock_upload):
        # `courier_id_document` est servi en URL SIGNÉE (voir users/serializers.py,
        # signed=True) : sans identifiants Cloudinary réels dans l'environnement de test, la
        # signature échouerait sinon avec "Must supply api_secret".
        self.addCleanup(use_test_cloudinary_credentials())
        mock_upload.return_value = fake_cloudinary_upload_result(type="authenticated")
        self.client.post(
            "/api/me/apply-courier/",
            {"vehicle_type": "moto", "id_document": make_test_image_file()},
            format="multipart",
        )
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["courier_id_document"], str)
        self.assertTrue(response.data["courier_id_document"])

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
