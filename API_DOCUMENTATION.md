# Swift Delivery API Documentation

This document describes the HTTP API exposed by the Swift Delivery Django REST Framework backend.

## 1. Base URL

Local development:

```text
http://127.0.0.1:8000/api/
```

Production or staging deployments should use the deployed backend URL with the same `/api/` prefix.

## 2. Authentication

The API uses Django REST Framework token authentication.

Authenticated requests should include:

```http
Authorization: Token <token>
```

Endpoints marked "Requires authentication" need this header. Other endpoints are public unless noted.

## 3. Common Response Codes

| Status | Meaning |
| --- | --- |
| `200 OK` | Request succeeded. |
| `201 Created` | Resource was created. |
| `204 No Content` | Resource was deleted. |
| `400 Bad Request` | Validation failed or required fields are missing. |
| `401 Unauthorized` | Authentication credentials were missing or invalid. |
| `404 Not Found` | The requested resource was not found. |
| `502 Bad Gateway` | External location provider failed. |
| `503 Service Unavailable` | Location search is not configured. |

## 4. Auth And Customer Endpoints

### 4.1. Sign Up Customer

```http
POST /api/auth/customer/signup/
```

Creates a customer account and returns an auth token.

Request body:

```json
{
  "phone_number": "08012345678",
  "first_name": "Ada",
  "last_name": "Okafor",
  "email": "ada@example.com"
}
```

Notes:

- Nigerian phone numbers are normalized to `+234...` format.
- The customer is created with an unusable password, so login is currently phone-number based.

Success response:

```json
{
  "token": "token-value",
  "customer": {
    "id": 1,
    "phone_number": "+2348012345678",
    "first_name": "Ada",
    "last_name": "Okafor",
    "email": "ada@example.com",
    "preferred_university": null,
    "created_at": "2026-07-13T12:00:00Z"
  }
}
```

### 4.2. Login Customer

```http
POST /api/auth/customer/login/
```

Returns an auth token for an existing customer.

Request body:

```json
{
  "phone_number": "08012345678"
}
```

Success response:

```json
{
  "token": "token-value",
  "customer": {
    "id": 1,
    "phone_number": "+2348012345678",
    "first_name": "Ada",
    "last_name": "Okafor",
    "email": "ada@example.com",
    "preferred_university": {
      "id": 1,
      "name": "University of Lagos"
    },
    "created_at": "2026-07-13T12:00:00Z"
  }
}
```

### 4.3. Logout Customer

```http
POST /api/auth/customer/logout/
```

Requires authentication. Deletes the customer's server-side auth token and
returns `204 No Content`. The client should also remove its locally stored token.

### 4.4. Get Current Customer

```http
GET /api/auth/customer/me/
```

Requires authentication.

Returns the authenticated customer's profile.

### 4.5. Update Current Customer

```http
PATCH /api/auth/customer/me/
```

Requires authentication.

Editable fields:

```json
{
  "phone_number": "+2348012345678",
  "preferred_university_id": 1
}
```

`preferred_university_id` is write-only. Customer responses expose the selected
university as an object containing its `id` and `name`.

## 5. Universities

### 5.1. List Universities

```http
GET /api/universities/
```

Returns active universities.

Response item fields:

```json
{
  "id": 1,
  "name": "University Name",
  "google_place_id": "ChIJ-university-root",
  "latitude": "6.524379",
  "longitude": "3.379206",
  "detection_radius_meters": 3000,
  "delivery_fee": "500.00",
  "delivery_area": {
    "type": "MultiPolygon",
    "coordinates": [
      [
        [
          [3.370000, 6.510000],
          [3.390000, 6.510000],
          [3.390000, 6.530000],
          [3.370000, 6.510000]
        ]
      ]
    ]
  },
  "is_active": true
}
```

`delivery_area` is a GeoJSON `MultiPolygon` or `null`. GeoJSON positions use
`[longitude, latitude]` order. When a delivery polygon is configured, it is the
authoritative campus geofence. Points on the polygon boundary are accepted.
`detection_radius_meters` is used only when `delivery_area` is `null` or empty.
`delivery_fee` is the university's current flat delivery fee in NGN. The
backend uses this value when producing a delivery quote and recalculates it
again when an order is created.
`google_place_id` is the optional Google Place ID of the university's broad
root listing. Clients may use it for an early selection check, but the backend
remains authoritative.

### 5.2. Retrieve University

```http
GET /api/universities/{id}/
```

### 5.3. Detect Nearest Supported University

```http
POST /api/universities/detect/
```

Request body:

```json
{
  "latitude": 6.524379,
  "longitude": 3.379206
}
```

Success response:

```json
{
  "university": {
    "id": 1,
    "name": "University Name",
    "google_place_id": "ChIJ-university-root",
    "latitude": "6.524379",
    "longitude": "3.379206",
    "detection_radius_meters": 3000,
    "delivery_fee": "500.00",
    "delivery_area": {
      "type": "MultiPolygon",
      "coordinates": [
        [
          [
            [3.370000, 6.510000],
            [3.390000, 6.510000],
            [3.390000, 6.530000],
            [3.370000, 6.510000]
          ]
        ]
      ]
    },
    "is_active": true
  },
  "distance_meters": 120
}
```

Possible errors:

- `400` if latitude or longitude is missing or invalid.
- `404` if no active university delivery polygon covers the point and no
  radius-fallback university contains it.

## 6. Location Search

### 6.1. Search Delivery Locations

```http
GET /api/locations/search/?query={query}&university_id={id}
```

Requires authentication.

Uses Google Places Autocomplete (New) (`places:autocomplete`) so incomplete
names and substrings such as `vice` can return campus POIs. The trimmed query is
sent unchanged as Autocomplete's `input`; the university name is not appended.
Only place predictions are requested. A circular location bias uses the
university coordinates and detection radius, capped at Google's supported
50-kilometre bias maximum.

The backend resolves up to five unique predictions through Place Details (New),
requesting only ID, display name, formatted address, coordinates, and primary
type. Successful details are cached for five minutes. One failed or stale
prediction does not prevent other valid predictions from being returned. If
Autocomplete supplies no usable result, including when all resolved results
are outside the delivery area, the existing Text Search flow runs as a
fallback.

The Google location bias is not authoritative. Every Autocomplete and Text
Search result is validated by the backend against the university delivery
polygon or its radius fallback.

Results from both provider paths retain the existing response format and are
deduplicated by normalized Google Place ID. Google's prediction order is kept
where ranking quality is equal. Named establishments, premises, campus
buildings, and points of interest rank above plus-code-only or generic address
results. If Place Details has no display name, `formatted_address` is returned
as `name`. Results outside the selected university polygon are excluded.
Universities without a polygon continue to use `detection_radius_meters`.

If the University has `google_place_id` configured, the exact matching root
listing is omitted. No result is excluded merely because Google classifies it
as `university`; offices, cafeterias, libraries, halls, hostels, and other
campus POIs remain available when their Place IDs differ. `ChIJ...` and
`places/ChIJ...` are normalized to the same ID before filtering and
deduplication. Universities without `google_place_id` retain the previous
search behavior.

Query parameters:

| Parameter | Required | Description |
| --- | --- | --- |
| `query` | Yes | Search text. Must be at least 3 characters. |
| `university_id` | Yes | Active university ID used to bias and validate results. |

Success response:

```json
{
  "university": 1,
  "results": [
    {
      "place_id": "example",
      "name": "Main Gate",
      "formatted_address": "Main Gate, University Name",
      "latitude": 6.524379,
      "longitude": 3.379206,
      "distance_meters": 250
    }
  ]
}
```

Possible errors:

- `400` if `query` or `university_id` is invalid.
- `503` if `GOOGLE_MAPS_API_KEY` is not configured.
- `502` if Google Places fails.

### 6.2. Reverse Geocode An Exact Map Point

```http
POST /api/locations/reverse-geocode/
```

Requires authentication. Uses the server-side `GOOGLE_MAPS_API_KEY`. When a
Place ID is supplied, Google Place Details provides its precise display name
and formatted address. Without one, the endpoint falls back to reverse
geocoding for backward compatibility. The API key is never returned to the
client.

Request body:

```json
{
  "latitude": 7.123456,
  "longitude": 4.123456,
  "university_id": 1,
  "place_id": "ChIJ-campus-building"
}
```

`place_id` is optional and may be either a standalone ID (`ChIJ...`) or a
resource name (`places/ChIJ...`). The response always uses the normalized
standalone form.

Success response:

```json
{
  "university": 1,
  "place_id": "google-place-id",
  "name": "Redeemers University Cafeteria (Manna Palace)",
  "formatted_address": "MFH4+CRR, Ede 232101, Osun, Nigeria",
  "latitude": 7.123456,
  "longitude": 4.123456,
  "distance_meters": 250
}
```

The returned latitude and longitude are the customer's exact selected map
coordinates. Google result coordinates do not replace them. `name` prefers a
meaningful establishment, premise, campus building, or point of interest. It
falls back to `formatted_address` when Place Details has no display name. For
legacy coordinate-only reverse geocoding, it falls back to a sensible address
component or `null`; the backend does not invent a name.

Possible errors:

- `400` if coordinates are missing, non-numeric, outside valid latitude or
  longitude ranges, outside the selected university delivery area, or the
  normalized `place_id` exactly matches the university root Place ID. The last
  case returns `Select a specific campus building or delivery point.`
- `401` if authentication credentials are missing or invalid.
- `404` if the university does not exist, is inactive, or Google returns no
  address result.
- `503` if `GOOGLE_MAPS_API_KEY` is not configured.
- `502` if Google times out or returns a provider failure.

The response can be used without changing the selected coordinates:

- For `POST /api/addresses/`, map `formatted_address` to `address`, `place_id`
  to `provider_place_id`, and send `university`, `latitude`, and `longitude`.
- For `POST /api/orders/`, map `formatted_address` to `delivery_address`,
  `place_id` to `delivery_place_id`, and send `university` as well as
  `delivery_latitude` and `delivery_longitude`.

## 7. Customer Addresses

All address endpoints require authentication.

### 7.1. List Addresses

```http
GET /api/addresses/
```

### 7.2. Create Address

```http
POST /api/addresses/
```

Request body:

```json
{
  "university": 1,
  "label": "Hostel",
  "address": "Block A, Room 12",
  "provider_place_id": "example",
  "latitude": "6.524379",
  "longitude": "3.379206",
  "delivery_instructions": "Call when outside",
  "is_default": true
}
```

Notes:

- `customer` is set from the authenticated user and is read-only.
- If this is the customer's first address, it becomes the default.
- If `is_default` is true, other addresses for the customer are marked non-default.
- If a university and coordinates are provided, the address must be covered by
  its delivery polygon, including the polygon boundary. The detection radius is
  used only when that university has no polygon.
- `provider_place_id` is normalized before storage. If it exactly matches the
  selected university's root `google_place_id`, the request is rejected. Other
  campus POIs are unaffected.

### 7.3. Retrieve Address

```http
GET /api/addresses/{id}/
```

### 7.4. Update Address

```http
PUT /api/addresses/{id}/
PATCH /api/addresses/{id}/
```

### 7.5. Delete Address

```http
DELETE /api/addresses/{id}/
```

If the deleted address was the default, another address is made default when available.

## 8. Vendors

The backend exposes the same vendor viewset under both `/api/vendors/` and `/api/cafeterias/`. Prefer `/api/vendors/` for new clients.

### 8.1. List Vendors

```http
GET /api/vendors/
GET /api/cafeterias/
```

Optional query parameters:

| Parameter | Description |
| --- | --- |
| `vendor_type` | Filter by `cafeteria`, `grills`, `pastries`, or `drinks`. |
| `university_id` | Filter vendors by university. |

Response item fields include:

```json
{
  "id": 1,
  "menu_items": [],
  "average_rating": 4.5,
  "rating_count": 10,
  "name": "Vendor Name",
  "image": "/media/cafeteria_images/example.jpg",
  "logo": "/media/vendor_logos/example-logo.png",
  "vendor_type": "cafeteria",
  "closing_time": "18:00:00",
  "university": 1
}
```

### 8.2. Create Vendor

```http
POST /api/vendors/
```

Requires an authenticated staff user. For normal catalog management, use the
Django Admin interface instead.

Request body:

```json
{
  "name": "Vendor Name",
  "vendor_type": "cafeteria",
  "closing_time": "18:00:00",
  "university": 1
}
```

### 8.3. Retrieve Vendor

```http
GET /api/vendors/{id}/
```

### 8.4. Update Vendor

```http
PUT /api/vendors/{id}/
PATCH /api/vendors/{id}/
```

Requires an authenticated staff user. Send `multipart/form-data` with a `logo`
file to upload or replace the vendor logo. The storefront `image` and `logo`
are separate fields.

### 8.5. Delete Vendor

```http
DELETE /api/vendors/{id}/
```

Requires an authenticated staff user.

### 8.6. List Vendor Menu

```http
GET /api/vendors/{id}/menu/
```

Optional query parameters:

| Parameter | Description |
| --- | --- |
| `category_id` | Filter menu items by category. |

### 8.7. List Or Create Vendor Ratings

```http
GET /api/vendors/{id}/ratings/
POST /api/vendors/{id}/ratings/
```

POST request body:

```json
{
  "rating": 5,
  "customer_name": "Ada",
  "comment": "Fast service"
}
```

The `vendor` field is automatically set from the URL.

## 9. Vendor Ratings

### 9.1. List Ratings

```http
GET /api/vendor-ratings/
```

Optional query parameters:

| Parameter | Description |
| --- | --- |
| `vendor_id` | Filter ratings by vendor. |

### 9.2. Create Rating

```http
POST /api/vendor-ratings/
```

Request body:

```json
{
  "vendor": 1,
  "rating": 5,
  "customer_name": "Ada",
  "comment": "Fast service"
}
```

Rating must be between `1` and `5`.

### 9.3. Retrieve, Update, Delete Rating

```http
GET /api/vendor-ratings/{id}/
PUT /api/vendor-ratings/{id}/
PATCH /api/vendor-ratings/{id}/
DELETE /api/vendor-ratings/{id}/
```

## 10. Menu Items

### 10.1. List Menu Items

```http
GET /api/menu-items/
```

Response item fields include:

```json
{
  "id": 1,
  "category_name": "Rice",
  "vendors": [1],
  "name": "Jollof Rice",
  "price": "1500.00",
  "available": true,
  "image": "/media/menu_images/jollof.jpg",
  "category": 1
}
```

### 10.2. Create Menu Item

```http
POST /api/menu-items/
```

Request body:

```http
Content-Type: multipart/form-data

vendors=1
name=Jollof Rice
price=1500.00
available=true
category=1
image=@jollof.jpg
```

When `image` is supplied, the server removes its background before saving the
menu item and stores the result as a transparent PNG. The same processing runs
when an image is replaced with `PUT` or `PATCH`, and for uploads through Django
admin. JPG/JPEG (including `.jfif`), PNG, and WebP uploads are supported. A
processing or configuration failure returns `400 Bad Request` with an error on
the `image` field; the original image is not stored.

Menu images are sent to the background-removal provider with the foreground
type set to `product`, which improves detection for food-item photography.

Menu items are returned in stable category-creation order and then item-creation
order. Updating an image does not change the category or item position.

Set `REMOVE_BG_API_KEY` in the server environment to enable processing. The API
key must never be exposed in a client application.

### 10.3. Retrieve, Update, Delete Menu Item

```http
GET /api/menu-items/{id}/
PUT /api/menu-items/{id}/
PATCH /api/menu-items/{id}/
DELETE /api/menu-items/{id}/
```

## 11. Cart

Cart endpoints require authentication.

### 11.1. Get Cart

```http
GET /api/cart/
```

Response:

```json
{
  "id": 1,
  "customer": 1,
  "items": [
    {
      "id": 1,
      "menu_item": 3,
      "menu_item_detail": {},
      "quantity": 2,
      "line_total": "3000.00",
      "added_at": "2026-07-13T12:00:00Z",
      "updated_at": "2026-07-13T12:00:00Z"
    }
  ],
  "vendor_notes": "No onions, please",
  "delivery_notes": "Call me when you reach the hostel gate",
  "notes": "No onions, please",
  "subtotal_amount": "3000.00",
  "total_amount": "3000.00",
  "item_count": 2,
  "created_at": "2026-07-13T12:00:00Z",
  "updated_at": "2026-07-13T12:00:00Z"
}
```

`notes` is a deprecated response alias for `vendor_notes`. It is included
temporarily for older frontend clients and never represents delivery-agent
instructions.

`subtotal_amount` is calculated from current backend menu-item prices. During
the frontend migration, cart `total_amount` remains an alias for this item
subtotal and does **not** include a delivery fee. Request an authoritative
checkout total from `/api/delivery/quote/` after selecting a delivery point.

### 11.2. Update Current Cart Instructions

```http
PATCH /api/cart/
```

Request body:

```json
{
  "vendor_notes": "No onions, please",
  "delivery_notes": "Call me when you reach the hostel gate"
}
```

Both fields are optional strings with a maximum length of 500 characters.
PATCH is partial: updating one instruction does not alter the other. Item
addition, quantity updates, and item deletion also leave both instructions
unchanged.

For backward compatibility, PATCH may still send `notes`. The deprecated field
maps only to `vendor_notes`. If both `notes` and `vendor_notes` are supplied,
`vendor_notes` takes precedence. New clients must use the explicit fields.

### 11.3. List Or Create Saved Cart Notes

```http
GET /api/cart/saved-notes/?type=vendor
GET /api/cart/saved-notes/?type=delivery
POST /api/cart/saved-notes/
```

GET returns only the authenticated customer's saved notes of the requested
type, ordered by most recently updated. `type` must be `vendor` or `delivery`.
Omitting it returns both types for compatibility.

POST request body:

```json
{
  "note": "Call me at the hostel gate",
  "note_type": "delivery"
}
```

Saved notes can contain up to 500 characters. A successful POST returns
`201 Created`:

```json
{
  "id": 1,
  "note": "Call me at the hostel gate",
  "note_type": "delivery",
  "created_at": "2026-07-13T12:00:00Z",
  "updated_at": "2026-07-13T12:00:00Z"
}
```

`note_type` must be `vendor` or `delivery`. It defaults to `vendor` only for
legacy clients and legacy saved-note records. To select a saved note for the
current cart, copy its `note` value into the matching `vendor_notes` or
`delivery_notes` field in `PATCH /api/cart/`.

### 11.4. Retrieve, Update, Or Delete A Saved Cart Note

```http
GET /api/cart/saved-notes/{id}/
PUT /api/cart/saved-notes/{id}/
PATCH /api/cart/saved-notes/{id}/
DELETE /api/cart/saved-notes/{id}/
```

Customers can access only their own saved notes. DELETE returns
`204 No Content`.

### 11.5. Add Or Replace Cart Item

```http
POST /api/cart/
```

Request body:

```json
{
  "menu_item": 3,
  "quantity": 2
}
```

Notes:

- If the menu item is already in the cart, its quantity is replaced.
- Quantity must be a positive integer.
- Adding or replacing an item does not overwrite either current instruction.

### 11.6. Clear Cart

```http
DELETE /api/cart/
```

Deletes all cart items, clears both `vendor_notes` and `delivery_notes`, and
returns the empty cart. Saved cart notes are not deleted.

### 11.7. Update Cart Item Quantity

```http
PATCH /api/cart/items/{id}/
```

Request body:

```json
{
  "quantity": 3
}
```

### 11.8. Delete Cart Item

```http
DELETE /api/cart/items/{id}/
```

Returns `204 No Content`.

## 12. Delivery Quote

```http
POST /api/delivery/quote/
```

Requires customer authentication and uses the authenticated customer's current
cart. The cart must contain at least one item.

Request body:

```json
{
  "university": 1,
  "delivery_latitude": "7.123456",
  "delivery_longitude": "4.123456",
  "delivery_place_id": "ChIJ-example",
  "customer_address": 3
}
```

`customer_address` is optional. When supplied, it must belong to the
authenticated customer. Missing university, coordinate, Place ID, and address
values are resolved from that saved address where available. Explicit values
must remain consistent with the selected saved address's university.

The selected point must be covered by the university's delivery polygon;
polygon-boundary points are accepted. The detection radius is used only when
the university has no polygon. The university's exact root Google Place ID is
not a valid delivery point.

Success response:

```json
{
  "currency": "NGN",
  "item_count": 5,
  "subtotal_amount": "3400.00",
  "delivery_fee": "500.00",
  "total_amount": "3900.00",
  "university": 1
}
```

All monetary values are decimal strings with two places. The subtotal comes
from current backend menu-item prices and the delivery fee comes from the
selected university. Client-supplied subtotal, fee, or total values are
ignored. A quote is informational: `POST /api/orders/` always recalculates all
three values inside the order transaction.

Possible errors include:

- `400` for an empty cart, missing/invalid delivery details, an address owned
  by another customer, an uncovered delivery point, the university root Place
  ID, or an invalid/negative configured fee.
- `401` when customer authentication is missing or invalid.

## 13. Favorites

Favorite vendor endpoints require authentication.

### 13.1. List Favorite Vendors

```http
GET /api/favorites/vendors/
```

### 13.2. Add Favorite Vendor

```http
POST /api/favorites/vendors/
```

Request body:

```json
{
  "vendor": 1
}
```

Success response includes the vendor and nested `vendor_detail`.

### 13.3. Remove Favorite Vendor

```http
DELETE /api/favorites/vendors/{vendor_id}/
```

Returns `204 No Content`.

## 14. Orders

### 14.1. List Orders

```http
GET /api/orders/
```

Staff only. Returns all orders.

### 14.2. Create Order

```http
POST /api/orders/
```

If the request is authenticated and the user has a customer profile, the order is linked to that customer.

Request body:

```json
{
  "customer_name": "Ada Okafor",
  "phone_number": "+2348012345678",
  "delivery_address": "Block A, Room 12",
  "delivery_place_id": "example",
  "customer_address": 1,
  "delivery_latitude": "6.524379",
  "delivery_longitude": "3.379206",
  "university": 1,
  "vendor_notes": "No onions",
  "delivery_notes": "Call when outside",
  "order_items": [
    {
      "menu_item": 3,
      "quantity": 2
    }
  ]
}
```

Notes:

- `order_items` is required when creating an order.
- An active university, delivery address, and coordinates are required. A
  selected saved address may provide these values.
- If `customer_address` is supplied, it must belong to the authenticated customer.
- If `customer_address` is supplied, the serializer can copy address, place ID, coordinates, university, and delivery instructions from the saved address.
- Latitude and longitude must be supplied together.
- If a university and coordinates are provided, the delivery location must be
  covered by its delivery polygon, including the polygon boundary. The
  detection radius is used only when that university has no polygon.
- `vendor_notes` and `delivery_notes` are optional and limited to 500 characters each.
- Instruction precedence is: an explicitly supplied order value, then the matching current-cart value, then (for `delivery_notes` only) the selected saved address's `delivery_instructions`, then an empty string.
- An explicitly supplied empty string counts as an explicit value and prevents cart or address prefilling.
- Saved-address `delivery_instructions` never populate `vendor_notes`.
- `delivery_place_id` is normalized before storage. It must not exactly match
  the selected university's root `google_place_id`, including when copied from
  a saved address. Other campus POIs remain valid regardless of Google type.
- Any client-supplied `subtotal_amount`, `delivery_fee`, or `total_amount` is
  ignored. The backend reloads current menu-item prices, calculates the flat
  university delivery fee through the shared pricing service, and stores all
  three monetary snapshots inside the order transaction.
- The delivery quote is not a price lock. Order creation recalculates the
  subtotal and fee so intervening menu-price or university-fee changes are
  reflected in the final payable total.

Response fields include:

```json
{
  "id": 1,
  "order_id": "40172007",
  "items": [
    {
      "id": 1,
      "menu_item": 3,
      "menu_item_name": "Jollof Rice",
      "price": "1500.00",
      "quantity": 2,
      "order": 1
    }
  ],
  "subtotal_amount": "3000.00",
  "delivery_fee": "500.00",
  "total_amount": "3500.00",
  "customer": 1,
  "customer_name": "Ada Okafor",
  "phone_number": "+2348012345678",
  "delivery_address": "Block A, Room 12",
  "delivery_place_id": "example",
  "customer_address": 1,
  "delivery_latitude": "6.524379",
  "delivery_longitude": "3.379206",
  "university": 1,
  "vendor_notes": "No onions",
  "delivery_notes": "Call when outside",
  "order_time": "2026-07-13T12:00:00Z"
}
```

`order_id` is an immutable, server-generated public reference containing
exactly eight numeric digits. Clients must not send it during creation or
updates; attempts to do so are rejected. The numeric `id` remains the internal
database primary key and continues to be used in API routes and authorization
checks, such as `/api/orders/1/`.

### 14.3. Retrieve, Update, Delete Order

```http
GET /api/orders/{id}/
PUT /api/orders/{id}/
PATCH /api/orders/{id}/
DELETE /api/orders/{id}/
```

Authenticated customers may retrieve their own order details. Staff may
retrieve any order and are required for listing, updating, or deleting orders.
Order detail responses include both instruction fields for the owning customer.
Order list and detail responses also include both the internal `id` and public
`order_id`.

### 14.4. Role-Specific Order Instructions

```http
GET /api/orders/{id}/vendor/
GET /api/orders/{id}/delivery/
```

These integration views currently require staff authentication. The vendor
view exposes `vendor_notes` and omits `delivery_notes` and delivery/customer
details. The delivery view exposes delivery/contact details and
`delivery_notes`, and omits `vendor_notes`. This prevents one operational role
from receiving the other role's private instructions. Both responses include
the immutable public `order_id`.

## 15. Customer Order History

```http
GET /api/orders/history/
```

Requires authentication.

Returns orders for the authenticated customer, newest first. Each response
includes `order_id`, `vendor_notes`, and `delivery_notes`.

Order list, detail, history, and creation responses expose the immutable
`subtotal_amount`, `delivery_fee`, and final `total_amount` snapshots. Orders
that existed before this feature retain their historical total: it was copied
to `subtotal_amount`, their `delivery_fee` was initialized to `0.00`, and their
original `total_amount` was left unchanged.

## 16. Media Files

In local development with `DEBUG=True`, uploaded media is served from:

```text
/media/
```

Image fields such as `Vendor.image`, `Vendor.logo`, and `MenuItem.image` may
return paths under `/media/cafeteria_images/`, `/media/vendor_logos/`, or
`/media/menu_images/`.

## 17. Notes For Frontend Clients

- Use `Authorization: Token <token>` after signup or login.
- Prefer `/api/vendors/` over `/api/cafeterias/` for new vendor-related screens.
- Use `/api/universities/detect/` before address creation when you need to map coordinates to a supported campus.
- Use `/api/locations/search/` or `/api/locations/reverse-geocode/` only after
  configuring `GOOGLE_MAPS_API_KEY` with the required Google Places and
  Geocoding APIs enabled.
- The cart API is customer-specific and requires authentication.
- Vendor create, update, and delete operations require an authenticated staff
  user. Menu-item management and ratings retain their existing permissions.

## 18. Geospatial Configuration

University delivery polygons are stored in PostGIS as SRID 4326
`MultiPolygonField` values. The `postgis` PostgreSQL extension must be enabled
before applying the migration that adds the nullable field. Extension lifecycle
is intentionally managed outside application migrations so rolling back an app
migration can never remove a shared database extension. The application
database must use PostgreSQL with PostGIS; plain PostgreSQL and SQLite cannot
store this field.

GeoDjango also requires the GEOS, GDAL, and PROJ native libraries in every
environment that runs Django, including development, test, and deployment
environments. The Django database engine is selected as
`django.contrib.gis.db.backends.postgis` whenever `DATABASE_URL` points to
PostgreSQL.

Administrators can draw or edit `delivery_area` on a Google map in the
University admin page. The map initially centers on the university's saved
`latitude` and `longitude`. Select **Start polygon**, click at least three map
points, then select **Finish polygon**. Existing vertices can be dragged, and
the controls also support undo, cancel, deleting one selected polygon, and
clearing all polygons. Changes are persisted only after the University form is
saved. A live `Zoom: N` indicator beneath the map shows the exact zoom value
reported by Google Maps and updates for button, mouse, and touch zoom changes.
Administrators should also populate `google_place_id` with the exact Google
Place ID of the university's broad root listing. The value is configured per
university and is never hardcoded in application code.

The admin map uses the browser-visible
`GOOGLE_MAPS_ADMIN_BROWSER_API_KEY` environment variable. This must be a Google
Maps JavaScript API key with website restrictions for the admin origins (for
example, the production admin domain and local development URLs). It is kept
separate from the server-side `GOOGLE_MAPS_API_KEY`, which must not be sent to a
browser. The Google Maps Drawing Library is not required; the widget uses the
supported editable Polygon API.

All polygon coordinates and GeoJSON positions use longitude first and latitude
second. The widget submits a GeoJSON `MultiPolygon`, including when only one
polygon is drawn.

The same validator is used by university detection, location search, reverse
geocoding, customer-address validation, and order creation:

1. If a non-empty polygon exists, `delivery_area.covers(point)` decides whether
   the coordinate is accepted. `covers` includes boundary points.
2. If no polygon exists, the distance from the university center is compared
   with `detection_radius_meters` for backward compatibility.
