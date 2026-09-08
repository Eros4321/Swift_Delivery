import json
from hashlib import sha256
from math import asin, cos, radians, sin, sqrt
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.cache import cache


GOOGLE_AUTOCOMPLETE_URL = 'https://places.googleapis.com/v1/places:autocomplete'
GOOGLE_TEXT_SEARCH_URL = 'https://places.googleapis.com/v1/places:searchText'
GOOGLE_PLACE_DETAILS_URL = 'https://places.googleapis.com/v1/places'
GOOGLE_GEOCODING_URL = 'https://maps.googleapis.com/maps/api/geocode/json'
GOOGLE_AUTOCOMPLETE_FIELD_MASK = (
    'suggestions.placePrediction.placeId,'
    'suggestions.placePrediction.place'
)
GOOGLE_TEXT_SEARCH_FIELD_MASK = (
    'places.id,places.displayName,places.formattedAddress,places.location,'
    'places.primaryType,places.types'
)
GOOGLE_PLACE_DETAILS_FIELD_MASK = 'id,displayName,formattedAddress'
GOOGLE_SEARCH_PLACE_DETAILS_FIELD_MASK = (
    'id,displayName,formattedAddress,location,primaryType'
)
GOOGLE_PLACE_DETAILS_CACHE_TIMEOUT = 300
MEANINGFUL_PLACE_TYPES = {
    'establishment',
    'point_of_interest',
    'premise',
    'school',
    'university',
}
GENERIC_PLACE_NAMES = {'unnamed road', 'unknown road'}
PLUS_CODE_PATTERN = re.compile(r'^[23456789CFGHJMPQRVWX]{2,8}\+[23456789CFGHJMPQRVWX]+', re.IGNORECASE)


class LocationProviderError(Exception):
    pass


class LocationProviderNotConfigured(LocationProviderError):
    pass


class LocationResultNotFound(LocationProviderError):
    pass


class LocationOutsideDeliveryArea(ValueError):
    pass


class UniversityRootPlaceSelected(ValueError):
    pass


def normalize_google_place_id(value):
    if not value:
        return None
    value = str(value).strip()
    if value.startswith('places/'):
        value = value.removeprefix('places/')
    return value or None


def validate_google_place_id_for_university(university, place_id):
    normalized_place_id = normalize_google_place_id(place_id)
    university_place_id = normalize_google_place_id(
        getattr(university, 'google_place_id', None)
    )
    if (
        normalized_place_id
        and university_place_id
        and normalized_place_id == university_place_id
    ):
        raise UniversityRootPlaceSelected(
            'Select a specific campus building or delivery point.'
        )
    return normalized_place_id


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


def validate_coordinates_within_university(university, latitude, longitude):
    distance = distance_in_meters(
        university.latitude,
        university.longitude,
        latitude,
        longitude,
    )

    delivery_area = university.delivery_area
    if delivery_area is not None and not delivery_area.empty:
        # GeoJSON and WGS84 geometry coordinates are always longitude, latitude.
        point = Point(float(longitude), float(latitude), srid=4326)
        if delivery_area.srid and delivery_area.srid != point.srid:
            point.transform(delivery_area.srid)
        if not delivery_area.covers(point):
            raise LocationOutsideDeliveryArea
    elif distance > university.detection_radius_meters:
        raise LocationOutsideDeliveryArea
    return distance


def _looks_like_plus_code(value):
    return bool(value and PLUS_CODE_PATTERN.match(value.strip()))


def _is_meaningful_name(name, formatted_address=''):
    if not name:
        return False
    normalized_name = name.strip().casefold()
    return (
        normalized_name not in GENERIC_PLACE_NAMES
        and not _looks_like_plus_code(name)
        and normalized_name != (formatted_address or '').strip().casefold()
    )


def _search_result_score(place, query):
    name = ((place.get('displayName') or {}).get('text') or '').strip()
    formatted_address = (place.get('formattedAddress') or '').strip()
    place_types = set(place.get('types') or [])
    primary_type = place.get('primaryType')
    if primary_type:
        place_types.add(primary_type)

    score = 0
    query_normalized = query.casefold()
    meaningful_name = _is_meaningful_name(name, formatted_address)
    if query_normalized in name.casefold():
        score += 100 if meaningful_name else 15
    if meaningful_name:
        score += 50
    if place_types & MEANINGFUL_PLACE_TYPES:
        score += 25
    if query_normalized in formatted_address.casefold():
        score += 10
    if _looks_like_plus_code(name) or 'plus_code' in place_types:
        score -= 100
    return score


def _meaningful_reverse_geocode_name(result):
    explicit_name = (result.get('name') or '').strip()
    formatted_address = (result.get('formatted_address') or '').strip()
    if _is_meaningful_name(explicit_name, formatted_address):
        return explicit_name

    components = result.get('address_components') or []
    preferred_component_types = (
        'establishment',
        'point_of_interest',
        'premise',
        'university',
        'school',
        'subpremise',
        'neighborhood',
        'sublocality',
        'route',
        'locality',
    )
    for preferred_type in preferred_component_types:
        for component in components:
            if preferred_type not in (component.get('types') or []):
                continue
            name = (component.get('long_name') or '').strip()
            if _is_meaningful_name(name, formatted_address):
                return name
    return None


def _reverse_result_score(result):
    name = _meaningful_reverse_geocode_name(result)
    result_types = set(result.get('types') or [])
    score = 100 if name else 0
    if result_types & MEANINGFUL_PLACE_TYPES:
        score += 50
    if 'plus_code' in result_types:
        score -= 100
    return score


def _google_location_bias(university):
    # Autocomplete's circle radius cannot exceed 50 km. The backend geofence
    # remains authoritative regardless of how broad this provider-side bias is.
    radius = min(float(university.detection_radius_meters), 50000.0)
    return {
        'circle': {
            'center': {
                'latitude': float(university.latitude),
                'longitude': float(university.longitude),
            },
            'radius': radius,
        }
    }


def _read_google_json(request, error_message):
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise LocationProviderError(error_message) from error


def _autocomplete_google_place_ids(query, university, api_key):
    request = Request(
        GOOGLE_AUTOCOMPLETE_URL,
        data=json.dumps({
            'input': query,
            'includeQueryPredictions': False,
            'languageCode': 'en',
            'locationBias': _google_location_bias(university),
        }).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': api_key,
            'X-Goog-FieldMask': GOOGLE_AUTOCOMPLETE_FIELD_MASK,
        },
        method='POST',
    )
    payload = _read_google_json(
        request,
        'Google Places could not complete the autocomplete search.',
    )

    place_ids = []
    seen_place_ids = set()
    for suggestion in (payload.get('suggestions') or [])[:5]:
        prediction = suggestion.get('placePrediction') or {}
        place_id = normalize_google_place_id(
            prediction.get('placeId') or prediction.get('place')
        )
        if not place_id or place_id in seen_place_ids:
            continue
        try:
            validate_google_place_id_for_university(university, place_id)
        except UniversityRootPlaceSelected:
            continue
        seen_place_ids.add(place_id)
        place_ids.append(place_id)
    return place_ids


def _place_to_search_result(place, query, university, provider_index):
    location = place.get('location') or {}
    latitude = location.get('latitude')
    longitude = location.get('longitude')
    if latitude is None or longitude is None:
        return None

    try:
        place_id = validate_google_place_id_for_university(
            university,
            place.get('id'),
        )
        distance = validate_coordinates_within_university(
            university,
            latitude,
            longitude,
        )
    except (LocationOutsideDeliveryArea, UniversityRootPlaceSelected):
        return None

    formatted_address = (place.get('formattedAddress') or '').strip()
    display_name = ((place.get('displayName') or {}).get('text') or '').strip()
    return {
        'place_id': place_id or '',
        'name': display_name or formatted_address or None,
        'formatted_address': formatted_address,
        'latitude': latitude,
        'longitude': longitude,
        'distance_meters': round(distance),
        '_provider_index': provider_index,
        '_score': _search_result_score(place, query),
    }


def _rank_and_clean_search_results(results):
    ranked_results = sorted(
        results,
        key=lambda result: (
            -result['_score'],
            result['_provider_index'],
            result['distance_meters'],
        ),
    )
    for result in ranked_results:
        result.pop('_provider_index', None)
        result.pop('_score', None)
    return ranked_results


def _deduplicate_search_results(results):
    results_by_key = {}
    for result in results:
        normalized_place_id = normalize_google_place_id(result.get('place_id'))
        deduplication_key = normalized_place_id or (
            (result.get('formatted_address') or '').casefold(),
            str(result.get('latitude')),
            str(result.get('longitude')),
        )
        result['place_id'] = normalized_place_id or ''
        existing = results_by_key.get(deduplication_key)
        if existing is None or result['_score'] > existing['_score']:
            results_by_key[deduplication_key] = result
    return list(results_by_key.values())


def _search_google_places_text(query, university, api_key):
    request = Request(
        GOOGLE_TEXT_SEARCH_URL,
        data=json.dumps({
            'textQuery': f'{query}, {university.name}',
            'pageSize': 10,
            'languageCode': 'en',
            'locationBias': _google_location_bias(university),
        }).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': api_key,
            'X-Goog-FieldMask': GOOGLE_TEXT_SEARCH_FIELD_MASK,
        },
        method='POST',
    )
    payload = _read_google_json(
        request,
        'Google Places could not complete the text search.',
    )
    results = []
    for provider_index, place in enumerate(payload.get('places') or []):
        result = _place_to_search_result(
            place,
            query,
            university,
            provider_index,
        )
        if result:
            results.append(result)
    return _deduplicate_search_results(results)


def _details_to_provider_place(details):
    location = None
    if details.get('latitude') is not None and details.get('longitude') is not None:
        location = {
            'latitude': details['latitude'],
            'longitude': details['longitude'],
        }
    return {
        'id': details.get('place_id'),
        'displayName': {'text': details.get('name') or ''},
        'formattedAddress': details.get('formatted_address') or '',
        'location': location,
        'primaryType': details.get('primary_type'),
    }


def search_google_places(query, university):
    api_key = settings.GOOGLE_MAPS_API_KEY
    if not api_key:
        raise LocationProviderNotConfigured('Google Places is not configured.')

    query = query.strip()
    predicted_place_ids = _autocomplete_google_place_ids(
        query,
        university,
        api_key,
    )
    autocomplete_results = []
    for provider_index, place_id in enumerate(predicted_place_ids[:5]):
        try:
            details = get_google_place_details(place_id, include_location=True)
        except (LocationProviderError, LocationResultNotFound):
            # One stale or temporarily unavailable prediction must not discard
            # other predictions that can still be resolved successfully.
            continue
        result = _place_to_search_result(
            _details_to_provider_place(details),
            query,
            university,
            provider_index,
        )
        if result:
            autocomplete_results.append(result)

    autocomplete_results = _deduplicate_search_results(autocomplete_results)
    if autocomplete_results:
        return _rank_and_clean_search_results(autocomplete_results)

    fallback_results = _search_google_places_text(query, university, api_key)
    merged_results = _deduplicate_search_results(
        autocomplete_results + fallback_results
    )
    return _rank_and_clean_search_results(merged_results)


def get_google_place_details(place_id, *, include_location=False):
    api_key = settings.GOOGLE_MAPS_API_KEY
    if not api_key:
        raise LocationProviderNotConfigured('Google Places is not configured.')

    normalized_place_id = normalize_google_place_id(place_id)
    if not normalized_place_id:
        raise LocationResultNotFound('A valid Google Place ID is required.')

    field_mask = (
        GOOGLE_SEARCH_PLACE_DETAILS_FIELD_MASK
        if include_location
        else GOOGLE_PLACE_DETAILS_FIELD_MASK
    )
    cache_digest = sha256(
        f'{normalized_place_id}:{field_mask}'.encode('utf-8')
    ).hexdigest()
    cache_key = f'google-place-details:{cache_digest}'
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    request = Request(
        f'{GOOGLE_PLACE_DETAILS_URL}/{quote(normalized_place_id, safe="")}',
        headers={
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': api_key,
            'X-Goog-FieldMask': field_mask,
        },
        method='GET',
    )

    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except HTTPError as error:
        if error.code == 404:
            raise LocationResultNotFound(
                'Google Places returned no place result.'
            ) from error
        raise LocationProviderError(
            'Google Places could not complete the place-details lookup.'
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise LocationProviderError(
            'Google Places could not complete the place-details lookup.'
        ) from error

    formatted_address = (payload.get('formattedAddress') or '').strip()
    display_name = ((payload.get('displayName') or {}).get('text') or '').strip()
    if not display_name and not formatted_address:
        raise LocationResultNotFound('Google Places returned no place result.')

    result = {
        'place_id': normalize_google_place_id(payload.get('id')) or normalized_place_id,
        'name': display_name or formatted_address,
        'formatted_address': formatted_address,
    }
    if include_location:
        location = payload.get('location') or {}
        result.update({
            'latitude': location.get('latitude'),
            'longitude': location.get('longitude'),
            'primary_type': payload.get('primaryType'),
        })
    cache.set(cache_key, result, GOOGLE_PLACE_DETAILS_CACHE_TIMEOUT)
    return result


def reverse_geocode_google(latitude, longitude):
    api_key = settings.GOOGLE_MAPS_API_KEY
    if not api_key:
        raise LocationProviderNotConfigured('Google Geocoding is not configured.')

    query = urlencode({
        'latlng': f'{latitude},{longitude}',
        'key': api_key,
        'language': 'en',
    })
    request = Request(f'{GOOGLE_GEOCODING_URL}?{query}', method='GET')

    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise LocationProviderError(
            'Google Geocoding could not complete the lookup.'
        ) from error

    provider_status = payload.get('status')
    results = payload.get('results') or []
    if provider_status == 'ZERO_RESULTS':
        raise LocationResultNotFound('Google Geocoding returned no address result.')
    if provider_status != 'OK':
        raise LocationProviderError(
            'Google Geocoding could not complete the lookup.'
        )
    if not results:
        raise LocationResultNotFound('Google Geocoding returned no address result.')

    result = max(
        enumerate(results),
        key=lambda item: (_reverse_result_score(item[1]), -item[0]),
    )[1]
    return {
        'place_id': normalize_google_place_id(result.get('place_id')) or '',
        'name': _meaningful_reverse_geocode_name(result),
        'formatted_address': result.get('formatted_address') or '',
    }
