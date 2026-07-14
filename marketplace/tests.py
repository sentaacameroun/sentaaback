from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from marketplace.models import Category
from marketplace.models import Listing
from marketplace.models import ListingFavorite
from marketplace.models import Offer
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
