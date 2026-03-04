from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from unittest.mock import patch

User = get_user_model()

class AuthTests(APITestCase):
    
    def setUp(self):
        self.register_url = reverse('register')
        self.otp_request_url = reverse('otp-request')
        self.user_data = {
            "phone_number": "+237670000000",
            "first_name": "Test",
            "last_name": "User",
            "password": "securepassword123"
        }

    # le paquet racine s'appelle simplement `users` dans ce projet (pas `apps`).
    # on cible donc le module réel où la classe est définie.
    @patch('users.services.otpservice.OTPService.generate_otp')
    def test_registration_success(self, mock_otp):
        """Vérifie la création d'utilisateur et l'appel de l'OTP"""
        mock_otp.return_value = "123456"
        response = self.client.post(self.register_url, self.user_data)
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(phone_number="+237670000000").exists())
        # On vérifie que la fonction de génération d'OTP a bien été appelée
        mock_otp.assert_called_once()

    def test_otp_verification_flow(self):
        """Test complet du flux de connexion via OTP (Mocké en cache)"""
        # 1. Créer l'utilisateur
        # `username` n'existe pas sur notre modèle personnalisé
        user = User.objects.create_user(phone_number="+237680000000", first_name="Verify User", last_name="Test", password="anothersecurepassword")
        
        # 2. Simuler un code en cache (concept d'injection de dépendance)
        from django.core.cache import cache
        cache.set("otp_+237680000000", "654321", timeout=300)
        
        # 3. Vérifier le code via l'API
        verify_url = reverse('otp-verify')
        response = self.client.post(verify_url, {
            "phone_number": "+237680000000",
            "otp_code": "654321"
        })
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data) # Le token JWT est présent