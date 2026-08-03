from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CustomerCartItemView,
    CustomerCartView,
    CustomerSavedCartNoteViewSet,
    CustomerAddressViewSet,
    CustomerFavoriteVendorDetailView,
    CustomerFavoriteVendorListView,
    CustomerLoginView,
    CustomerLogoutView,
    CustomerMeView,
    CustomerOrderHistoryView,
    CustomerSignupView,
    LocationSearchView,
    MenuItemViewSet,
    OrderViewSet,
    UniversityViewSet,
    VendorViewSet,
    VendorRatingViewSet,
)

router = DefaultRouter()
router.register(r'menu-items', MenuItemViewSet)
router.register(r'orders', OrderViewSet)
router.register(r'vendors', VendorViewSet, basename='vendor')
router.register(r'cafeterias', VendorViewSet, basename='cafeteria')
router.register(r'vendor-ratings', VendorRatingViewSet)
router.register(r'universities', UniversityViewSet, basename='university')
router.register(r'addresses', CustomerAddressViewSet, basename='customer-address')
router.register(r'cart/saved-notes', CustomerSavedCartNoteViewSet, basename='customer-saved-cart-note')

urlpatterns = [
    path('auth/customer/signup/', CustomerSignupView.as_view(), name='customer-signup'),
    path('auth/customer/login/', CustomerLoginView.as_view(), name='customer-login'),
    path('auth/customer/logout/', CustomerLogoutView.as_view(), name='customer-logout'),
    path('auth/customer/me/', CustomerMeView.as_view(), name='customer-me'),
    path('locations/search/', LocationSearchView.as_view(), name='location-search'),
    path('cart/', CustomerCartView.as_view(), name='customer-cart'),
    path('cart/items/<int:pk>/', CustomerCartItemView.as_view(), name='customer-cart-item'),
    path('orders/history/', CustomerOrderHistoryView.as_view(), name='customer-order-history'),
    path('favorites/vendors/', CustomerFavoriteVendorListView.as_view(), name='customer-favorite-vendors'),
    path('favorites/vendors/<int:vendor_id>/', CustomerFavoriteVendorDetailView.as_view(), name='customer-favorite-vendor-detail'),
    path('', include(router.urls)),
]
