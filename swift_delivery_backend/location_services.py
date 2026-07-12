import json
from math import asin, cos, radians, sin, sqrt
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


GOOGLE_TEXT_SEARCH_URL = 'https://places.googleapis.com/v1/places:searchText'
GOOGLE_FIELD_MASK = 'places.id,places.displayName,places.formattedAddress,places.location'


class LocationProviderError(Exception):
    pass


class LocationProviderNotConfigured(LocationProviderError):
    pass


def distance_in_meters(latitude_one, longitude_one, latitude_two, longitude_two):
    earth_radius_meters = 6371000
    latitude_delta = radians(float(latitude_two) - float(latitude_one))
    longitude_delta = radians(float(longitude_two) - float(longitude_one))
    latitude_one = radians(float(latitude_one))
    latitude_two = radians(float(latitude_two))
    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(latitude_one) * cos(latitude_two) * sin(longitude_delta / 2) ** 2
    )
    return 2 * earth_radius_meters * asin(sqrt(haversine))


def search_google_places(query, university):
    api_key = settings.GOOGLE_MAPS_API_KEY
    if not api_key:
        raise LocationProviderNotConfigured('Google Places is not configured.')

    request_body = json.dumps({
        'textQuery': f'{query}, {university.name}',
        'pageSize': 10,
        'languageCode': 'en',
        'locationBias': {
            'circle': {
                'center': {
                    'latitude': float(university.latitude),
                    'longitude': float(university.longitude),
                },
                'radius': float(university.detection_radius_meters),
            }
        },
    }).encode('utf-8')
    request = Request(
        GOOGLE_TEXT_SEARCH_URL,
        data=request_body,
        headers={
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': api_key,
            'X-Goog-FieldMask': GOOGLE_FIELD_MASK,
        },
        method='POST',
    )

    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise LocationProviderError('Google Places could not complete the search.') from error

    results = []
    for place in payload.get('places', []):
        location = place.get('location') or {}
        latitude = location.get('latitude')
        longitude = location.get('longitude')
        if latitude is None or longitude is None:
            continue

        distance = distance_in_meters(
            university.latitude,
            university.longitude,
            latitude,
            longitude,
        )
        if distance > university.detection_radius_meters:
            continue

        results.append({
            'place_id': place.get('id', ''),
            'name': (place.get('displayName') or {}).get('text', ''),
            'formatted_address': place.get('formattedAddress', ''),
            'latitude': latitude,
            'longitude': longitude,
            'distance_meters': round(distance),
        })

    return results
