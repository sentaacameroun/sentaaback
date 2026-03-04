from rest_framework.test import APITestCase
from rest_framework import status
from rest_framework.test import APIClient
from django.urls import reverse
from users.models import User
from marketplace.models import Category, Listing



class MarketplaceTests(APITestCase):
    def setUp(self):
        self.client=APIClient()
        self.user = User.objects.create_user(phone_number="+237600000000", password="test", first_name="Test", last_name="Seller")
        self.category = Category.objects.create(name="Électronique", slug="elec")
        self.client.force_authenticate(user=self.user)
        self.url = reverse('listing-list') # Assumant router DRF

    def test_create_listing(self):
        data = {
            "title": "iPhone 13",
            "description": "État neuf",
            "price": "500000.00",
            "category": self.category.id,
            "city": "Douala"
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Listing.objects.count(), 1)
        self.assertEqual(Listing.objects.first().seller, self.user)

    def test_filter_listing_by_city(self):
        Listing.objects.create(title="A", price=100, category=self.category, seller=self.user, city="Douala")
        Listing.objects.create(title="B", price=200, category=self.category, seller=self.user, city="Yaoundé")
        
        response = self.client.get(self.url, {'city': 'Douala'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)