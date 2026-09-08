import json
import shutil
import tempfile
from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, Mock, patch
from urllib.error import URLError

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory, TransactionTestCase, override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from .forms import MenuItemAdminForm
from .admin import UniversityAdmin
from .location_services import (
    LocationOutsideDeliveryArea,
    normalize_google_place_id,
    validate_coordinates_within_university,
)
from .models import (
    CafeteriaCategory,
    Cart,
    Customer,
    CustomerAddress,
    MenuItem,
    Order,
    SavedCartNote,
    University,
    Vendor,
)
from .widgets import GoogleMapsMultiPolygonWidget


def google_json_response(payload):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(payload).encode(
        'utf-8'
    )
    return response


class CustomerAddressTests(APITestCase):
    def setUp(self):
        cache.clear()
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
            google_place_id='places/root-university-place',
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

    def test_google_place_id_normalization_handles_resource_names(self):
        self.assertEqual(
            normalize_google_place_id('  places/ChIJ-campus  '),
            'ChIJ-campus',
        )
        self.assertEqual(normalize_google_place_id('ChIJ-campus'), 'ChIJ-campus')
        self.assertIsNone(normalize_google_place_id('  places/  '))
        self.assertIsNone(normalize_google_place_id(None))

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

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_search_omits_only_root_place_and_keeps_internal_university_type(
        self,
        mock_urlopen,
    ):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps({
                'places': [
                    {
                        'id': 'root-university-place',
                        'displayName': {'text': 'University of Lagos'},
                        'formattedAddress': 'University Road, Lagos',
                        'location': {'latitude': 6.518, 'longitude': 3.390},
                        'primaryType': 'university',
                        'types': ['university', 'establishment'],
                    },
                    {
                        'id': 'vice-chancellor-office',
                        'displayName': {'text': "Vice Chancellor's Office"},
                        'formattedAddress': 'Inside University of Lagos',
                        'location': {'latitude': 6.5181, 'longitude': 3.3901},
                        'primaryType': 'university',
                        'types': ['university', 'establishment'],
                    },
                ],
            }).encode('utf-8')
        )

        response = self.client.get(
            f'/api/locations/search/?query=University&university_id={self.university.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [result['place_id'] for result in response.data['results']],
            ['vice-chancellor-office'],
        )
        provider_request = mock_urlopen.call_args.args[0]
        self.assertIn('places.id', provider_request.headers['X-goog-fieldmask'])

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_search_retains_current_behavior_without_university_place_id(
        self,
        mock_urlopen,
    ):
        self.university.google_place_id = None
        self.university.save(update_fields=['google_place_id'])
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps({
                'places': [{
                    'id': 'broad-university-result',
                    'displayName': {'text': 'University of Lagos'},
                    'formattedAddress': 'University Road, Lagos',
                    'location': {'latitude': 6.518, 'longitude': 3.390},
                    'primaryType': 'university',
                }],
            }).encode('utf-8')
        )

        response = self.client.get(
            f'/api/locations/search/?query=University&university_id={self.university.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [result['place_id'] for result in response.data['results']],
            ['broad-university-result'],
        )

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_location_search_ranks_named_places_and_deduplicates_place_ids(
        self,
        mock_urlopen,
    ):
        provider_response = {
            'places': [
                {
                    'id': 'plus-code-result',
                    'displayName': {'text': 'MFH4+CRR'},
                    'formattedAddress': 'MFH4+CRR, Ede 232101, Osun, Nigeria',
                    'location': {'latitude': 6.5181, 'longitude': 3.3901},
                    'types': ['plus_code'],
                },
                {
                    'id': 'manna-palace',
                    'displayName': {
                        'text': 'Redeemers University Cafeteria (Manna Palace)'
                    },
                    'formattedAddress': 'MFH4+CRR, Ede 232101, Osun, Nigeria',
                    'location': {'latitude': 6.5182, 'longitude': 3.3902},
                    'primaryType': 'restaurant',
                    'types': ['establishment', 'point_of_interest', 'restaurant'],
                },
                {
                    'id': 'places/manna-palace',
                    'displayName': {'text': 'Manna Palace duplicate'},
                    'formattedAddress': 'Duplicate provider result',
                    'location': {'latitude': 6.5182, 'longitude': 3.3902},
                    'types': ['establishment'],
                },
            ]
        }
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps(provider_response).encode('utf-8')
        )

        response = self.client.get(
            f'/api/locations/search/?query=Manna&university_id={self.university.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'][0]['place_id'], 'manna-palace')
        self.assertEqual(
            response.data['results'][0]['name'],
            'Redeemers University Cafeteria (Manna Palace)',
        )
        self.assertEqual(
            [result['place_id'] for result in response.data['results']].count(
                'manna-palace'
            ),
            1,
        )

        provider_request = mock_urlopen.call_args.args[0]
        request_body = json.loads(provider_request.data.decode('utf-8'))
        self.assertEqual(
            request_body['textQuery'],
            'Manna, University of Lagos',
        )
        self.assertIn('locationBias', request_body)
        self.assertNotIn('locationRestriction', request_body)

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

    def test_saved_address_and_order_reject_exact_university_root_place(self):
        address_response = self.client.post(
            '/api/addresses/',
            {
                'university': self.university.id,
                'label': 'Too broad',
                'address': 'University of Lagos',
                'provider_place_id': 'root-university-place',
                'latitude': '6.518000',
                'longitude': '3.390000',
            },
            format='json',
        )
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='2500.00')
        order_response = self.client.post(
            '/api/orders/',
            {
                'university': self.university.id,
                'delivery_address': 'University of Lagos',
                'delivery_place_id': 'places/root-university-place',
                'delivery_latitude': '6.518000',
                'delivery_longitude': '3.390000',
                'order_items': [{'menu_item': menu_item.id, 'quantity': 1}],
            },
            format='json',
        )

        self.assertEqual(address_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            address_response.data['provider_place_id'][0],
            'Select a specific campus building or delivery point.',
        )
        self.assertEqual(order_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            order_response.data['delivery_place_id'][0],
            'Select a specific campus building or delivery point.',
        )


class AutocompleteLocationSearchTests(APITestCase):
    def setUp(self):
        cache.clear()
        user = User.objects.create_user(username='+2348011111111')
        Customer.objects.create(user=user, phone_number='+2348011111111')
        self.client.force_authenticate(user=user)
        polygon = Polygon(
            (
                (3.385, 6.510),
                (3.395, 6.510),
                (3.395, 6.520),
                (3.385, 6.520),
                (3.385, 6.510),
            ),
            srid=4326,
        )
        self.university = University.objects.create(
            name='University of Lagos',
            google_place_id='places/root-university-place',
            latitude='6.515800',
            longitude='3.389900',
            detection_radius_meters=3000,
            delivery_area=MultiPolygon(polygon, srid=4326),
        )

    def search(self, query):
        return self.client.get(
            f'/api/locations/search/?query={query}&university_id={self.university.id}'
        )

    @staticmethod
    def autocomplete_response(*place_ids):
        return {
            'suggestions': [
                {'placePrediction': {'placeId': place_id}}
                for place_id in place_ids
            ]
        }

    @staticmethod
    def details_response(
        place_id,
        name,
        latitude=6.516,
        longitude=3.390,
        primary_type='establishment',
    ):
        response = {
            'id': place_id,
            'formattedAddress': f'{name or "Campus location"}, University of Lagos',
            'location': {'latitude': latitude, 'longitude': longitude},
            'primaryType': primary_type,
        }
        if name is not None:
            response['displayName'] = {'text': name}
        return response

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_partial_vice_input_returns_office_with_unchanged_contract(
        self,
        mock_urlopen,
    ):
        mock_urlopen.side_effect = [
            google_json_response(self.autocomplete_response('vice-office')),
            google_json_response(self.details_response(
                'vice-office',
                "Vice Chancellor's Office",
                primary_type='university',
            )),
        ]

        response = self.search('vice')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['university'], self.university.id)
        self.assertEqual(response.data['results'][0]['name'], "Vice Chancellor's Office")
        self.assertEqual(
            set(response.data['results'][0]),
            {
                'place_id',
                'name',
                'formatted_address',
                'latitude',
                'longitude',
                'distance_meters',
            },
        )
        autocomplete_request = mock_urlopen.call_args_list[0].args[0]
        request_body = json.loads(autocomplete_request.data.decode('utf-8'))
        self.assertEqual(request_body['input'], 'vice')
        self.assertFalse(request_body['includeQueryPredictions'])
        self.assertEqual(request_body['locationBias']['circle']['radius'], 3000.0)
        self.assertNotIn(self.university.name, request_body['input'])
        details_request = mock_urlopen.call_args_list[1].args[0]
        self.assertEqual(
            details_request.headers['X-goog-fieldmask'],
            'id,displayName,formattedAddress,location,primaryType',
        )

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_partial_substring_returns_matching_campus_poi(self, mock_urlopen):
        mock_urlopen.side_effect = [
            google_json_response(self.autocomplete_response('library-place')),
            google_json_response(self.details_response(
                'library-place',
                'Kenneth Dike Library',
                primary_type='library',
            )),
        ]

        response = self.search('dike')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'][0]['name'], 'Kenneth Dike Library')
        autocomplete_request = mock_urlopen.call_args_list[0].args[0]
        self.assertEqual(
            json.loads(autocomplete_request.data.decode('utf-8'))['input'],
            'dike',
        )

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_autocomplete_excludes_outside_polygon_and_accepts_boundary(
        self,
        mock_urlopen,
    ):
        mock_urlopen.side_effect = [
            google_json_response(self.autocomplete_response(
                'outside-place',
                'boundary-place',
            )),
            google_json_response(self.details_response(
                'outside-place',
                'Nearby Shop',
                latitude=6.525,
                longitude=3.390,
            )),
            google_json_response(self.details_response(
                'boundary-place',
                'Campus Boundary Gate',
                latitude=6.520,
                longitude=3.390,
            )),
        ]

        response = self.search('campus')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [result['place_id'] for result in response.data['results']],
            ['boundary-place'],
        )

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_root_is_excluded_and_internal_university_type_is_deduplicated(
        self,
        mock_urlopen,
    ):
        mock_urlopen.side_effect = [
            google_json_response({
                'suggestions': [
                    {'placePrediction': {'placeId': 'root-university-place'}},
                    {'placePrediction': {'placeId': 'internal-office'}},
                    {'placePrediction': {'place': 'places/internal-office'}},
                ],
            }),
            google_json_response(self.details_response(
                'internal-office',
                "Vice Chancellor's Office",
                primary_type='university',
            )),
        ]

        response = self.search('office')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [result['place_id'] for result in response.data['results']],
            ['internal-office'],
        )
        self.assertEqual(mock_urlopen.call_count, 2)

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_null_display_name_falls_back_to_formatted_address(self, mock_urlopen):
        details = self.details_response('unnamed-place', None)
        details['formattedAddress'] = 'Faculty Road, University of Lagos'
        mock_urlopen.side_effect = [
            google_json_response(self.autocomplete_response('unnamed-place')),
            google_json_response(details),
        ]

        response = self.search('faculty')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['results'][0]['name'],
            'Faculty Road, University of Lagos',
        )

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_text_search_fallback_runs_without_usable_predictions(self, mock_urlopen):
        mock_urlopen.side_effect = [
            google_json_response({
                'suggestions': [
                    {'queryPrediction': {'text': {'text': 'Campus cafe'}}},
                    {'placePrediction': {}},
                ],
            }),
            google_json_response({
                'places': [{
                    'id': 'fallback-cafe',
                    'displayName': {'text': 'Campus Cafe'},
                    'formattedAddress': 'Campus Cafe, University of Lagos',
                    'location': {'latitude': 6.516, 'longitude': 3.390},
                    'primaryType': 'cafe',
                }],
            }),
        ]

        response = self.search('caf')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'][0]['place_id'], 'fallback-cafe')
        fallback_request = mock_urlopen.call_args_list[1].args[0]
        self.assertEqual(fallback_request.full_url, 'https://places.googleapis.com/v1/places:searchText')

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_failed_place_details_does_not_remove_other_predictions(
        self,
        mock_urlopen,
    ):
        mock_urlopen.side_effect = [
            google_json_response(self.autocomplete_response(
                'stale-place',
                'working-place',
            )),
            URLError('stale prediction'),
            google_json_response(self.details_response(
                'working-place',
                'Working Campus Hall',
            )),
        ]

        response = self.search('hall')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [result['place_id'] for result in response.data['results']],
            ['working-place'],
        )
        self.assertEqual(mock_urlopen.call_count, 3)


class ReverseGeocodeTests(APITestCase):
    def setUp(self):
        cache.clear()
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
        self.token = signup_response.data['token']
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token}')
        self.university = University.objects.create(
            name='Redeemers University',
            google_place_id='places/root-university-place',
            latitude='7.123000',
            longitude='4.123000',
            detection_radius_meters=3000,
        )
        self.latitude = 7.123456
        self.longitude = 4.123456

    def request_body(self, **overrides):
        body = {
            'latitude': self.latitude,
            'longitude': self.longitude,
            'university_id': self.university.id,
        }
        body.update(overrides)
        return body

    @staticmethod
    def successful_provider_response():
        return {
            'status': 'OK',
            'results': [
                {
                    'place_id': 'plus-code-result',
                    'formatted_address': 'MFH4+CRR, Ede 232101, Osun, Nigeria',
                    'types': ['plus_code'],
                    'address_components': [
                        {
                            'long_name': 'MFH4+CRR',
                            'types': ['plus_code'],
                        }
                    ],
                },
                {
                    'place_id': 'google-place-id',
                    'formatted_address': 'MFH4+CRR, Ede 232101, Osun, Nigeria',
                    'types': ['establishment', 'point_of_interest'],
                    'address_components': [
                        {
                            'long_name': 'Redeemers University Cafeteria (Manna Palace)',
                            'types': ['establishment', 'point_of_interest'],
                        },
                        {
                            'long_name': 'Ede',
                            'types': ['locality'],
                        },
                    ],
                    'geometry': {
                        'location': {'lat': 7.999999, 'lng': 4.999999}
                    },
                }
            ],
        }

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_successful_reverse_geocoding_preserves_selected_coordinates_and_is_reusable(
        self,
        mock_urlopen,
    ):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps(self.successful_provider_response()).encode('utf-8')
        )

        response = self.client.post(
            '/api/locations/reverse-geocode/',
            self.request_body(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['university'], self.university.id)
        self.assertEqual(response.data['place_id'], 'google-place-id')
        self.assertEqual(
            response.data['name'],
            'Redeemers University Cafeteria (Manna Palace)',
        )
        self.assertEqual(
            response.data['formatted_address'],
            'MFH4+CRR, Ede 232101, Osun, Nigeria',
        )
        self.assertEqual(response.data['latitude'], self.latitude)
        self.assertEqual(response.data['longitude'], self.longitude)
        self.assertNotIn('test-server-key', str(response.data))

        address_response = self.client.post(
            '/api/addresses/',
            {
                'university': response.data['university'],
                'label': 'Map pin',
                'address': response.data['formatted_address'],
                'provider_place_id': response.data['place_id'],
                'latitude': response.data['latitude'],
                'longitude': response.data['longitude'],
            },
            format='json',
        )
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='2500.00')
        order_response = self.client.post(
            '/api/orders/',
            {
                'university': response.data['university'],
                'delivery_address': response.data['formatted_address'],
                'delivery_place_id': response.data['place_id'],
                'delivery_latitude': response.data['latitude'],
                'delivery_longitude': response.data['longitude'],
                'order_items': [{'menu_item': menu_item.id, 'quantity': 1}],
            },
            format='json',
        )

        self.assertEqual(address_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(order_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(order_response.data['delivery_place_id'], 'google-place-id')

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_reverse_geocode_rejects_exact_university_root_place_id(
        self,
        mock_urlopen,
    ):
        response = self.client.post(
            '/api/locations/reverse-geocode/',
            self.request_body(place_id='root-university-place'),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['place_id'],
            'Select a specific campus building or delivery point.',
        )
        mock_urlopen.assert_not_called()

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_reverse_geocode_uses_place_details_for_internal_poi(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps({
                'id': 'vice-chancellor-office',
                'formattedAddress': "Vice Chancellor's Office, Redeemers University",
            }).encode('utf-8')
        )

        response = self.client.post(
            '/api/locations/reverse-geocode/',
            self.request_body(place_id='places/vice-chancellor-office'),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['place_id'], 'vice-chancellor-office')
        self.assertEqual(
            response.data['name'],
            "Vice Chancellor's Office, Redeemers University",
        )
        self.assertEqual(response.data['latitude'], self.latitude)
        self.assertEqual(response.data['longitude'], self.longitude)
        provider_request = mock_urlopen.call_args.args[0]
        self.assertTrue(provider_request.full_url.endswith('/vice-chancellor-office'))
        self.assertEqual(
            provider_request.headers['X-goog-fieldmask'],
            'id,displayName,formattedAddress',
        )

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_reverse_geocode_rejects_malformed_coordinates(self, mock_urlopen):
        invalid_coordinates = (
            {'latitude': 'not-a-number'},
            {'longitude': 'not-a-number'},
            {'latitude': 91},
            {'longitude': 181},
            {'latitude': 'NaN'},
        )

        for invalid_values in invalid_coordinates:
            with self.subTest(invalid_values=invalid_values):
                response = self.client.post(
                    '/api/locations/reverse-geocode/',
                    self.request_body(**invalid_values),
                    format='json',
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_urlopen.assert_not_called()

    def test_reverse_geocode_requires_university_and_active_university(self):
        missing_id_body = self.request_body()
        missing_id_body.pop('university_id')
        missing_id_response = self.client.post(
            '/api/locations/reverse-geocode/',
            missing_id_body,
            format='json',
        )
        unknown_response = self.client.post(
            '/api/locations/reverse-geocode/',
            self.request_body(university_id=999999),
            format='json',
        )
        self.university.is_active = False
        self.university.save(update_fields=['is_active'])
        inactive_response = self.client.post(
            '/api/locations/reverse-geocode/',
            self.request_body(),
            format='json',
        )

        self.assertEqual(missing_id_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('university_id', missing_id_response.data)
        self.assertEqual(unknown_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(inactive_response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_reverse_geocode_rejects_point_outside_university_radius(
        self,
        mock_urlopen,
    ):
        response = self.client.post(
            '/api/locations/reverse-geocode/',
            self.request_body(latitude=9.0765, longitude=7.3986),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('location', response.data)
        mock_urlopen.assert_not_called()

    def test_reverse_geocode_requires_authentication(self):
        self.client.credentials()

        response = self.client.post(
            '/api/locations/reverse-geocode/',
            self.request_body(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_reverse_geocode_reports_no_address_result(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps({'status': 'ZERO_RESULTS', 'results': []}).encode('utf-8')
        )

        response = self.client.post(
            '/api/locations/reverse-geocode/',
            self.request_body(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_reverse_geocode_reports_timeout_and_provider_failure(self, mock_urlopen):
        for provider_error in (TimeoutError(), URLError('provider unavailable')):
            with self.subTest(provider_error=provider_error):
                mock_urlopen.side_effect = provider_error
                response = self.client.post(
                    '/api/locations/reverse-geocode/',
                    self.request_body(),
                    format='json',
                )
                self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_reverse_geocode_reports_google_api_failure(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps({'status': 'REQUEST_DENIED', 'results': []}).encode('utf-8')
        )

        response = self.client.post(
            '/api/locations/reverse-geocode/',
            self.request_body(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)

    @override_settings(GOOGLE_MAPS_API_KEY='')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_reverse_geocode_reports_missing_provider_configuration(
        self,
        mock_urlopen,
    ):
        response = self.client.post(
            '/api/locations/reverse-geocode/',
            self.request_body(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        mock_urlopen.assert_not_called()


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


class CampusDeliveryAreaTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='+2348012345678')
        self.customer = Customer.objects.create(
            user=self.user,
            phone_number='+2348012345678',
        )
        self.client.force_authenticate(user=self.user)
        polygon = Polygon(
            (
                (3.385, 6.510),
                (3.395, 6.510),
                (3.395, 6.520),
                (3.385, 6.520),
                (3.385, 6.510),
            ),
            srid=4326,
        )
        self.university = University.objects.create(
            name='University of Lagos',
            google_place_id='places/root-university-place',
            latitude='6.515800',
            longitude='3.389900',
            detection_radius_meters=3000,
            delivery_area=MultiPolygon(polygon, srid=4326),
        )

    def test_shared_validator_uses_polygon_covers_including_boundary(self):
        validate_coordinates_within_university(
            self.university,
            latitude=6.516,
            longitude=3.390,
        )
        validate_coordinates_within_university(
            self.university,
            latitude=6.520,
            longitude=3.390,
        )

        with self.assertRaises(LocationOutsideDeliveryArea):
            validate_coordinates_within_university(
                self.university,
                latitude=6.525,
                longitude=3.390,
            )

    def test_shared_validator_falls_back_to_radius_without_polygon(self):
        self.university.delivery_area = None
        self.university.save(update_fields=['delivery_area'])

        validate_coordinates_within_university(
            self.university,
            latitude=6.525,
            longitude=3.390,
        )
        with self.assertRaises(LocationOutsideDeliveryArea):
            validate_coordinates_within_university(
                self.university,
                latitude=9.0765,
                longitude=7.3986,
            )

    def test_university_api_exposes_geojson_and_detect_accepts_boundary(self):
        list_response = self.client.get('/api/universities/')
        detect_response = self.client.post(
            '/api/universities/detect/',
            {'latitude': 6.520, 'longitude': 3.390},
            format='json',
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            list_response.data[0]['delivery_area']['type'],
            'MultiPolygon',
        )
        self.assertEqual(
            list_response.data[0]['google_place_id'],
            'places/root-university-place',
        )
        self.assertEqual(list_response.data[0]['delivery_fee'], '500.00')
        self.assertEqual(detect_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            detect_response.data['university']['delivery_area']['type'],
            'MultiPolygon',
        )

    @override_settings(GOOGLE_MAPS_ADMIN_BROWSER_API_KEY='test-browser-key')
    def test_university_admin_uses_google_map_centered_on_saved_coordinates(self):
        request = RequestFactory().get(
            f'/admin/swift_delivery_backend/university/{self.university.id}/change/'
        )
        request.user = User.objects.create_superuser(
            username='map-admin',
            password='test-password',
        )
        model_admin = UniversityAdmin(University, AdminSite())

        form = model_admin.get_form(request, obj=self.university)
        widget = form.base_fields['delivery_area'].widget
        widget_attrs = widget.attrs
        rendered_widget = str(form(instance=self.university)['delivery_area'])
        widget_context = widget.get_context(
            'delivery_area',
            self.university.delivery_area,
            {'id': 'id_delivery_area'},
        )

        self.assertIsInstance(widget, GoogleMapsMultiPolygonWidget)
        self.assertIn('google_place_id', form.base_fields)
        self.assertIn('delivery_fee', form.base_fields)
        self.assertEqual(widget_attrs['default_lon'], 3.3899)
        self.assertEqual(widget_attrs['default_lat'], 6.5158)
        self.assertEqual(widget_attrs['default_zoom'], 16)
        self.assertIn('Start polygon', rendered_widget)
        self.assertEqual(widget_context['google_maps_api_key'], 'test-browser-key')
        self.assertIn('MultiPolygon', rendered_widget)
        self.assertIn('Zoom: loading', rendered_widget)

    def test_google_admin_widget_accepts_multipolygon_geojson(self):
        request = RequestFactory().get(
            f'/admin/swift_delivery_backend/university/{self.university.id}/change/'
        )
        request.user = User.objects.create_superuser(
            username='geometry-admin',
            password='test-password',
        )
        model_admin = UniversityAdmin(University, AdminSite())
        form = model_admin.get_form(request, obj=self.university)
        delivery_area_field = form.base_fields['delivery_area']
        geojson = json.dumps({
            'type': 'MultiPolygon',
            'coordinates': [[[
                [3.385, 6.510],
                [3.395, 6.510],
                [3.395, 6.520],
                [3.385, 6.520],
                [3.385, 6.510],
            ]]],
        })

        geometry = delivery_area_field.clean(geojson)

        self.assertEqual(geometry.geom_type, 'MultiPolygon')
        self.assertEqual(geometry.srid, 4326)

    def test_detection_rejects_point_outside_polygon_but_inside_radius(self):
        response = self.client.post(
            '/api/universities/detect/',
            {'latitude': 6.525, 'longitude': 3.390},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_location_search_filters_results_against_polygon(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps({
                'places': [
                    {
                        'id': 'inside-place',
                        'displayName': {'text': 'Campus Cafeteria'},
                        'formattedAddress': 'Inside campus',
                        'location': {'latitude': 6.516, 'longitude': 3.390},
                    },
                    {
                        'id': 'outside-polygon',
                        'displayName': {'text': 'Nearby Shop'},
                        'formattedAddress': 'Near campus',
                        'location': {'latitude': 6.525, 'longitude': 3.390},
                    },
                ],
            }).encode('utf-8')
        )

        response = self.client.get(
            f'/api/locations/search/?query=Campus&university_id={self.university.id}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [result['place_id'] for result in response.data['results']],
            ['inside-place'],
        )

    @override_settings(GOOGLE_MAPS_API_KEY='test-server-key')
    @patch('swift_delivery_backend.location_services.urlopen')
    def test_reverse_geocode_rejects_point_outside_polygon(self, mock_urlopen):
        response = self.client.post(
            '/api/locations/reverse-geocode/',
            {
                'latitude': 6.525,
                'longitude': 3.390,
                'university_id': self.university.id,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_urlopen.assert_not_called()

    def test_addresses_and_orders_reject_points_outside_polygon(self):
        address_response = self.client.post(
            '/api/addresses/',
            {
                'university': self.university.id,
                'label': 'Nearby hostel',
                'address': 'Just outside campus',
                'latitude': '6.525000',
                'longitude': '3.390000',
            },
            format='json',
        )
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='2500.00')
        order_response = self.client.post(
            '/api/orders/',
            {
                'university': self.university.id,
                'delivery_address': 'Just outside campus',
                'delivery_latitude': '6.525000',
                'delivery_longitude': '3.390000',
                'order_items': [{'menu_item': menu_item.id, 'quantity': 1}],
            },
            format='json',
        )

        self.assertEqual(address_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('location', address_response.data)
        self.assertEqual(order_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('delivery_location', order_response.data)


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


class DeliveryQuoteTests(APITestCase):
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
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Token {signup_response.data['token']}"
        )
        self.customer = Customer.objects.get(user__email='ada@example.com')
        polygon = Polygon(
            (
                (3.385, 6.510),
                (3.395, 6.510),
                (3.395, 6.520),
                (3.385, 6.520),
                (3.385, 6.510),
            ),
            srid=4326,
        )
        self.university = University.objects.create(
            name='University of Lagos',
            google_place_id='places/university-root',
            latitude='6.515800',
            longitude='3.389900',
            detection_radius_meters=3000,
            delivery_area=MultiPolygon(polygon, srid=4326),
            delivery_fee=Decimal('650.25'),
        )
        self.first_item = MenuItem.objects.create(
            name='Jollof Rice',
            price=Decimal('1000.10'),
        )
        self.second_item = MenuItem.objects.create(
            name='Water',
            price=Decimal('400.05'),
        )
        self.cart = Cart.objects.create(customer=self.customer)
        self.cart.cart_items.create(menu_item=self.first_item, quantity=2)
        self.cart.cart_items.create(menu_item=self.second_item, quantity=3)

    def quote_payload(self, **overrides):
        payload = {
            'university': self.university.id,
            'delivery_latitude': '6.516000',
            'delivery_longitude': '3.390000',
            'delivery_place_id': 'specific-campus-place',
        }
        payload.update(overrides)
        return payload

    def post_quote(self, **overrides):
        return self.client.post(
            '/api/delivery/quote/',
            self.quote_payload(**overrides),
            format='json',
        )

    def test_quote_uses_backend_prices_and_configured_fee_with_exact_decimals(self):
        response = self.post_quote(
            subtotal_amount='0.01',
            delivery_fee='0.01',
            total_amount='0.02',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {
            'currency': 'NGN',
            'item_count': 5,
            'subtotal_amount': '3200.35',
            'delivery_fee': '650.25',
            'total_amount': '3850.60',
            'university': self.university.id,
        })

    def test_quote_resolves_delivery_point_from_owned_saved_address(self):
        address = CustomerAddress.objects.create(
            customer=self.customer,
            university=self.university,
            label='Hostel',
            address='Moremi Hall',
            provider_place_id='moremi-hall',
            latitude='6.516000',
            longitude='3.390000',
        )

        response = self.client.post(
            '/api/delivery/quote/',
            {'customer_address': address.id},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['university'], self.university.id)

    def test_quote_rejects_address_owned_by_another_customer(self):
        other_user = User.objects.create_user(username='+2348099999999')
        other_customer = Customer.objects.create(
            user=other_user,
            phone_number='+2348099999999',
        )
        address = CustomerAddress.objects.create(
            customer=other_customer,
            university=self.university,
            label='Private',
            address='Private address',
            latitude='6.516000',
            longitude='3.390000',
        )

        response = self.client.post(
            '/api/delivery/quote/',
            {'customer_address': address.id},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('customer_address', response.data)

    def test_quote_rejects_saved_address_outside_polygon(self):
        address = CustomerAddress.objects.create(
            customer=self.customer,
            university=self.university,
            label='Outside',
            address='Outside campus',
            latitude='6.525000',
            longitude='3.390000',
        )

        response = self.client.post(
            '/api/delivery/quote/',
            {'customer_address': address.id},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('delivery_location', response.data)

    def test_quote_accepts_polygon_boundary_and_radius_fallback(self):
        boundary_response = self.post_quote(
            delivery_latitude='6.520000',
            delivery_longitude='3.390000',
        )
        self.university.delivery_area = None
        self.university.save(update_fields=['delivery_area'])
        radius_response = self.post_quote(
            delivery_latitude='6.525000',
            delivery_longitude='3.390000',
        )

        self.assertEqual(boundary_response.status_code, status.HTTP_200_OK)
        self.assertEqual(radius_response.status_code, status.HTTP_200_OK)

    def test_empty_cart_cannot_be_quoted(self):
        self.cart.cart_items.all().delete()

        response = self.post_quote()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('cart', response.data)

    def test_zero_delivery_fee_and_invalid_fee_configuration(self):
        self.university.delivery_fee = Decimal('0.00')
        self.university.save(update_fields=['delivery_fee'])
        zero_fee_response = self.post_quote()

        self.university.delivery_fee = Decimal('-1.00')
        self.university.save(update_fields=['delivery_fee'])
        invalid_fee_response = self.post_quote()

        self.assertEqual(zero_fee_response.status_code, status.HTTP_200_OK)
        self.assertEqual(zero_fee_response.data['delivery_fee'], '0.00')
        self.assertEqual(
            zero_fee_response.data['total_amount'],
            zero_fee_response.data['subtotal_amount'],
        )
        self.assertEqual(
            invalid_fee_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn('delivery_fee', invalid_fee_response.data)

    def test_quote_rejects_university_root_place_and_missing_location(self):
        root_response = self.post_quote(
            delivery_place_id='university-root',
        )
        missing_response = self.client.post(
            '/api/delivery/quote/',
            {},
            format='json',
        )

        self.assertEqual(root_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('delivery_place_id', root_response.data)
        self.assertEqual(missing_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('university', missing_response.data)

    def test_order_recalculates_and_persists_authoritative_totals(self):
        quote_response = self.post_quote()
        self.assertEqual(quote_response.data['delivery_fee'], '650.25')

        self.university.delivery_fee = Decimal('700.15')
        self.university.save(update_fields=['delivery_fee'])
        self.first_item.price = Decimal('1100.10')
        self.first_item.save(update_fields=['price'])
        response = self.client.post(
            '/api/orders/',
            {
                'university': self.university.id,
                'delivery_address': 'Senate Building',
                'delivery_place_id': 'senate-building',
                'delivery_latitude': '6.516000',
                'delivery_longitude': '3.390000',
                'subtotal_amount': '1.00',
                'delivery_fee': '1.00',
                'total_amount': '2.00',
                'order_items': [
                    {'menu_item': self.first_item.id, 'quantity': 2, 'price': '1.00'},
                    {'menu_item': self.second_item.id, 'quantity': 3, 'price': '1.00'},
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['subtotal_amount'], '3400.35')
        self.assertEqual(response.data['delivery_fee'], '700.15')
        self.assertEqual(response.data['total_amount'], '4100.50')
        order = Order.objects.get(pk=response.data['id'])
        self.assertEqual(order.subtotal_amount, Decimal('3400.35'))
        self.assertEqual(order.delivery_fee, Decimal('700.15'))
        self.assertEqual(order.total_amount, Decimal('4100.50'))

    def test_quote_requires_authentication(self):
        self.client.credentials()

        response = self.post_quote()

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


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

    def test_customer_can_save_vendor_and_delivery_notes_independently(self):
        self.authenticate_customer()

        update_response = self.client.patch(
            '/api/cart/',
            {
                'vendor_notes': 'No onions, please',
                'delivery_notes': 'Call me at the hostel gate',
            },
            format='json',
        )

        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(update_response.data['vendor_notes'], 'No onions, please')
        self.assertEqual(
            update_response.data['delivery_notes'],
            'Call me at the hostel gate',
        )

        vendor_update = self.client.patch(
            '/api/cart/',
            {'vendor_notes': 'No onions or pepper, please'},
            format='json',
        )

        self.assertEqual(vendor_update.data['vendor_notes'], 'No onions or pepper, please')
        self.assertEqual(vendor_update.data['delivery_notes'], 'Call me at the hostel gate')

    def test_cart_item_changes_preserve_both_notes_and_clear_cart_clears_them(self):
        self.authenticate_customer()
        menu_item = MenuItem.objects.create(name='Jollof Rice', price='2500.00')
        self.client.patch(
            '/api/cart/',
            {
                'vendor_notes': 'No onions, please',
                'delivery_notes': 'Call when outside',
            },
            format='json',
        )

        add_response = self.client.post(
            '/api/cart/',
            {'menu_item': menu_item.id, 'quantity': 1},
            format='json',
        )
        cart_item_id = add_response.data['items'][0]['id']
        self.client.patch(
            f'/api/cart/items/{cart_item_id}/',
            {'quantity': 2},
            format='json',
        )

        current_cart = self.client.get('/api/cart/').data
        self.assertEqual(current_cart['vendor_notes'], 'No onions, please')
        self.assertEqual(current_cart['delivery_notes'], 'Call when outside')

        self.client.delete(f'/api/cart/items/{cart_item_id}/')
        current_cart = self.client.get('/api/cart/').data
        self.assertEqual(current_cart['vendor_notes'], 'No onions, please')
        self.assertEqual(current_cart['delivery_notes'], 'Call when outside')

        clear_response = self.client.delete('/api/cart/')

        self.assertEqual(clear_response.status_code, status.HTTP_200_OK)
        self.assertEqual(clear_response.data['vendor_notes'], '')
        self.assertEqual(clear_response.data['delivery_notes'], '')

    def test_deprecated_notes_field_maps_only_to_vendor_notes(self):
        self.authenticate_customer()
        self.client.patch(
            '/api/cart/',
            {'delivery_notes': 'Meet me at the gate'},
            format='json',
        )

        response = self.client.patch(
            '/api/cart/',
            {'notes': 'No onions, please'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['notes'], 'No onions, please')
        self.assertEqual(response.data['vendor_notes'], 'No onions, please')
        self.assertEqual(response.data['delivery_notes'], 'Meet me at the gate')

    def test_new_vendor_notes_wins_when_deprecated_alias_is_also_supplied(self):
        self.authenticate_customer()

        response = self.client.patch(
            '/api/cart/',
            {
                'vendor_notes': 'Use the new field',
                'notes': 'Ignore the legacy alias',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['vendor_notes'], 'Use the new field')
        self.assertEqual(response.data['notes'], 'Use the new field')

    def test_customer_can_manage_multiple_saved_cart_notes(self):
        self.authenticate_customer()

        first_response = self.client.post(
            '/api/cart/saved-notes/',
            {'note': 'No onions, please', 'note_type': 'vendor'},
            format='json',
        )
        second_response = self.client.post(
            '/api/cart/saved-notes/',
            {'note': 'Call me at the hostel gate', 'note_type': 'delivery'},
            format='json',
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_201_CREATED)

        vendor_response = self.client.get('/api/cart/saved-notes/?type=vendor')
        delivery_response = self.client.get('/api/cart/saved-notes/?type=delivery')

        self.assertEqual(vendor_response.status_code, status.HTTP_200_OK)
        self.assertEqual([note['note'] for note in vendor_response.data], ['No onions, please'])
        self.assertEqual(vendor_response.data[0]['note_type'], 'vendor')
        self.assertEqual([note['note'] for note in delivery_response.data], ['Call me at the hostel gate'])
        self.assertEqual(delivery_response.data[0]['note_type'], 'delivery')

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

    def test_saved_note_validation_and_legacy_default_type(self):
        self.authenticate_customer()

        legacy_response = self.client.post(
            '/api/cart/saved-notes/',
            {'note': 'No pepper'},
            format='json',
        )
        invalid_type_response = self.client.post(
            '/api/cart/saved-notes/',
            {'note': 'Wait outside', 'note_type': 'agent'},
            format='json',
        )
        too_long_response = self.client.post(
            '/api/cart/saved-notes/',
            {'note': 'x' * 501, 'note_type': 'delivery'},
            format='json',
        )
        invalid_filter_response = self.client.get('/api/cart/saved-notes/?type=agent')

        self.assertEqual(legacy_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(legacy_response.data['note_type'], 'vendor')
        self.assertEqual(invalid_type_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('note_type', invalid_type_response.data)
        self.assertEqual(too_long_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('note', too_long_response.data)
        self.assertEqual(invalid_filter_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('type', invalid_filter_response.data)

    def test_cart_note_fields_enforce_maximum_length(self):
        self.authenticate_customer()

        for field in ('vendor_notes', 'delivery_notes', 'notes'):
            response = self.client.patch(
                '/api/cart/',
                {field: 'x' * 501},
                format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(field, response.data)

    def test_saved_notes_require_authentication(self):
        response = self.client.get('/api/cart/saved-notes/?type=vendor')

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

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
        self.assertEqual(cart_response.data['subtotal_amount'], '7500.00')
        self.assertEqual(cart_response.data['total_amount'], '7500.00')

        delete_response = self.client.delete(f'/api/cart/items/{cart_item_id}/')

        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.client.get('/api/cart/').data['item_count'], 0)


class OrderInstructionTests(APITestCase):
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
        self.token = signup_response.data['token']
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token}')
        self.customer = Customer.objects.get(user__email='ada@example.com')
        self.university = University.objects.create(
            name='University of Lagos',
            latitude='6.515800',
            longitude='3.389900',
        )
        self.address = CustomerAddress.objects.create(
            customer=self.customer,
            university=self.university,
            label='Hostel',
            address='Moremi Hall, University of Lagos',
            latitude='6.518000',
            longitude='3.390000',
            delivery_instructions='Call when you reach the hostel gate',
        )
        self.menu_item = MenuItem.objects.create(
            name='Jollof Rice',
            price='2500.00',
        )

    def create_order(self, **overrides):
        payload = {
            'customer_name': 'Ada Okafor',
            'phone_number': '08012345678',
            'delivery_address': 'Moremi Hall, University of Lagos',
            'university': self.university.id,
            'delivery_latitude': '6.518000',
            'delivery_longitude': '3.390000',
            'order_items': [{'menu_item': self.menu_item.id, 'quantity': 1}],
        }
        payload.update(overrides)
        return self.client.post('/api/orders/', payload, format='json')

    def test_order_accepts_and_returns_both_instruction_types(self):
        response = self.create_order(
            vendor_notes='No onions',
            delivery_notes='Call when outside',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['vendor_notes'], 'No onions')
        self.assertEqual(response.data['delivery_notes'], 'Call when outside')

        detail = self.client.get(f"/api/orders/{response.data['id']}/")
        history = self.client.get('/api/orders/history/')
        self.assertEqual(detail.data['vendor_notes'], 'No onions')
        self.assertEqual(detail.data['delivery_notes'], 'Call when outside')
        self.assertEqual(history.data[0]['vendor_notes'], 'No onions')
        self.assertEqual(history.data[0]['delivery_notes'], 'Call when outside')

    def test_order_id_is_generated_read_only_and_exposed_by_all_order_apis(self):
        rejected_create = self.create_order(order_id='12345678')
        self.assertEqual(rejected_create.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('order_id', rejected_create.data)

        creation = self.create_order()
        self.assertEqual(creation.status_code, status.HTTP_201_CREATED)
        public_order_id = creation.data['order_id']
        self.assertRegex(public_order_id, r'^\d{8}$')

        detail = self.client.get(f"/api/orders/{creation.data['id']}/")
        history = self.client.get('/api/orders/history/')
        self.assertEqual(detail.data['order_id'], public_order_id)
        self.assertEqual(history.data[0]['order_id'], public_order_id)

        staff = User.objects.create_user(
            username='order-id-admin',
            password='test-password',
            is_staff=True,
        )
        self.client.force_authenticate(user=staff)
        order_list = self.client.get('/api/orders/')
        vendor = self.client.get(f"/api/orders/{creation.data['id']}/vendor/")
        delivery = self.client.get(f"/api/orders/{creation.data['id']}/delivery/")
        rejected_update = self.client.patch(
            f"/api/orders/{creation.data['id']}/",
            {'order_id': '87654321'},
            format='json',
        )

        listed_order = next(
            order for order in order_list.data
            if order['id'] == creation.data['id']
        )
        self.assertEqual(listed_order['order_id'], public_order_id)
        self.assertEqual(vendor.data['order_id'], public_order_id)
        self.assertEqual(delivery.data['order_id'], public_order_id)
        self.assertEqual(rejected_update.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('order_id', rejected_update.data)
        self.assertEqual(
            Order.objects.get(pk=creation.data['id']).order_id,
            public_order_id,
        )

        field = Order._meta.get_field('order_id')
        self.assertEqual(field.max_length, 8)
        self.assertTrue(field.unique)
        self.assertTrue(field.db_index)
        self.assertFalse(field.editable)

    def test_order_creation_retries_a_database_order_id_collision(self):
        Order.objects.create(
            order_id='40172007',
            customer_name='Existing order',
        )

        with patch(
            'swift_delivery_backend.order_services.generate_order_id',
            side_effect=['40172007', '87654321'],
        ) as generator:
            response = self.create_order()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['order_id'], '87654321')
        self.assertEqual(generator.call_count, 2)

    def test_order_copies_both_notes_from_cart(self):
        self.client.patch(
            '/api/cart/',
            {
                'vendor_notes': 'Pack sauce separately',
                'delivery_notes': 'Meet me beside the gate',
            },
            format='json',
        )

        response = self.create_order()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['vendor_notes'], 'Pack sauce separately')
        self.assertEqual(response.data['delivery_notes'], 'Meet me beside the gate')

    def test_explicit_order_notes_override_cart_notes(self):
        self.client.patch(
            '/api/cart/',
            {
                'vendor_notes': 'Cart vendor note',
                'delivery_notes': 'Cart delivery note',
            },
            format='json',
        )

        response = self.create_order(
            vendor_notes='Order vendor note',
            delivery_notes='Order delivery note',
        )

        self.assertEqual(response.data['vendor_notes'], 'Order vendor note')
        self.assertEqual(response.data['delivery_notes'], 'Order delivery note')

    def test_saved_address_instructions_only_prefill_delivery_notes(self):
        self.client.patch(
            '/api/cart/',
            {'vendor_notes': 'No cutlery', 'delivery_notes': ''},
            format='json',
        )

        response = self.create_order(
            customer_address=self.address.id,
            delivery_address=None,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['vendor_notes'], 'No cutlery')
        self.assertEqual(
            response.data['delivery_notes'],
            'Call when you reach the hostel gate',
        )

    def test_explicit_empty_delivery_note_suppresses_cart_and_address_defaults(self):
        self.client.patch(
            '/api/cart/',
            {'delivery_notes': 'Cart delivery note'},
            format='json',
        )

        response = self.create_order(
            customer_address=self.address.id,
            delivery_address=None,
            delivery_notes='',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['delivery_notes'], '')

    def test_order_instruction_fields_enforce_maximum_length(self):
        for field in ('vendor_notes', 'delivery_notes'):
            response = self.create_order(**{field: 'x' * 501})
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn(field, response.data)

    def test_role_specific_order_views_do_not_mix_private_instructions(self):
        order_response = self.create_order(
            vendor_notes='No onions',
            delivery_notes='Call when outside',
        )
        staff = User.objects.create_user(
            username='operations-admin',
            password='test-password',
            is_staff=True,
        )
        self.client.force_authenticate(user=staff)

        vendor_response = self.client.get(
            f"/api/orders/{order_response.data['id']}/vendor/"
        )
        delivery_response = self.client.get(
            f"/api/orders/{order_response.data['id']}/delivery/"
        )

        self.assertEqual(vendor_response.status_code, status.HTTP_200_OK)
        self.assertEqual(vendor_response.data['vendor_notes'], 'No onions')
        self.assertNotIn('delivery_notes', vendor_response.data)
        self.assertEqual(delivery_response.data['delivery_notes'], 'Call when outside')
        self.assertNotIn('vendor_notes', delivery_response.data)


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
        university = University.objects.create(
            name='University of Lagos',
            latitude='6.515800',
            longitude='3.389900',
        )

        order_response = self.client.post(
            '/api/orders/',
            {
                'customer_name': 'Ada Okafor',
                'phone_number': '08012345678',
                'delivery_address': '12 Lagos Street',
                'university': university.id,
                'delivery_latitude': '6.518000',
                'delivery_longitude': '3.390000',
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


class SeparateInstructionMigrationTests(TransactionTestCase):
    migrate_from = [('swift_delivery_backend', '0022_savedcartnote')]
    migrate_to = [
        ('swift_delivery_backend', '0025_separate_vendor_and_delivery_notes')
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        UserModel = old_apps.get_model('auth', 'User')
        CustomerModel = old_apps.get_model('swift_delivery_backend', 'Customer')
        CartModel = old_apps.get_model('swift_delivery_backend', 'Cart')
        SavedNoteModel = old_apps.get_model('swift_delivery_backend', 'SavedCartNote')

        user = UserModel.objects.create(username='migration-customer')
        customer = CustomerModel.objects.create(
            user_id=user.id,
            phone_number='+2348012345678',
        )
        CartModel.objects.create(
            customer_id=customer.id,
            notes='Legacy vendor instruction',
        )
        SavedNoteModel.objects.create(
            customer_id=customer.id,
            note='Legacy saved vendor instruction',
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_cart_and_saved_notes_are_preserved_as_vendor_notes(self):
        CartModel = self.apps.get_model('swift_delivery_backend', 'Cart')
        SavedNoteModel = self.apps.get_model(
            'swift_delivery_backend',
            'SavedCartNote',
        )

        cart = CartModel.objects.get()
        saved_note = SavedNoteModel.objects.get()
        self.assertEqual(cart.vendor_notes, 'Legacy vendor instruction')
        self.assertEqual(cart.delivery_notes, '')
        self.assertEqual(saved_note.note, 'Legacy saved vendor instruction')
        self.assertEqual(saved_note.note_type, 'vendor')


class DeliveryFeeMigrationTests(TransactionTestCase):
    migrate_from = [('swift_delivery_backend', '0027_university_google_place_id')]
    migrate_to = [('swift_delivery_backend', '0028_delivery_fees_and_order_totals')]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        UniversityModel = old_apps.get_model(
            'swift_delivery_backend',
            'University',
        )
        MenuItemModel = old_apps.get_model(
            'swift_delivery_backend',
            'MenuItem',
        )
        OrderModel = old_apps.get_model('swift_delivery_backend', 'Order')
        OrderItemModel = old_apps.get_model(
            'swift_delivery_backend',
            'OrderItem',
        )

        UniversityModel.objects.create(
            name='Legacy University',
            latitude='6.515800',
            longitude='3.389900',
        )
        menu_item = MenuItemModel.objects.create(
            name='Legacy Meal',
            price=Decimal('750.25'),
        )
        order = OrderModel.objects.create(
            customer_name='Legacy Customer',
            delivery_address='Legacy Address',
        )
        OrderItemModel.objects.create(
            order_id=order.id,
            menu_item_id=menu_item.id,
            quantity=2,
        )
        self.order_id = order.id

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_orders_keep_historical_total_and_universities_get_default_fee(self):
        UniversityModel = self.apps.get_model(
            'swift_delivery_backend',
            'University',
        )
        OrderModel = self.apps.get_model('swift_delivery_backend', 'Order')

        university = UniversityModel.objects.get(name='Legacy University')
        order = OrderModel.objects.get(pk=self.order_id)
        self.assertEqual(university.delivery_fee, Decimal('500.00'))
        self.assertEqual(order.subtotal_amount, Decimal('1500.50'))
        self.assertEqual(order.delivery_fee, Decimal('0.00'))
        self.assertEqual(order.total_amount, Decimal('1500.50'))


class OrderIdMigrationTests(TransactionTestCase):
    migrate_from = [('swift_delivery_backend', '0028_delivery_fees_and_order_totals')]
    migrate_to = [('swift_delivery_backend', '0029_order_order_id')]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OrderModel = old_apps.get_model('swift_delivery_backend', 'Order')
        self.legacy_order_pks = [
            OrderModel.objects.create(customer_name=f'Legacy {index}').pk
            for index in range(3)
        ]

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_orders_receive_unique_eight_digit_order_ids(self):
        OrderModel = self.apps.get_model('swift_delivery_backend', 'Order')
        order_ids = list(
            OrderModel.objects
            .filter(pk__in=self.legacy_order_pks)
            .values_list('order_id', flat=True)
        )

        self.assertEqual(len(order_ids), 3)
        self.assertEqual(len(set(order_ids)), 3)
        for order_id in order_ids:
            self.assertRegex(order_id, r'^\d{8}$')

        field = OrderModel._meta.get_field('order_id')
        self.assertTrue(field.unique)
        self.assertTrue(field.db_index)
        self.assertFalse(field.null)
