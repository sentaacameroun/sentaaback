import json
from datetime import timedelta
from unittest.mock import patch

import firebase_admin
from django.core import mail
from django.test import TestCase
from django.utils import timezone
from firebase_admin import exceptions as firebase_exceptions
from firebase_admin import messaging
from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from escrow.models import Order
from jobs.models import JobApplication
from jobs.models import JobOffer
from marketplace.models import Category
from marketplace.models import Listing
from notifications.emails import send_application_reminder
from notifications.emails import send_newsletter
from notifications.emails import send_payment_reminder
from notifications.models import DeviceToken
from notifications.services.push import _ensure_initialized
from notifications.services.push import send_push
from notifications.services.unsubscribe import build_unsubscribe_url
from notifications.services.unsubscribe import generate_unsubscribe_token
from notifications.tasks import check_pending_escrow_payments
from notifications.tasks import check_pending_job_applications
from notifications.tasks import check_pending_reception_confirmations
from notifications.tasks import send_push_notification_task
from notifications.tasks import send_weekly_newsletter
from users.models import User


class NotificationTasksTests(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(
            phone_number="+237611400000",
            first_name="Acheteur",
            last_name="Test",
            email="buyer@example.com",
        )
        self.seller = User.objects.create_user(
            phone_number="+237622400000",
            first_name="Vendeur",
            last_name="Test",
            email="seller@example.com",
        )
        self.cat = Category.objects.create(name="Test", slug="test")
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Objet",
            description="desc",
            price=1000,
            category=self.cat,
        )

    def test_check_pending_escrow_payments_sends_and_dedupes(self):
        old = timezone.now() - timedelta(hours=48)
        order = Order.objects.create(
            buyer=self.buyer,
            listing=self.listing,
            item_price=1000,
            service_fee=30,
            total_amount=1030,
            status="pending",
        )
        Order.objects.filter(id=order.id).update(created_at=old)

        sent = check_pending_escrow_payments()
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        order.refresh_from_db()
        self.assertIsNotNone(order.payment_reminder_sent_at)

        # deuxième passage : déjà tamponné, aucun nouvel envoi
        sent_again = check_pending_escrow_payments()
        self.assertEqual(sent_again, 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_check_pending_reception_confirmations(self):
        # L'acheteur ne peut confirmer la réception qu'à partir de `delivered` (flux en
        # deux étapes, PR 3) : c'est donc ce statut que le rappel doit cibler.
        old = timezone.now() - timedelta(days=10)
        order = Order.objects.create(
            buyer=self.buyer,
            listing=self.listing,
            item_price=1000,
            service_fee=30,
            total_amount=1030,
            status="delivered",
        )
        Order.objects.filter(id=order.id).update(updated_at=old)

        sent = check_pending_reception_confirmations()
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        order.refresh_from_db()
        self.assertIsNotNone(order.reception_reminder_sent_at)

    def test_shipped_order_not_reminded_for_reception(self):
        # Régression : une commande encore `shipped` (coursier n'a pas confirmé la
        # livraison) ne doit PAS déclencher de rappel de réception — l'acheteur ne peut
        # pas encore confirmer (confirm_reception renverrait 400).
        old = timezone.now() - timedelta(days=10)
        order = Order.objects.create(
            buyer=self.buyer,
            listing=self.listing,
            item_price=1000,
            service_fee=30,
            total_amount=1030,
            status="shipped",
        )
        Order.objects.filter(id=order.id).update(updated_at=old)

        sent = check_pending_reception_confirmations()
        self.assertEqual(sent, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_check_pending_job_applications(self):
        job = JobOffer.objects.create(
            recruiter=self.seller,
            title="Dev",
            company_name="Senta'a",
            description="desc",
        )
        old = timezone.now() - timedelta(days=10)
        application = JobApplication.objects.create(
            job=job, applicant=self.buyer, message="Bonjour"
        )
        JobApplication.objects.filter(id=application.id).update(applied_at=old)

        sent = check_pending_job_applications()
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        application.refresh_from_db()
        self.assertIsNotNone(application.reminder_sent_at)

    @patch("notifications.tasks.send_push_notification_task.delay")
    def test_check_pending_escrow_payments_also_sends_push(self, mock_delay):
        """BE-PUSH-3 : le rappel de paiement déclenche le push EN PLUS de l'email
        existant, jamais à la place."""
        old = timezone.now() - timedelta(hours=48)
        order = Order.objects.create(
            buyer=self.buyer,
            listing=self.listing,
            item_price=1000,
            service_fee=30,
            total_amount=1030,
            status="pending",
        )
        Order.objects.filter(id=order.id).update(created_at=old)

        with self.captureOnCommitCallbacks(execute=True):
            sent = check_pending_escrow_payments()

        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)  # canal existant conservé
        mock_delay.assert_called_once_with(
            user_id=self.buyer.id,
            title="Rappel de paiement",
            body="Ta commande est en attente de paiement",
            data={"type": "order", "id": str(order.id)},
        )

    def test_check_pending_escrow_payments_without_active_token_does_not_break_reminder(
        self,
    ):
        # Acheteur sans DeviceToken actif : le rappel doit se comporter exactement comme
        # avant (email seul). On ne mocke pas `send_push` — `.delay()` est remplacé par un
        # appel direct à la tâche réelle (comme dans SendPushNotificationTaskTests) pour
        # exercer le vrai chemin de code, sans dépendre d'un broker Celery dans les tests.
        # Le service (BE-PUSH-1) doit déjà gérer 0 destinataire comme un no-op : ce test
        # vérifie que c'est bien le cas, sans dupliquer cette garde dans la tâche de rappel.
        old = timezone.now() - timedelta(hours=48)
        order = Order.objects.create(
            buyer=self.buyer,
            listing=self.listing,
            item_price=1000,
            service_fee=30,
            total_amount=1030,
            status="pending",
        )
        Order.objects.filter(id=order.id).update(created_at=old)

        with patch(
            "notifications.tasks.send_push_notification_task.delay",
            side_effect=send_push_notification_task,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                sent = check_pending_escrow_payments()

        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        order.refresh_from_db()
        self.assertIsNotNone(order.payment_reminder_sent_at)

    @patch("notifications.tasks.send_push_notification_task.delay")
    def test_check_pending_reception_confirmations_also_sends_push(self, mock_delay):
        old = timezone.now() - timedelta(days=10)
        order = Order.objects.create(
            buyer=self.buyer,
            listing=self.listing,
            item_price=1000,
            service_fee=30,
            total_amount=1030,
            status="delivered",
        )
        Order.objects.filter(id=order.id).update(updated_at=old)

        with self.captureOnCommitCallbacks(execute=True):
            sent = check_pending_reception_confirmations()

        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)  # canal existant conservé
        mock_delay.assert_called_once_with(
            user_id=self.buyer.id,
            title="Rappel de réception",
            body="Confirme la réception de ta commande",
            data={"type": "order", "id": str(order.id)},
        )

    @patch("notifications.tasks.send_push_notification_task.delay")
    def test_check_pending_job_applications_also_sends_push(self, mock_delay):
        job = JobOffer.objects.create(
            recruiter=self.seller,
            title="Dev",
            company_name="Senta'a",
            description="desc",
        )
        old = timezone.now() - timedelta(days=10)
        application = JobApplication.objects.create(
            job=job, applicant=self.buyer, message="Bonjour"
        )
        JobApplication.objects.filter(id=application.id).update(applied_at=old)

        with self.captureOnCommitCallbacks(execute=True):
            sent = check_pending_job_applications()

        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)  # canal existant conservé
        mock_delay.assert_called_once_with(
            user_id=self.seller.id,
            title="Candidatures en attente",
            body="Candidatures en attente pour Dev",
            data={"type": "job_offer", "id": str(job.id)},
        )

    def test_weekly_newsletter_only_to_opted_in_users_with_email(self):
        Listing.objects.create(
            seller=self.seller,
            title="Nouvel objet",
            description="d",
            price=500,
            category=self.cat,
        )
        User.objects.create_user(
            phone_number="+237633400000", first_name="NoEmail", last_name="Test"
        )  # pas d'email -> exclu
        User.objects.create_user(
            phone_number="+237644400000",
            first_name="OptOut",
            last_name="Test",
            email="optout@example.com",
            newsletter_opt_in=False,
        )  # désinscrit -> exclu

        sent = send_weekly_newsletter()
        self.assertEqual(
            sent, 2
        )  # buyer + seller (les deux ont un email et newsletter_opt_in=True)


class SendPushServiceTests(TestCase):
    """notifications/services/push.py::send_push — pas d'appel réseau réel : le SDK Firebase
    (`messaging.send`) est mocké, seule l'API v1 (`firebase_admin.messaging`) est exercée.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+237611500000",
            first_name="Ali",
            last_name="Test",
            email="ali@example.com",
        )

    def test_send_push_without_active_token_is_a_noop(self):
        DeviceToken.objects.create(
            user=self.user, token="tok-inactive", platform="android", active=False
        )
        with patch("notifications.services.push.messaging.send") as mock_send:
            sent = send_push(self.user, "Titre", "Corps")
        mock_send.assert_not_called()
        self.assertEqual(sent, 0)

    @patch("notifications.services.push._ensure_initialized")
    @patch("notifications.services.push.messaging.send")
    def test_send_push_calls_v1_api_for_active_token(self, mock_send, mock_init):
        mock_send.return_value = "projects/x/messages/1"
        token = DeviceToken.objects.create(
            user=self.user, token="tok-1", platform="android"
        )

        sent = send_push(
            self.user,
            "Nouveau message",
            "Tu as reçu un message",
            data={"order_id": "42"},
        )

        mock_init.assert_called_once()
        self.assertEqual(sent, 1)
        mock_send.assert_called_once()
        (message,), _ = mock_send.call_args
        # API v1 : un objet `messaging.Message` (jamais un payload de l'ancienne API legacy
        # `fcm.googleapis.com/fcm/send`, désactivée depuis juin 2024).
        self.assertIsInstance(message, messaging.Message)
        self.assertEqual(message.token, token.token)
        self.assertEqual(message.notification.title, "Nouveau message")
        self.assertEqual(message.notification.body, "Tu as reçu un message")
        self.assertEqual(message.data, {"order_id": "42"})

    @patch("notifications.services.push._ensure_initialized")
    @patch("notifications.services.push.messaging.send")
    def test_send_push_deactivates_unregistered_token(self, mock_send, mock_init):
        mock_send.side_effect = messaging.UnregisteredError("token désinscrit")
        token = DeviceToken.objects.create(
            user=self.user, token="tok-dead", platform="ios"
        )

        sent = send_push(self.user, "Titre", "Corps")

        self.assertEqual(sent, 0)
        token.refresh_from_db()
        self.assertFalse(token.active)

    @patch("notifications.services.push._ensure_initialized")
    @patch("notifications.services.push.messaging.send")
    def test_dead_token_does_not_block_other_devices(self, mock_send, mock_init):
        dead = DeviceToken.objects.create(
            user=self.user, token="tok-dead", platform="android"
        )
        alive = DeviceToken.objects.create(
            user=self.user, token="tok-alive", platform="ios"
        )

        def fake_send(message):
            if message.token == dead.token:
                raise messaging.UnregisteredError("token désinscrit")
            return "projects/x/messages/2"

        mock_send.side_effect = fake_send

        sent = send_push(self.user, "Titre", "Corps")

        self.assertEqual(sent, 1)
        dead.refresh_from_db()
        alive.refresh_from_db()
        self.assertFalse(dead.active)
        self.assertTrue(alive.active)

    @patch("notifications.services.push._ensure_initialized")
    @patch("notifications.services.push.messaging.send")
    def test_send_push_transient_firebase_error_does_not_deactivate_token(
        self, mock_send, mock_init
    ):
        # Une erreur FCM qui n'indique pas un token mort (ex. service indisponible) ne doit
        # jamais désactiver le token — sinon un incident FCM temporaire viderait la table.
        mock_send.side_effect = firebase_exceptions.InternalError("indisponible")
        token = DeviceToken.objects.create(
            user=self.user, token="tok-flaky", platform="android"
        )

        sent = send_push(self.user, "Titre", "Corps")

        self.assertEqual(sent, 0)
        token.refresh_from_db()
        self.assertTrue(token.active)


class EnsureFirebaseInitializedTests(TestCase):
    """notifications/services/push.py::_ensure_initialized — `GOOGLE_APPLICATION_CREDENTIALS`
    contient le JSON complet du compte de service (jamais un chemin de fichier, voir
    .env.example) : pas de fichier de credentials à déposer sur le VPS."""

    def tearDown(self):
        # `firebase_admin._apps` est un singleton process-wide (comme cloudinary.config()) :
        # nettoyage défensif si un test en venait à initialiser une vraie app.
        firebase_admin._apps.clear()

    @patch("notifications.services.push.firebase_admin.initialize_app")
    @patch("notifications.services.push.credentials.Certificate")
    def test_ensure_initialized_parses_json_content(
        self, mock_certificate, mock_initialize_app
    ):
        creds_dict = {"type": "service_account", "project_id": "sentaa-test"}
        with self.settings(GOOGLE_APPLICATION_CREDENTIALS=json.dumps(creds_dict)):
            _ensure_initialized()

        # Le dict *parsé*, pas la chaîne JSON brute ni un chemin de fichier.
        mock_certificate.assert_called_once_with(creds_dict)
        mock_initialize_app.assert_called_once_with(mock_certificate.return_value)

    def test_ensure_initialized_raises_without_credentials(self):
        with self.settings(GOOGLE_APPLICATION_CREDENTIALS=""):
            with self.assertRaises(RuntimeError):
                _ensure_initialized()

    def test_ensure_initialized_rejects_a_file_path(self):
        # Régression : l'ancienne convention (chemin de fichier) ne doit plus être acceptée
        # silencieusement — un chemin n'est pas du JSON valide, l'échec doit être explicite.
        with self.settings(
            GOOGLE_APPLICATION_CREDENTIALS="/etc/secrets/firebase-service-account.json"
        ):
            with self.assertRaises(RuntimeError):
                _ensure_initialized()


class SendPushNotificationTaskTests(TestCase):
    """La tâche Celery est l'unique point d'entrée pour déclencher un envoi (voir
    .claude/rules/push-notifications.md) : elle délègue au service, jamais l'inverse."""

    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+237611600000",
            first_name="Ivy",
            last_name="Test",
            email="ivy@example.com",
        )

    @patch("notifications.tasks.send_push")
    def test_task_delegates_to_service_for_active_user(self, mock_send_push):
        mock_send_push.return_value = 1

        result = send_push_notification_task(
            str(self.user.id), "Titre", "Corps", data={"a": "b"}
        )

        mock_send_push.assert_called_once_with(
            self.user, "Titre", "Corps", data={"a": "b"}
        )
        self.assertEqual(result, 1)

    @patch("notifications.tasks.send_push")
    def test_task_is_a_noop_for_unknown_user(self, mock_send_push):
        result = send_push_notification_task(
            "00000000-0000-0000-0000-000000000000", "Titre", "Corps"
        )

        mock_send_push.assert_not_called()
        self.assertEqual(result, 0)

    @patch("notifications.tasks.send_push")
    def test_task_is_a_noop_for_inactive_user(self, mock_send_push):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        result = send_push_notification_task(self.user.id, "Titre", "Corps")

        mock_send_push.assert_not_called()
        self.assertEqual(result, 0)


class RegisterDeviceViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+237611700000",
            first_name="Reg",
            last_name="Test",
            email="reg@example.com",
        )
        self.other_user = User.objects.create_user(
            phone_number="+237611700001",
            first_name="Other",
            last_name="Test",
            email="other@example.com",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_register_device_creates_token(self):
        response = self.client.post(
            "/api/notifications/register-device/",
            {"token": "fcm-token-abc", "platform": "android", "device_id": "phone-1"},
        )

        self.assertEqual(response.status_code, 200)
        token = DeviceToken.objects.get(token="fcm-token-abc")
        self.assertEqual(token.user, self.user)
        self.assertEqual(token.platform, "android")
        self.assertTrue(token.active)

    def test_register_device_reassigns_existing_token_to_current_user(self):
        # Un même token FCM (même appareil) peut revenir avec un utilisateur différent
        # (logout/login) : l'entrée est réattribuée et réactivée plutôt que dupliquée.
        DeviceToken.objects.create(
            user=self.other_user, token="fcm-shared", platform="ios", active=False
        )

        response = self.client.post(
            "/api/notifications/register-device/",
            {"token": "fcm-shared", "platform": "ios"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(DeviceToken.objects.count(), 1)
        token = DeviceToken.objects.get(token="fcm-shared")
        self.assertEqual(token.user, self.user)
        self.assertTrue(token.active)

    def test_register_device_rejects_invalid_platform(self):
        response = self.client.post(
            "/api/notifications/register-device/",
            {"token": "fcm-token-xyz", "platform": "windows"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(DeviceToken.objects.filter(token="fcm-token-xyz").exists())

    def test_register_device_requires_authentication(self):
        anon_client = APIClient()
        response = anon_client.post(
            "/api/notifications/register-device/",
            {"token": "fcm-token-anon", "platform": "android"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertFalse(DeviceToken.objects.filter(token="fcm-token-anon").exists())


class UnregisterDeviceViewTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+237611800000",
            first_name="Un",
            last_name="Test",
            email="un@example.com",
        )
        self.other_user = User.objects.create_user(
            phone_number="+237611800001",
            first_name="Other",
            last_name="Test",
            email="other2@example.com",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_unregister_device_deactivates_own_token(self):
        DeviceToken.objects.create(user=self.user, token="fcm-mine", platform="android")

        response = self.client.post(
            "/api/notifications/unregister-device/", {"token": "fcm-mine"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(DeviceToken.objects.get(token="fcm-mine").active)

    def test_unregister_device_does_not_affect_other_users_token(self):
        # Cas d'accès non autorisé : connaître le token d'un tiers ne doit pas permettre de
        # le désactiver (permission objet implicite, scope sur `request.user`).
        other_token = DeviceToken.objects.create(
            user=self.other_user, token="fcm-theirs", platform="ios"
        )

        response = self.client.post(
            "/api/notifications/unregister-device/", {"token": "fcm-theirs"}
        )

        self.assertEqual(
            response.status_code, 200
        )  # idempotent, ne fuite pas l'existence
        other_token.refresh_from_db()
        self.assertTrue(other_token.active)

    def test_unregister_device_unknown_token_is_a_noop(self):
        response = self.client.post(
            "/api/notifications/unregister-device/", {"token": "does-not-exist"}
        )

        self.assertEqual(response.status_code, 200)

    def test_unregister_device_requires_authentication(self):
        anon_client = APIClient()
        response = anon_client.post(
            "/api/notifications/unregister-device/", {"token": "fcm-mine"}
        )

        self.assertEqual(response.status_code, 401)


class EmailTemplatesTests(TestCase):
    """Templates HTML/CSS standardisés (base_email.html) + garde-fous anti-spam de base :
    partie texte brut systématique, aucune variable de template non résolue."""

    def setUp(self):
        self.buyer = User.objects.create_user(
            phone_number="+237611900000",
            first_name="Léa",
            last_name="Test",
            email="lea@example.com",
        )
        self.recruiter = User.objects.create_user(
            phone_number="+237611900001",
            first_name="Marc",
            last_name="Test",
            email="marc@example.com",
        )
        cat = Category.objects.create(name="Test", slug="test-email")
        self.listing = Listing.objects.create(
            seller=self.recruiter,
            title="Vélo",
            description="desc",
            price=15000,
            category=cat,
        )

    def _assert_no_unresolved_template_syntax(self, html):
        # Régression : un bloc {% %} / {{ }} non résolu dans le HTML final indique un nom de
        # variable ou de bloc qui ne correspond à rien (faute de frappe, contexte manquant).
        self.assertNotIn("{{", html)
        self.assertNotIn("{%", html)

    def test_payment_reminder_is_multipart_html_and_text(self):
        order = Order.objects.create(
            buyer=self.buyer,
            listing=self.listing,
            item_price=15000,
            service_fee=300,
            total_amount=15300,
            status="pending",
        )

        send_payment_reminder(order)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.alternatives[0][1], "text/html")
        html = message.alternatives[0][0]
        self._assert_no_unresolved_template_syntax(html)
        # Le montant reste dans l'email (contrairement au push, voir
        # .claude/rules/push-notifications.md — cette règle est spécifique au push, pas à
        # l'email) : l'acheteur a besoin de savoir combien payer.
        self.assertIn("15300", html)
        self.assertIn("Vélo", message.body)  # partie texte non vide et pertinente

    def test_application_reminder_pluralizes_pending_count(self):
        job = JobOffer.objects.create(
            recruiter=self.recruiter, title="Dev", company_name="Senta'a"
        )

        send_application_reminder(job, 1)
        send_application_reminder(job, 3)

        singular_html = mail.outbox[0].alternatives[0][0]
        plural_html = mail.outbox[1].alternatives[0][0]
        self.assertIn("1 candidature ", singular_html)
        self.assertIn("3 candidatures", plural_html)

    def test_newsletter_carries_one_click_unsubscribe_headers(self):
        # Exigé par Gmail/Yahoo pour tout envoi en volume (RFC 8058) — voir
        # notifications/services/unsubscribe.py et notifications/emails.py::send_newsletter.
        send_newsletter(self.buyer, [self.listing], [])

        message = mail.outbox[0]
        self.assertIn("List-Unsubscribe", message.extra_headers)
        self.assertIn(
            "/api/notifications/unsubscribe-newsletter/?token=",
            message.extra_headers["List-Unsubscribe"],
        )
        self.assertEqual(
            message.extra_headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click"
        )
        html = message.alternatives[0][0]
        self._assert_no_unresolved_template_syntax(html)
        self.assertIn("Se désinscrire", message.body)


class UnsubscribeNewsletterViewTests(APITestCase):
    """BE-PUSH-3 n'y touche pas — feature adjacente ajoutée avec le reste du travail email :
    désinscription newsletter en un clic, sans connexion (RFC 8058)."""

    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+237611910000",
            first_name="Nora",
            last_name="Test",
            email="nora@example.com",
            newsletter_opt_in=True,
        )
        self.other_user = User.objects.create_user(
            phone_number="+237611910001",
            first_name="Autre",
            last_name="Test",
            email="autre@example.com",
            newsletter_opt_in=True,
        )
        self.client = (
            APIClient()
        )  # jamais authentifié : le lien doit marcher sans connexion

    def test_valid_token_opts_out_and_shows_confirmation_page(self):
        token = generate_unsubscribe_token(self.user)

        response = self.client.get(
            f"/api/notifications/unsubscribe-newsletter/?token={token}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Senta'a", response.content)
        self.user.refresh_from_db()
        self.assertFalse(self.user.newsletter_opt_in)

    def test_valid_token_does_not_affect_another_user(self):
        # Cas d'accès non autorisé : le token encode un seul utilisateur, jamais un autre.
        token = generate_unsubscribe_token(self.user)

        self.client.get(f"/api/notifications/unsubscribe-newsletter/?token={token}")

        self.other_user.refresh_from_db()
        self.assertTrue(self.other_user.newsletter_opt_in)

    def test_one_click_post_opts_out_without_rendering_a_page(self):
        # RFC 8058 : le client mail poste en tâche de fond, sans jamais afficher de page.
        token = generate_unsubscribe_token(self.user)

        response = self.client.post(
            f"/api/notifications/unsubscribe-newsletter/?token={token}"
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.newsletter_opt_in)

    def test_invalid_token_is_rejected_and_changes_nothing(self):
        response = self.client.get(
            "/api/notifications/unsubscribe-newsletter/?token=garbage"
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.newsletter_opt_in)

    def test_missing_token_is_rejected(self):
        response = self.client.get("/api/notifications/unsubscribe-newsletter/")

        self.assertEqual(response.status_code, 400)

    def test_unsubscribe_is_idempotent(self):
        token = generate_unsubscribe_token(self.user)

        self.client.get(f"/api/notifications/unsubscribe-newsletter/?token={token}")
        response = self.client.get(
            f"/api/notifications/unsubscribe-newsletter/?token={token}"
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.newsletter_opt_in)

    def test_build_unsubscribe_url_round_trips_through_the_endpoint(self):
        url = build_unsubscribe_url(self.user)
        # `build_unsubscribe_url` renvoie une URL absolue (BACKEND_PUBLIC_URL) : on ne garde
        # que le chemin + querystring pour le test client, qui ne connaît que le host de test.
        relative = "/" + url.split("/", 3)[3]

        response = self.client.get(relative)

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.newsletter_opt_in)
