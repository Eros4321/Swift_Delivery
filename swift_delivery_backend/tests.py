import json
import shutil
import tempfile
from io import BytesIO
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from .forms import MenuItemAdminForm
from .models import (
    CafeteriaCategory,
    Customer,
    CustomerAddress,
    MenuItem,
    Order,
    University,
    Vendor,
)


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
            {'preferred_university_id': self.university.id},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['preferred_university'],
            {'id': self.university.id, 'name': self.university.name},
        )
        self.assertNotIn('preferred_university_id', response.data)

        login_response = self.client.post(
            '/api/auth/customer/login/',
            {'phone_number': '08012345678'},
            format='json',
        )

        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            login_response.data['customer']['preferred_university'],
            {'id': self.university.id, 'name': self.university.name},
        )

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

        logout_response = self.client.post('/api/auth/customer/logout/')

        self.assertEqual(logout_response.status_code, status.HTTP_204_NO_CONTENT)

        profile_response_after_logout = self.client.get('/api/auth/customer/me/')

        self.assertEqual(
            profile_response_after_logout.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_logout_requires_authentication(self):
        response = self.client.post('/api/auth/customer/logout/')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

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

    def test_customer_can_persist_and_clear_cart_notes(self):
        self.authenticate_customer()
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='2500.00')

        update_response = self.client.patch(
            '/api/cart/',
            {'notes': 'No onions, please'},
            format='json',
        )

        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(update_response.data['notes'], 'No onions, please')
        self.assertEqual(
            self.client.get('/api/cart/').data['notes'],
            'No onions, please',
        )

        add_response = self.client.post(
            '/api/cart/',
            {'menu_item': menu_item.id, 'quantity': 1},
            format='json',
        )

        self.assertEqual(add_response.data['notes'], 'No onions, please')

        clear_response = self.client.delete('/api/cart/')

        self.assertEqual(clear_response.status_code, status.HTTP_200_OK)
        self.assertEqual(clear_response.data['notes'], '')

    def test_customer_can_manage_multiple_saved_cart_notes(self):
        self.authenticate_customer()

        first_response = self.client.post(
            '/api/cart/saved-notes/',
            {'note': 'No onions, please'},
            format='json',
        )
        second_response = self.client.post(
            '/api/cart/saved-notes/',
            {'note': 'Call me at the hostel gate'},
            format='json',
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_201_CREATED)

        list_response = self.client.get('/api/cart/saved-notes/')

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {saved_note['note'] for saved_note in list_response.data},
            {'No onions, please', 'Call me at the hostel gate'},
        )

        update_response = self.client.patch(
            f"/api/cart/saved-notes/{first_response.data['id']}/",
            {'note': 'No onions or pepper, please'},
            format='json',
        )

        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(update_response.data['note'], 'No onions or pepper, please')

        delete_response = self.client.delete(
            f"/api/cart/saved-notes/{second_response.data['id']}/"
        )

        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(len(self.client.get('/api/cart/saved-notes/').data), 1)

    def test_customer_cannot_access_another_customers_saved_cart_note(self):
        self.authenticate_customer()
        saved_note_response = self.client.post(
            '/api/cart/saved-notes/',
            {'note': 'Deliver to my faculty'},
            format='json',
        )

        second_signup_response = self.client.post(
            '/api/auth/customer/signup/',
            {
                'phone_number': '09012345678',
                'first_name': 'Bola',
                'last_name': 'Adeniyi',
                'email': 'bola@example.com',
            },
            format='json',
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Token {second_signup_response.data['token']}"
        )

        self.assertEqual(self.client.get('/api/cart/saved-notes/').data, [])
        detail_response = self.client.get(
            f"/api/cart/saved-notes/{saved_note_response.data['id']}/"
        )
        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)

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


class CatalogOrderingTests(APITestCase):
    def test_vendor_menu_groups_follow_category_then_item_creation_order(self):
        main_dishes = CafeteriaCategory.objects.create(name='Main Dishes')
        snacks = CafeteriaCategory.objects.create(name='Snacks')
        drinks = CafeteriaCategory.objects.create(name='Drinks')
        vendor = Vendor.objects.create(name='Swift Cafeteria')

        snack = MenuItem.objects.create(
            name='Chin Chin',
            price='500.00',
            category=snacks,
        )
        second_main_dish = MenuItem.objects.create(
            name='Fried Rice',
            price='1800.00',
            category=main_dishes,
        )
        first_main_dish = MenuItem.objects.create(
            name='Jollof Rice',
            price='1500.00',
            category=main_dishes,
        )
        drink = MenuItem.objects.create(
            name='Water',
            price='300.00',
            category=drinks,
        )
        vendor.menu_items.add(snack, second_main_dish, first_main_dish, drink)

        response = self.client.get(f'/api/vendors/{vendor.id}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [item['name'] for item in response.data['menu_items']],
            ['Fried Rice', 'Jollof Rice', 'Chin Chin', 'Water'],
        )

    def test_catalog_models_declare_stable_default_ordering(self):
        self.assertEqual(Vendor._meta.ordering, ['id'])
        self.assertEqual(CafeteriaCategory._meta.ordering, ['id'])
        self.assertEqual(MenuItem._meta.ordering, ['category_id', 'id'])


class VendorLogoTests(APITestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.vendor = Vendor.objects.create(name='Swift Cafeteria')
        self.staff_user = User.objects.create_user(
            username='catalog-admin',
            password='test-password',
            is_staff=True,
            is_superuser=True,
        )

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_root)

    @staticmethod
    def uploaded_logo():
        buffer = BytesIO()
        Image.new('RGBA', (4, 4), (255, 0, 0, 0)).save(buffer, format='PNG')
        return SimpleUploadedFile(
            'swift-logo.png',
            buffer.getvalue(),
            content_type='image/png',
        )

    def test_anonymous_user_cannot_change_vendor_logo(self):
        response = self.client.patch(
            f'/api/vendors/{self.vendor.id}/',
            {'logo': self.uploaded_logo()},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.vendor.refresh_from_db()
        self.assertFalse(self.vendor.logo)

    def test_staff_user_can_upload_logo_and_api_returns_its_url(self):
        self.client.force_authenticate(user=self.staff_user)

        response = self.client.patch(
            f'/api/vendors/{self.vendor.id}/',
            {'logo': self.uploaded_logo()},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('/media/vendor_logos/swift-logo', response.data['logo'])
        self.vendor.refresh_from_db()
        self.assertTrue(self.vendor.logo.name.startswith('vendor_logos/swift-logo'))

    def test_django_admin_vendor_form_contains_logo_upload(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(
            f'/admin/swift_delivery_backend/vendor/{self.vendor.id}/change/'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, 'name="logo"')


@override_settings(REMOVE_BG_API_KEY='test-remove-bg-key')
class MenuItemBackgroundRemovalTests(APITestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.processed_png = self.make_image_bytes('PNG', 'RGBA')

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.media_root)

    @staticmethod
    def make_image_bytes(image_format='JPEG', mode='RGB'):
        buffer = BytesIO()
        color = (255, 0, 0, 0) if mode == 'RGBA' else (255, 0, 0)
        Image.new(mode, (2, 2), color).save(buffer, format=image_format)
        return buffer.getvalue()

    def uploaded_image(self, name='jollof.jpg'):
        return SimpleUploadedFile(
            name,
            self.make_image_bytes(),
            content_type='image/jpeg',
        )

    @staticmethod
    def successful_remove_bg_response(content):
        return Mock(status_code=200, content=content, text='')

    @patch('swift_delivery_backend.background_removal.requests.post')
    def test_create_automatically_saves_processed_transparent_png(self, post):
        post.return_value = self.successful_remove_bg_response(self.processed_png)

        response = self.client.post(
            '/api/menu-items/',
            {
                'name': 'Jollof Rice',
                'price': '1500.00',
                'image': self.uploaded_image(),
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        menu_item = MenuItem.objects.get()
        self.assertTrue(menu_item.image.name.endswith('jollof-no-bg.png'))
        with menu_item.image.open('rb') as stored_image:
            self.assertEqual(stored_image.read(), self.processed_png)

        request = post.call_args.kwargs
        self.assertEqual(request['headers'], {'X-Api-Key': 'test-remove-bg-key'})
        self.assertEqual(
            request['data'],
            {'size': 'auto', 'format': 'png', 'type': 'product'},
        )
        self.assertEqual(request['files']['image_file'][0], 'jollof.jpg')

    @patch('swift_delivery_backend.background_removal.requests.post')
    def test_jfif_upload_is_sent_to_provider_as_jpeg(self, post):
        def assert_standard_jpeg(*args, **kwargs):
            request_file = kwargs['files']['image_file']
            self.assertEqual(request_file[0], 'jollof.jpg')
            self.assertEqual(request_file[2], 'image/jpeg')
            with Image.open(request_file[1]) as provider_image:
                provider_image.load()
                self.assertEqual(provider_image.format, 'JPEG')
                self.assertEqual(provider_image.mode, 'RGB')
                self.assertFalse(provider_image.info.get('progressive', False))
            return self.successful_remove_bg_response(self.processed_png)

        post.side_effect = assert_standard_jpeg
        source_buffer = BytesIO()
        Image.new('RGB', (2, 2), (255, 0, 0)).save(
            source_buffer,
            format='JPEG',
            progressive=True,
        )
        uploaded_image = SimpleUploadedFile(
            'jollof.jfif',
            source_buffer.getvalue(),
            content_type='image/jpeg',
        )

        response = self.client.post(
            '/api/menu-items/',
            {
                'name': 'Jollof Rice',
                'price': '1500.00',
                'image': uploaded_image,
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(MenuItem.objects.get().image.name.endswith('jollof-no-bg.png'))

    @patch('swift_delivery_backend.background_removal.requests.post')
    def test_replacing_an_image_also_removes_its_background(self, post):
        post.return_value = self.successful_remove_bg_response(self.processed_png)
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='1500.00')

        response = self.client.patch(
            f'/api/menu-items/{menu_item.id}/',
            {'image': self.uploaded_image('updated.jpg')},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        menu_item.refresh_from_db()
        self.assertTrue(menu_item.image.name.endswith('updated-no-bg.png'))
        post.assert_called_once()

    @patch('swift_delivery_backend.background_removal.requests.post')
    def test_update_without_an_image_does_not_call_background_removal(self, post):
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='1500.00')

        response = self.client.patch(
            f'/api/menu-items/{menu_item.id}/',
            {'available': False},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        post.assert_not_called()

    @patch('swift_delivery_backend.background_removal.requests.post')
    def test_provider_failure_rejects_the_unprocessed_image(self, post):
        post.return_value = Mock(
            status_code=402,
            content=b'',
            text='Insufficient credits',
        )

        response = self.client.post(
            '/api/menu-items/',
            {
                'name': 'Jollof Rice',
                'price': '1500.00',
                'image': self.uploaded_image(),
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            str(response.data['image'][0]),
            'The background removal quota has been exhausted.',
        )
        self.assertFalse(MenuItem.objects.exists())

    @patch('swift_delivery_backend.background_removal.requests.post')
    def test_unknown_foreground_returns_specific_photo_guidance(self, post):
        provider_response = Mock(
            status_code=400,
            content=b'',
            text='Could not identify foreground',
        )
        provider_response.json.return_value = {
            'errors': [
                {
                    'title': 'Could not identify foreground in image.',
                    'code': 'unknown_foreground',
                }
            ]
        }
        post.return_value = provider_response

        response = self.client.post(
            '/api/menu-items/',
            {
                'name': 'Jollof Rice',
                'price': '1500.00',
                'image': self.uploaded_image(),
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('No clear food item', str(response.data['image'][0]))
        self.assertFalse(MenuItem.objects.exists())

    @override_settings(REMOVE_BG_API_KEY='')
    @patch('swift_delivery_backend.background_removal.requests.post')
    def test_missing_api_key_rejects_the_unprocessed_image(self, post):
        response = self.client.post(
            '/api/menu-items/',
            {
                'name': 'Jollof Rice',
                'price': '1500.00',
                'image': self.uploaded_image(),
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('REMOVE_BG_API_KEY', str(response.data['image'][0]))
        self.assertFalse(MenuItem.objects.exists())
        post.assert_not_called()

    @patch('swift_delivery_backend.forms.remove_image_background')
    def test_admin_form_processes_new_menu_images(self, remove_background):
        remove_background.return_value = ContentFile(
            self.processed_png,
            name='jollof-no-bg.png',
        )
        form = MenuItemAdminForm(
            data={
                'name': 'Jollof Rice',
                'price': '1500.00',
                'available': True,
            },
            files={'image': self.uploaded_image()},
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['image'].name, 'jollof-no-bg.png')
        remove_background.assert_called_once()
