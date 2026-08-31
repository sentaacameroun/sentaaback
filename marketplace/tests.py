from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from common.images.testing import fake_cloudinary_upload_result
from common.images.testing import make_test_image_file
from common.images.testing import use_test_cloudinary_credentials
from marketplace.models import Category
from marketplace.models import Listing
from marketplace.models import ListingFavorite
from marketplace.models import ListingImage
from marketplace.models import Offer
from marketplace.views import MAX_IMAGES_PER_LISTING
from users.models import User


class MarketplaceTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            phone_number="+237600000000",
            password="test",
            first_name="Test",
            last_name="Seller",
        )
        self.category = Category.objects.create(name="Électronique", slug="elec")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("listing-list")  # Assumant router DRF

    def test_create_listing(self):
        data = {
            "title": "iPhone 13",
            "description": "État neuf",
            "price": "500000.00",
            "category": self.category.id,
            "city": "Douala",
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Listing.objects.count(), 1)
        self.assertEqual(Listing.objects.first().seller, self.user)

    def test_creating_a_listing_grants_is_seller(self):
        self.assertFalse(self.user.is_seller)
        data = {
            "title": "iPhone 13",
            "description": "État neuf",
            "price": "500000.00",
            "category": self.category.id,
            "city": "Douala",
        }
        self.client.post(self.url, data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_seller)

    def test_filter_listing_by_city(self):
        Listing.objects.create(
            title="A",
            price=100,
            category=self.category,
            seller=self.user,
            city="Douala",
        )
        Listing.objects.create(
            title="B",
            price=200,
            category=self.category,
            seller=self.user,
            city="Yaoundé",
        )

        response = self.client.get(self.url, {"city": "Douala"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Réponse paginée (DEFAULT_PAGINATION_CLASS) : les résultats sont dans 'results'.
        self.assertEqual(len(response.data["results"]), 1)

    def test_toggle_favorite_is_idempotent_and_scoped_to_user(self):
        listing = Listing.objects.create(
            title="A",
            price=100,
            category=self.category,
            seller=self.user,
            city="Douala",
        )
        buyer = User.objects.create_user(
            phone_number="+237611000001", first_name="B", last_name="T"
        )
        client = APIClient()
        client.force_authenticate(user=buyer)

        response = client.post(f"/api/listings/{listing.id}/toggle_favorite/")
        self.assertEqual(response.data, {"favorited": True})
        self.assertTrue(
            ListingFavorite.objects.filter(user=buyer, listing=listing).exists()
        )

        response = client.post(f"/api/listings/{listing.id}/toggle_favorite/")
        self.assertEqual(response.data, {"favorited": False})
        self.assertFalse(
            ListingFavorite.objects.filter(user=buyer, listing=listing).exists()
        )

        favorites_response = self.client.get("/api/listings/favorites/")
        self.assertEqual(len(favorites_response.data["results"]), 0)

    def _listing_data(self, **overrides):
        data = {
            "title": "iPhone 13",
            "description": "État neuf",
            "price": "500000.00",
            "category": self.category.id,
            "city": "Douala",
        }
        data.update(overrides)
        return data

    @patch("cloudinary.uploader.upload")
    def test_create_listing_with_images_uploads_to_cloudinary(self, mock_upload):
        # La réponse 201 sérialise `images` (voir ListingSerializer), donc construit une URL
        # de délivrance (voir common/images/delivery.py) : nécessite un cloud_name, absent de
        # l'environnement de test (voir CLOUDINARY dans back_sentaa/settings.py).
        self.addCleanup(use_test_cloudinary_credentials())
        mock_upload.return_value = fake_cloudinary_upload_result()
        data = self._listing_data(
            images=[make_test_image_file("a.jpg"), make_test_image_file("b.jpg")]
        )
        response = self.client.post(self.url, data, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        listing = Listing.objects.get(id=response.data["id"])
        self.assertEqual(listing.images.count(), 2)
        # Première image envoyée = image de couverture par défaut (voir
        # marketplace/views.py::_attach_uploaded_images).
        self.assertEqual(listing.images.filter(is_main=True).count(), 1)
        self.assertEqual(mock_upload.call_count, 2)

    def test_rejects_invalid_image_without_calling_cloudinary_and_rolls_back_listing(
        self,
    ):
        # Un fichier invalide dans le lot ne doit ni consommer de quota Cloudinary, ni laisser
        # une annonce orpheline derrière une réponse d'erreur (voir
        # ListingViewSet.perform_create : transaction.atomic()).
        bad_file = SimpleUploadedFile(
            "doc.jpg", b"pas une image", content_type="image/jpeg"
        )
        data = self._listing_data(images=[bad_file])
        with patch("cloudinary.uploader.upload") as mock_upload:
            response = self.client.post(self.url, data, format="multipart")
        self.assertEqual(response.status_code, 400)
        mock_upload.assert_not_called()
        self.assertEqual(Listing.objects.count(), 0)

    def test_rejects_more_than_max_images_per_listing(self):
        data = self._listing_data(
            images=[
                make_test_image_file(f"{i}.jpg")
                for i in range(MAX_IMAGES_PER_LISTING + 1)
            ]
        )
        with patch("cloudinary.uploader.upload") as mock_upload:
            response = self.client.post(self.url, data, format="multipart")
        self.assertEqual(response.status_code, 400)
        mock_upload.assert_not_called()
        self.assertEqual(Listing.objects.count(), 0)

    @patch("cloudinary.uploader.upload")
    def test_deleting_listing_cleans_up_cloudinary_images(self, mock_upload):
        self.addCleanup(use_test_cloudinary_credentials())
        mock_upload.return_value = fake_cloudinary_upload_result(
            public_id="sentaa/listings/x/y"
        )
        response = self.client.post(
            self.url,
            self._listing_data(images=[make_test_image_file("a.jpg")]),
            format="multipart",
        )
        listing = Listing.objects.get(id=response.data["id"])

        with patch("cloudinary.uploader.destroy") as mock_destroy:
            listing.delete()
        mock_destroy.assert_called_once_with(
            "sentaa/listings/x/y", resource_type="image", type="upload"
        )
        self.assertEqual(ListingImage.objects.count(), 0)


class ListingObjectPermissionTests(APITestCase):
    """
    Régression IDOR : avant l'ajout de `IsOwnerSeller`, `ListingViewSet` ne vérifiait
    que le défaut global `IsAuthenticated` et n'importe quel compte connecté pouvait
    modifier/supprimer l'annonce d'un autre vendeur par son id.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            phone_number="+237655000001", first_name="Own", last_name="Er"
        )
        self.other = User.objects.create_user(
            phone_number="+237655000002", first_name="Oth", last_name="Er"
        )
        self.category = Category.objects.create(name="Cat", slug="cat-perm")
        self.listing = Listing.objects.create(
            seller=self.owner,
            title="Vélo",
            description="d",
            price=1000,
            category=self.category,
        )

    def test_non_owner_cannot_update_listing(self):
        client = APIClient()
        client.force_authenticate(user=self.other)
        response = client.patch(
            f"/api/listings/{self.listing.id}/", {"title": "Hacked"}
        )
        self.assertEqual(response.status_code, 403)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.title, "Vélo")

    def test_non_owner_cannot_delete_listing(self):
        client = APIClient()
        client.force_authenticate(user=self.other)
        response = client.delete(f"/api/listings/{self.listing.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Listing.objects.filter(id=self.listing.id).exists())

    def test_owner_can_update_own_listing(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.patch(
            f"/api/listings/{self.listing.id}/", {"title": "Vélo VTT"}
        )
        self.assertEqual(response.status_code, 200)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.title, "Vélo VTT")


class OfferNegotiationTests(APITestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            phone_number="+237622000002", first_name="V", last_name="T"
        )
        self.buyer = User.objects.create_user(
            phone_number="+237611000002", first_name="A", last_name="T"
        )
        self.category = Category.objects.create(name="Test", slug="test-offer")
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Objet",
            description="d",
            price=1000,
            category=self.category,
        )
        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(user=self.buyer)
        self.seller_client = APIClient()
        self.seller_client.force_authenticate(user=self.seller)

    def test_full_negotiation_cycle_accept(self):
        response = self.buyer_client.post(
            "/api/offers/", {"listing": self.listing.id, "proposed_price": "800.00"}
        )
        self.assertEqual(response.status_code, 201)
        offer_id = response.data["id"]
        self.assertEqual(response.data["status"], "pending")

        # Le buyer ne peut pas répondre à sa propre proposition.
        response = self.buyer_client.post(f"/api/offers/{offer_id}/accept/")
        self.assertEqual(response.status_code, 403)

        response = self.seller_client.post(
            f"/api/offers/{offer_id}/counter/", {"proposed_price": "900.00"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "countered")
        self.assertEqual(str(response.data["proposed_price"]), "900.00")

        # Le seller ne peut pas répondre à sa propre contre-offre.
        response = self.seller_client.post(f"/api/offers/{offer_id}/accept/")
        self.assertEqual(response.status_code, 403)

        response = self.buyer_client.post(f"/api/offers/{offer_id}/accept/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "accepted")

        offer = Offer.objects.get(id=offer_id)
        self.assertEqual(offer.status, "accepted")
        self.assertEqual(offer.proposed_price, 900)

    def test_cannot_negotiate_own_listing(self):
        response = self.seller_client.post(
            "/api/offers/", {"listing": self.listing.id, "proposed_price": "800.00"}
        )
        self.assertEqual(response.status_code, 400)

    def test_order_uses_accepted_offer_price(self):
        from escrow.models import Order

        response = self.buyer_client.post(
            "/api/offers/", {"listing": self.listing.id, "proposed_price": "700.00"}
        )
        offer_id = response.data["id"]
        self.seller_client.post(f"/api/offers/{offer_id}/accept/")

        response = self.buyer_client.post(
            "/api/orders/",
            {
                "listing": self.listing.id,
                "destination_latitude": "4.05",
                "destination_longitude": "9.70",
            },
        )
        self.assertEqual(response.status_code, 201)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.item_price, 700)
        self.assertEqual(str(order.offer_id), offer_id)

    @patch("marketplace.views.send_push_notification_task.delay")
    def test_new_offer_notifies_seller(self, mock_delay):
        # BE-PUSH-2 : le vendeur est notifié d'une nouvelle offre sur son annonce.
        with self.captureOnCommitCallbacks(execute=True):
            response = self.buyer_client.post(
                "/api/offers/", {"listing": self.listing.id, "proposed_price": "800.00"}
            )

        self.assertEqual(response.status_code, 201)
        mock_delay.assert_called_once_with(
            user_id=self.seller.id,
            title="Nouvelle offre",
            body="Nouvelle offre sur ton annonce",
            data={"type": "offer", "id": response.data["id"]},
        )

    @patch("marketplace.views.send_push_notification_task.delay")
    def test_offer_accept_notifies_the_party_who_did_not_act(self, mock_delay):
        # BE-PUSH-2 : la contre-offre du seller notifie le buyer (qui n'a pas agi), puis
        # l'acceptation du buyer notifie le seller (qui n'a pas agi) — jamais l'auteur de
        # l'action en cours.
        response = self.buyer_client.post(
            "/api/offers/", {"listing": self.listing.id, "proposed_price": "800.00"}
        )
        offer_id = response.data["id"]
        mock_delay.reset_mock()  # on ignore la notification de création ci-dessus

        with self.captureOnCommitCallbacks(execute=True):
            self.seller_client.post(
                f"/api/offers/{offer_id}/counter/", {"proposed_price": "900.00"}
            )
        mock_delay.assert_called_once_with(
            user_id=self.buyer.id,
            title="Réponse à ta proposition",
            body="Ta proposition a été contre-proposée",
            data={"type": "offer", "id": offer_id},
        )
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            self.buyer_client.post(f"/api/offers/{offer_id}/accept/")
        mock_delay.assert_called_once_with(
            user_id=self.seller.id,
            title="Réponse à ta proposition",
            body="Ta proposition a été acceptée",
            data={"type": "offer", "id": offer_id},
        )
