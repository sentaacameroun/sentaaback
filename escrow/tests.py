from rest_framework.test import APITestCase
from users.models import User
from marketplace.models import Listing, Category
from .models import Order
from rest_framework.test import APIClient

class EscrowTests(APITestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(phone_number="+237611", first_name="Acheteur")
        self.seller = User.objects.create_user(phone_number="+237622", first_name="Vendeur")
        self.cat = Category.objects.create(name="Test", slug="test")
        self.listing = Listing.objects.create(
            seller=self.seller, title="Objet", price=1000, category=self.cat
        )

    def test_commission_calculation(self):
        self.client=APIClient()
        self.client.force_authenticate(user=self.buyer)
        response = self.client.post('/api/orders/', {"listing": self.listing.id})
        
        order = Order.objects.get(id=response.data[])
        # 1000 + 3% (30) = 1030
        self.assertEqual(order.service_fee, 30)
        self.assertEqual(order.total_amount, 1030)