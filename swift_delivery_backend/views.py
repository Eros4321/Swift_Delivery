from django.shortcuts import render

# Create your views here.
from django.shortcuts import get_object_or_404
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status, viewsets
from .location_services import (
    LocationProviderError,
    LocationProviderNotConfigured,
    distance_in_meters,
    search_google_places,
)
from .models import Cart, CartItem, Customer, CustomerAddress, FavoriteVendor, MenuItem, Order, SavedCartNote, University, Vendor, VendorRating
from .serializers import (
    CartSerializer,
    CartItemSerializer,
    CustomerLoginSerializer,
    CustomerAddressSerializer,
    CustomerSerializer,
    CustomerSignupSerializer,
    FavoriteVendorSerializer,
    MenuItemSerializer,
    OrderSerializer,
    SavedCartNoteSerializer,
    UniversitySerializer,
    VendorSerializer,
    VendorRatingSerializer,
)


class UniversityViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = University.objects.filter(is_active=True)
    serializer_class = UniversitySerializer

    @action(detail=False, methods=['post'])
    def detect(self, request):
        try:
            latitude = float(request.data.get('latitude'))
            longitude = float(request.data.get('longitude'))
        except (TypeError, ValueError):
            return Response(
                {'detail': 'Valid latitude and longitude are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            return Response(
                {'detail': 'Latitude or longitude is outside its valid range.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        candidates = [
            (distance_in_meters(latitude, longitude, university.latitude, university.longitude), university)
            for university in self.get_queryset()
        ]
        if not candidates:
            return Response({'detail': 'No supported university was found.'}, status=status.HTTP_404_NOT_FOUND)

        distance, university = min(candidates, key=lambda candidate: candidate[0])
        if distance > university.detection_radius_meters:
            return Response({'detail': 'No supported university was found near this location.'}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'university': self.get_serializer(university).data,
            'distance_meters': round(distance),
        })


class LocationSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.query_params.get('query', '').strip()
        university_id = request.query_params.get('university_id')
        if len(query) < 3:
            return Response(
                {'query': 'Enter at least 3 characters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not university_id:
            return Response(
                {'university_id': 'This field is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        university = get_object_or_404(University, pk=university_id, is_active=True)
        try:
            results = search_google_places(query, university)
        except LocationProviderNotConfigured:
            return Response(
                {'detail': 'Location search is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except LocationProviderError:
            return Response(
                {'detail': 'Location search is temporarily unavailable.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({
            'university': university.id,
            'results': results,
        })


class CustomerSignupView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CustomerSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = serializer.save()
        token, _ = Token.objects.get_or_create(user=customer.user)
        return Response(
            {
                'token': token.key,
                'customer': CustomerSerializer(customer).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CustomerLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CustomerLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone_number = serializer.validated_data['phone_number']
        customer = get_object_or_404(Customer, phone_number=phone_number)
        token, _ = Token.objects.get_or_create(user=customer.user)
        return Response(
            {
                'token': token.key,
                'customer': CustomerSerializer(customer).data,
            }
        )


class CustomerLogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomerMeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        customer = get_object_or_404(Customer, user=request.user)
        return Response(CustomerSerializer(customer).data)

    def patch(self, request):
        customer = get_object_or_404(Customer, user=request.user)
        serializer = CustomerSerializer(customer, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class CustomerAddressViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerAddressSerializer
    permission_classes = [IsAuthenticated]

    def get_customer(self):
        return get_object_or_404(Customer, user=self.request.user)

    def get_queryset(self):
        return CustomerAddress.objects.filter(customer=self.get_customer()).select_related('university')

    def perform_create(self, serializer):
        customer = self.get_customer()
        make_default = serializer.validated_data.get('is_default') or not customer.addresses.exists()
        if make_default:
            customer.addresses.update(is_default=False)
        serializer.save(customer=customer, is_default=make_default)

    def perform_update(self, serializer):
        address = self.get_object()
        was_default = address.is_default
        make_default = serializer.validated_data.get('is_default', address.is_default)
        if make_default:
            address.customer.addresses.exclude(pk=address.pk).update(is_default=False)
        serializer.save(is_default=make_default)
        if was_default and not make_default:
            replacement = address.customer.addresses.exclude(pk=address.pk).first()
            if replacement:
                replacement.is_default = True
                replacement.save(update_fields=['is_default', 'updated_at'])

    def perform_destroy(self, instance):
        customer = instance.customer
        was_default = instance.is_default
        instance.delete()
        if was_default:
            replacement = customer.addresses.first()
            if replacement:
                replacement.is_default = True
                replacement.save(update_fields=['is_default', 'updated_at'])


class CustomerCartView(APIView):
    permission_classes = [IsAuthenticated]

    def get_customer(self):
        return get_object_or_404(Customer, user=self.request.user)

    def get_cart(self):
        customer = self.get_customer()
        cart, _ = Cart.objects.prefetch_related('cart_items__menu_item').get_or_create(customer=customer)
        return cart

    def get(self, request):
        return Response(CartSerializer(self.get_cart()).data)

    def patch(self, request):
        cart = self.get_cart()
        serializer = CartSerializer(cart, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def post(self, request):
        cart = self.get_cart()
        menu_item_id = request.data.get('menu_item')
        quantity = request.data.get('quantity', 1)

        if not menu_item_id:
            return Response({'menu_item': 'This field is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return Response({'quantity': 'Quantity must be a positive integer.'}, status=status.HTTP_400_BAD_REQUEST)

        if quantity < 1:
            return Response({'quantity': 'Quantity must be at least 1.'}, status=status.HTTP_400_BAD_REQUEST)

        menu_item = get_object_or_404(MenuItem, pk=menu_item_id)
        CartItem.objects.update_or_create(
            cart=cart,
            menu_item=menu_item,
            defaults={'quantity': quantity},
        )
        cart = Cart.objects.prefetch_related('cart_items__menu_item').get(pk=cart.pk)
        return Response(CartSerializer(cart).data, status=status.HTTP_200_OK)

    def delete(self, request):
        cart = self.get_cart()
        cart.cart_items.all().delete()
        cart.notes = ''
        cart.save(update_fields=['notes', 'updated_at'])
        return Response(CartSerializer(cart).data)


class CustomerSavedCartNoteViewSet(viewsets.ModelViewSet):
    serializer_class = SavedCartNoteSerializer
    permission_classes = [IsAuthenticated]

    def get_customer(self):
        return get_object_or_404(Customer, user=self.request.user)

    def get_queryset(self):
        return SavedCartNote.objects.filter(customer=self.get_customer())

    def perform_create(self, serializer):
        serializer.save(customer=self.get_customer())


class CustomerCartItemView(APIView):
    permission_classes = [IsAuthenticated]

    def get_cart_item(self, request, pk):
        customer = get_object_or_404(Customer, user=request.user)
        cart, _ = Cart.objects.get_or_create(customer=customer)
        return get_object_or_404(CartItem, pk=pk, cart=cart)

    def patch(self, request, pk):
        cart_item = self.get_cart_item(request, pk)
        quantity = request.data.get('quantity')

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return Response({'quantity': 'Quantity must be a positive integer.'}, status=status.HTTP_400_BAD_REQUEST)

        if quantity < 1:
            return Response({'quantity': 'Quantity must be at least 1.'}, status=status.HTTP_400_BAD_REQUEST)

        cart_item.quantity = quantity
        cart_item.save(update_fields=['quantity', 'updated_at'])
        return Response(CartItemSerializer(cart_item).data)

    def delete(self, request, pk):
        cart_item = self.get_cart_item(request, pk)
        cart_item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomerOrderHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        customer = get_object_or_404(Customer, user=request.user)
        orders = (
            customer.orders
            .prefetch_related('orderitem_set__menu_item')
            .order_by('-order_time')
        )
        return Response(OrderSerializer(orders, many=True).data)


class CustomerFavoriteVendorListView(APIView):
    permission_classes = [IsAuthenticated]

    def get_customer(self):
        return get_object_or_404(Customer, user=self.request.user)

    def get(self, request):
        favorites = (
            FavoriteVendor.objects
            .select_related('vendor')
            .filter(customer=self.get_customer())
        )
        return Response(FavoriteVendorSerializer(favorites, many=True).data)

    def post(self, request):
        customer = self.get_customer()
        vendor_id = request.data.get('vendor')

        if not vendor_id:
            return Response({'vendor': 'This field is required.'}, status=status.HTTP_400_BAD_REQUEST)

        vendor = get_object_or_404(Vendor, pk=vendor_id)
        favorite, _ = FavoriteVendor.objects.get_or_create(customer=customer, vendor=vendor)
        return Response(FavoriteVendorSerializer(favorite).data, status=status.HTTP_201_CREATED)


class CustomerFavoriteVendorDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, vendor_id):
        customer = get_object_or_404(Customer, user=request.user)
        favorite = get_object_or_404(FavoriteVendor, customer=customer, vendor_id=vendor_id)
        favorite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class VendorViewSet(viewsets.ModelViewSet):
    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer

    def get_permissions(self):
        if self.action in {'create', 'update', 'partial_update', 'destroy'}:
            return [IsAdminUser()]
        return [AllowAny()]

    def get_queryset(self):
        queryset = super().get_queryset()
        vendor_type = self.request.query_params.get('vendor_type')
        university_id = self.request.query_params.get('university_id')
        if vendor_type:
            queryset = queryset.filter(vendor_type=vendor_type)
        if university_id:
            queryset = queryset.filter(university_id=university_id)
        return queryset
    
    @action(detail=True, methods=['get'], url_path='menu')
    def menu(self, request, pk=None):
        """Retrieve menu items specific to this vendor."""
        vendor = get_object_or_404(Vendor, pk=pk)
        category_id = request.query_params.get('category_id')
        
        menu_items = MenuItem.objects.filter(vendors=vendor)
        if category_id:
            menu_items = menu_items.filter(category_id=category_id)
        
        serializer = MenuItemSerializer(menu_items, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get', 'post'], url_path='ratings')
    def ratings(self, request, pk=None):
        vendor = get_object_or_404(Vendor, pk=pk)

        if request.method == 'GET':
            ratings = vendor.ratings.all()
            serializer = VendorRatingSerializer(ratings, many=True)
            return Response(serializer.data)

        payload = request.data.copy()
        payload['vendor'] = vendor.id
        serializer = VendorRatingSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class MenuItemViewSet(viewsets.ModelViewSet):
    queryset = MenuItem.objects.all()
    serializer_class = MenuItemSerializer


class VendorRatingViewSet(viewsets.ModelViewSet):
    queryset = VendorRating.objects.select_related('vendor').all()
    serializer_class = VendorRatingSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        vendor_id = self.request.query_params.get('vendor_id')
        if vendor_id:
            queryset = queryset.filter(vendor_id=vendor_id)
        return queryset


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer

    def perform_create(self, serializer):
        customer = None
        if self.request.user.is_authenticated:
            customer = Customer.objects.filter(user=self.request.user).first()
        serializer.save(customer=customer)
