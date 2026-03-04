from django.db import models
from users.models import User

class Delivery(models.Model):
    order = models.OneToOneField('escrow.Order', on_delete=models.CASCADE, related_name='delivery')
    courier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, limit_choices_to={'is_courier': True})
    tracking_number = models.CharField(max_length=100, unique=True)
    pickup_point = models.CharField(max_length=255)
    destination = models.TextField()
    
    estimated_arrival = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Delivery for Order {self.order.id} - Courier: {self.courier.first_name if self.courier else 'Unassigned'}"

    class Meta:
        app_label = 'logistics'
        verbose_name = 'Delivery'
        verbose_name_plural = 'Deliveries'
        db_table = 'deliveries'