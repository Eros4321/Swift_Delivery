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
  "latitude": "6.524379",
  "longitude": "3.379206",
  "detection_radius_meters": 3000,
  "is_active": true
}
```

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
    "latitude": "6.524379",
    "longitude": "3.379206",
    "detection_radius_meters": 3000,
    "is_active": true
  },
  "distance_meters": 120
}
```

Possible errors:

- `400` if latitude or longitude is missing or invalid.
- `404` if no active university is close enough.

## 6. Location Search

### 6.1. Search Delivery Locations

```http
GET /api/locations/search/?query={query}&university_id={id}
```

Requires authentication.

Searches Google Places within the selected university delivery radius.

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
      "place_id": "places/example",
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
  "provider_place_id": "places/example",
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
- If a university and coordinates are provided, the address must be within the university delivery radius.

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
  "notes": "No onions, please",
  "total_amount": "3000.00",
  "item_count": 2,
  "created_at": "2026-07-13T12:00:00Z",
  "updated_at": "2026-07-13T12:00:00Z"
}
```

### 11.2. Update Current Cart Notes

```http
PATCH /api/cart/
```

Request body:

```json
{
  "notes": "No onions, please"
}
```

The current note is persisted with the customer's cart and is returned by all
cart responses. Cart notes are not copied to an order automatically; submit the
note as `delivery_notes` when creating the order.

### 11.3. List Or Create Saved Cart Notes

```http
GET /api/cart/saved-notes/
POST /api/cart/saved-notes/
```

GET returns only the authenticated customer's saved notes, ordered by most
recently updated.

POST request body:

```json
{
  "note": "Call me at the hostel gate"
}
```

Saved notes can contain up to 500 characters. A successful POST returns
`201 Created`:

```json
{
  "id": 1,
  "note": "Call me at the hostel gate",
  "created_at": "2026-07-13T12:00:00Z",
  "updated_at": "2026-07-13T12:00:00Z"
}
```

To select a saved note for the current cart, copy its `note` value into
`PATCH /api/cart/`.

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
- Adding or replacing an item does not overwrite the current cart note.

### 11.6. Clear Cart

```http
DELETE /api/cart/
```

Deletes all cart items, clears the current cart note, and returns the empty
cart. Saved cart notes are not deleted.

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

## 12. Favorites

Favorite vendor endpoints require authentication.

### 12.1. List Favorite Vendors

```http
GET /api/favorites/vendors/
```

### 12.2. Add Favorite Vendor

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

### 12.3. Remove Favorite Vendor

```http
DELETE /api/favorites/vendors/{vendor_id}/
```

Returns `204 No Content`.

## 13. Orders

### 13.1. List Orders

```http
GET /api/orders/
```

Returns all orders.

### 13.2. Create Order

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
  "delivery_place_id": "places/example",
  "customer_address": 1,
  "delivery_latitude": "6.524379",
  "delivery_longitude": "3.379206",
  "university": 1,
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
- If `customer_address` is supplied, it must belong to the authenticated customer.
- If `customer_address` is supplied, the serializer can copy address, place ID, coordinates, university, and delivery instructions from the saved address.
- Latitude and longitude must be supplied together.
- If a university and coordinates are provided, the delivery location must be within the university delivery radius.

Response fields include:

```json
{
  "id": 1,
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
  "total_amount": 3000.0,
  "customer": 1,
  "customer_name": "Ada Okafor",
  "phone_number": "+2348012345678",
  "delivery_address": "Block A, Room 12",
  "delivery_place_id": "places/example",
  "customer_address": 1,
  "delivery_latitude": "6.524379",
  "delivery_longitude": "3.379206",
  "university": 1,
  "delivery_notes": "Call when outside",
  "order_time": "2026-07-13T12:00:00Z"
}
```

### 13.3. Retrieve, Update, Delete Order

```http
GET /api/orders/{id}/
PUT /api/orders/{id}/
PATCH /api/orders/{id}/
DELETE /api/orders/{id}/
```

## 14. Customer Order History

```http
GET /api/orders/history/
```

Requires authentication.

Returns orders for the authenticated customer, newest first.

## 15. Media Files

In local development with `DEBUG=True`, uploaded media is served from:

```text
/media/
```

Image fields such as `Vendor.image`, `Vendor.logo`, and `MenuItem.image` may
return paths under `/media/cafeteria_images/`, `/media/vendor_logos/`, or
`/media/menu_images/`.

## 16. Notes For Frontend Clients

- Use `Authorization: Token <token>` after signup or login.
- Prefer `/api/vendors/` over `/api/cafeterias/` for new vendor-related screens.
- Use `/api/universities/detect/` before address creation when you need to map coordinates to a supported campus.
- Use `/api/locations/search/` only after configuring `GOOGLE_MAPS_API_KEY`.
- The cart API is customer-specific and requires authentication.
- Vendor create, update, and delete operations require an authenticated staff
  user. Menu-item management and ratings retain their existing permissions.
