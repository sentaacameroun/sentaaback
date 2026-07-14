from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from escrow.models import Order
from jobs.models import JobApplication
from jobs.models import JobOffer
from marketplace.models import Category
from marketplace.models import Listing
from notifications.tasks import check_pending_escrow_payments
from notifications.tasks import check_pending_job_applications
from notifications.tasks import check_pending_reception_confirmations
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
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)

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
