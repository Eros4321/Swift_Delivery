import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Customer, CustomerAddress, MenuItem, Order, University, Vendor


class CustomerAddressTests(APITestCase):
    def setUp(self):
        signup_response = self.client.post(
            '/api/auth/customer/signup/',
            {
                'phone_number': '08012345678',
                'first_name': 'Ada',
                'last_name': 'Okafor',
                'email': 'ada@example.com',
            },
            format='json',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {signup_response.data['token']}")
        self.university = University.objects.create(
            name='University of Lagos',
            latitude='6.515800',
            longitude='3.389900',
        )

    def create_address(self, label='Hostel', is_default=False):
        return self.client.post(
            '/api/addresses/',
            {
                'university': self.university.id,
                'label': label,
                'address': f'{label}, University of Lagos',
                'provider_place_id': f'google-place-{label.lower()}',
                'latitude': '6.518000',
                'longitude': '3.390000',
                'delivery_instructions': 'Call when you reach the gate',
                'is_default': is_default,
            },
            format='json',
        )

    def test_customer_can_save_and_change_default_addresses(self):
        first_response = self.create_address()
        second_response = self.create_address(label='Faculty', is_default=True)

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(first_response.data['is_default'])
        self.assertTrue(second_response.data['is_default'])
        self.assertFalse(CustomerAddress.objects.get(pk=first_response.data['id']).is_default)

        list_response = self.client.get('/api/addresses/')
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data), 2)

    def test_order_copies_saved_address_as_delivery_snapshot(self):
        address_response = self.create_address()
        address = CustomerAddress.objects.get(pk=address_response.data['id'])
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='2500.00')

        order_response = self.client.post(
            '/api/orders/',
            {
                'customer_address': address.id,
                'order_items': [{'menu_item': menu_item.id, 'quantity': 1}],
            },
            format='json',
        )

        self.assertEqual(order_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(order_response.data['delivery_address'], address.address)
        self.assertEqual(order_response.data['delivery_place_id'], address.provider_place_id)
        self.assertEqual(order_response.data['delivery_latitude'], '6.518000')
        self.assertEqual(order_response.data['university'], self.university.id)

        address.address = 'A changed address'
        address.save(update_fields=['address'])
        order = Order.objects.get(pk=order_response.data['id'])
        self.assertEqual(order.delivery_address, 'Hostel, University of Lagos')

    def test_order_accepts_arbitrary_map_pin_without_saved_address(self):
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='2500.00')
        response = self.client.post(
            '/api/orders/',
            {
                'university': self.university.id,
                'delivery_address': 'Dropped pin beside Senate Building',
                'delivery_latitude': '6.519000',
                'delivery_longitude': '3.391000',
                'delivery_notes': 'Meet me outside',
                'order_items': [{'menu_item': menu_item.id, 'quantity': 1}],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data['customer_address'])
        self.assertEqual(response.data['delivery_longitude'], '3.391000')

    def test_order_rejects_incomplete_coordinates(self):
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='2500.00')
        response = self.client.post(
            '/api/orders/',
            {
                'delivery_latitude': '6.519000',
                'order_items': [{'menu_item': menu_item.id, 'quantity': 1}],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('delivery_location', response.data)

    def test_customer_cannot_access_or_order_with_another_customers_address(self):
        other_user = User.objects.create(username='+2348098765432')
        other_customer = Customer.objects.create(
            user=other_user,
            phone_number='+2348098765432',
        )
        other_address = CustomerAddress.objects.create(
            customer=other_customer,
            university=self.university,
            label='Private address',
            address='Another customer address',
            latitude='6.520000',
            longitude='3.392000',
        )
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='2500.00')

        detail_response = self.client.get(f'/api/addresses/{other_address.id}/')
        order_response = self.client.post(
            '/api/orders/',
            {
                'customer_address': other_address.id,
                'order_items': [{'menu_item': menu_item.id, 'quantity': 1}],
            },
            format='json',
        )

        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(order_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('customer_address', order_response.data)

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_customer_can_search_google_places_within_university(self, mock_urlopen):
        provider_response = {
            'places': [
                {
                    'id': 'nearby-place',
                    'displayName': {'text': 'Moremi Hall'},
                    'formattedAddress': 'Moremi Hall, University of Lagos',
                    'location': {'latitude': 6.518, 'longitude': 3.39},
                },
                {
                    'id': 'faraway-place',
                    'displayName': {'text': 'Far Away'},
                    'formattedAddress': 'Abuja, Nigeria',
                    'location': {'latitude': 9.0765, 'longitude': 7.3986},
                },
            ]
        }
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps(provider_response).encode('utf-8')
        )

        response = self.client.get(
            f'/api/locations/search/?query=Moremi&university_id={self.university.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['place_id'], 'nearby-place')

    @override_settings(GOOGLE_MAPS_API_KEY='')
    def test_location_search_reports_missing_provider_configuration(self):
        response = self.client.get(
            f'/api/locations/search/?query=Moremi&university_id={self.university.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_customer_cannot_save_address_outside_university_delivery_area(self):
        response = self.client.post(
            '/api/addresses/',
            {
                'university': self.university.id,
                'label': 'Too far away',
                'address': 'Abuja, Nigeria',
                'latitude': '9.076500',
                'longitude': '7.398600',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('location', response.data)


class UniversityLocationTests(APITestCase):
    def setUp(self):
        self.university = University.objects.create(
            name='University of Lagos',
            latitude='6.515800',
            longitude='3.389900',
            detection_radius_meters=3000,
        )
    def test_detects_nearby_university(self):
        response = self.client.post(
            '/api/universities/detect/',
            {'latitude': 6.516000, 'longitude': 3.390000},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['university']['id'], self.university.id)
        self.assertNotIn('locations', response.data['university'])
        self.assertLess(response.data['distance_meters'], 100)

    def test_detection_returns_not_found_outside_supported_campus(self):
        response = self.client.post(
            '/api/universities/detect/',
            {'latitude': 9.076500, 'longitude': 7.398600},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_customer_can_save_preferred_university(self):
        signup_response = self.client.post(
            '/api/auth/customer/signup/',
            {
                'phone_number': '08012345678',
                'first_name': 'Ada',
                'last_name': 'Okafor',
                'email': 'ada@example.com',
            },
            format='json',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {signup_response.data['token']}")

        response = self.client.patch(
            '/api/auth/customer/me/',
            {'preferred_university': self.university.id},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['preferred_university'], self.university.id)

class CustomerAuthTests(APITestCase):
    def test_customer_can_signup_login_and_fetch_profile_with_phone_number(self):
        signup_response = self.client.post(
            '/api/auth/customer/signup/',
            {
                'phone_number': '08012345678',
                'first_name': 'Ada',
                'last_name': 'Okafor',
                'email': 'ada@example.com',
            },
            format='json',
        )

        self.assertEqual(signup_response.status_code, status.HTTP_201_CREATED)
        self.assertIn('token', signup_response.data)
        self.assertEqual(signup_response.data['customer']['phone_number'], '+2348012345678')
        self.assertEqual(signup_response.data['customer']['first_name'], 'Ada')

        login_response = self.client.post(
            '/api/auth/customer/login/',
            {'phone_number': '08012345678'},
            format='json',
        )

        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.assertEqual(login_response.data['customer']['phone_number'], '+2348012345678')

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {login_response.data['token']}")
        profile_response = self.client.get('/api/auth/customer/me/')

        self.assertEqual(profile_response.status_code, status.HTTP_200_OK)
        self.assertEqual(profile_response.data['email'], 'ada@example.com')

    def test_directly_created_customer_phone_number_is_normalized_for_login(self):
        user = User.objects.create(
            username='admin-created-customer',
            first_name='Bola',
            last_name='Adeniyi',
            email='bola@example.com',
        )
        customer = Customer.objects.create(user=user, phone_number='09012345678')

        customer.refresh_from_db()
        self.assertEqual(customer.phone_number, '+2349012345678')

        login_response = self.client.post(
            '/api/auth/customer/login/',
            {'phone_number': '09012345678'},
            format='json',
        )

        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.assertEqual(login_response.data['customer']['phone_number'], '+2349012345678')


class CustomerCartTests(APITestCase):
    def authenticate_customer(self):
        response = self.client.post(
            '/api/auth/customer/signup/',
            {
                'phone_number': '08012345678',
                'first_name': 'Ada',
                'last_name': 'Okafor',
                'email': 'ada@example.com',
            },
            format='json',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['token']}")

    def test_customer_can_manage_cart_items(self):
        self.authenticate_customer()
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='2500.00')

        add_response = self.client.post(
            '/api/cart/',
            {
                'menu_item': menu_item.id,
                'quantity': 2,
            },
            format='json',
        )

        self.assertEqual(add_response.status_code, status.HTTP_200_OK)
        self.assertEqual(add_response.data['item_count'], 2)
        self.assertEqual(len(add_response.data['items']), 1)

        cart_item_id = add_response.data['items'][0]['id']
        update_response = self.client.patch(
            f'/api/cart/items/{cart_item_id}/',
            {'quantity': 3},
            format='json',
        )

        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(update_response.data['quantity'], 3)

        cart_response = self.client.get('/api/cart/')

        self.assertEqual(cart_response.status_code, status.HTTP_200_OK)
        self.assertEqual(cart_response.data['item_count'], 3)
        self.assertEqual(cart_response.data['total_amount'], 7500)

        delete_response = self.client.delete(f'/api/cart/items/{cart_item_id}/')

        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.client.get('/api/cart/').data['item_count'], 0)


class CustomerOrderHistoryAndFavoritesTests(APITestCase):
    def authenticate_customer(self):
        response = self.client.post(
            '/api/auth/customer/signup/',
            {
                'phone_number': '08012345678',
                'first_name': 'Ada',
                'last_name': 'Okafor',
                'email': 'ada@example.com',
            },
            format='json',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['token']}")

    def test_authenticated_customer_order_appears_in_order_history(self):
        self.authenticate_customer()
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='2500.00')

        order_response = self.client.post(
            '/api/orders/',
            {
                'customer_name': 'Ada Okafor',
                'phone_number': '08012345678',
                'delivery_address': '12 Lagos Street',
                'order_items': [
                    {
                        'menu_item': menu_item.id,
                        'quantity': 2,
                    }
                ],
            },
            format='json',
        )

        self.assertEqual(order_response.status_code, status.HTTP_201_CREATED)

        history_response = self.client.get('/api/orders/history/')

        self.assertEqual(history_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(history_response.data), 1)
        self.assertEqual(history_response.data[0]['customer_name'], 'Ada Okafor')
        self.assertEqual(history_response.data[0]['items'][0]['menu_item_name'], 'Jollof Rice')

    def test_customer_can_add_list_and_remove_favorite_vendor(self):
        self.authenticate_customer()
        vendor = Vendor.objects.create(name='Swift Cafeteria')

        add_response = self.client.post(
            '/api/favorites/vendors/',
            {'vendor': vendor.id},
            format='json',
        )

        self.assertEqual(add_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(add_response.data['vendor'], vendor.id)
        self.assertEqual(add_response.data['vendor_detail']['name'], 'Swift Cafeteria')

        list_response = self.client.get('/api/favorites/vendors/')

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data), 1)

        delete_response = self.client.delete(f'/api/favorites/vendors/{vendor.id}/')

        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(len(self.client.get('/api/favorites/vendors/').data), 0)
