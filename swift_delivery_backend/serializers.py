import json
from math import isfinite

from rest_framework import serializers
from django.contrib.auth.models import User
from django.db import transaction
from .background_removal import BackgroundRemovalError, remove_image_background
from .delivery_services import (
    DeliveryFeeConfigurationError,
    calculate_delivery_fee,
    calculate_order_items_subtotal,
)
from .location_services import (
    LocationOutsideDeliveryArea,
    UniversityRootPlaceSelected,
    normalize_google_place_id,
    validate_coordinates_within_university,
    validate_google_place_id_for_university,
)
from .order_services import (
    OrderIdGenerationError,
    create_order_with_generated_id,
)
from .models import (
    CafeteriaCategory,
    Cart,
    CartItem,
    Customer,
    CustomerAddress,
    FavoriteVendor,
    MenuItem,
    Order,
    OrderItem,
    SavedCartNote,
    University,
    Vendor,
    VendorRating,
    normalize_nigerian_phone_number,
)


class UniversitySummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = University
        fields = ('id', 'name')


class ReverseGeocodeRequestSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    university_id = serializers.IntegerField(min_value=1)
    place_id = serializers.CharField(
        max_length=255,
        allow_blank=True,
        allow_null=True,
        required=False,
    )

    def validate_latitude(self, value):
        if not isfinite(value):
            raise serializers.ValidationError('Enter a finite numeric latitude.')
        return value

    def validate_longitude(self, value):
        if not isfinite(value):
            raise serializers.ValidationError('Enter a finite numeric longitude.')
        return value

    def validate_place_id(self, value):
        return normalize_google_place_id(value)


class CustomerSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    preferred_university = UniversitySummarySerializer(read_only=True)
    preferred_university_id = serializers.PrimaryKeyRelatedField(
        source='preferred_university',
        queryset=University.objects.filter(is_active=True),
        allow_null=True,
        required=False,
        write_only=True,
    )

    class Meta:
        model = Customer
        fields = (
            'id',
            'phone_number',
            'first_name',
            'last_name',
            'email',
            'preferred_university',
            'preferred_university_id',
            'created_at',
        )


class CustomerAddressSerializer(serializers.ModelSerializer):
    university = serializers.PrimaryKeyRelatedField(
        queryset=University.objects.filter(is_active=True),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = CustomerAddress
        fields = '__all__'
        read_only_fields = ('customer',)

    def validate(self, attrs):
        university = attrs.get('university', getattr(self.instance, 'university', None))
        latitude = attrs.get('latitude', getattr(self.instance, 'latitude', None))
        longitude = attrs.get('longitude', getattr(self.instance, 'longitude', None))
        provider_place_id = attrs.get(
            'provider_place_id',
            getattr(self.instance, 'provider_place_id', ''),
        )
        try:
            normalized_place_id = validate_google_place_id_for_university(
                university,
                provider_place_id,
            )
        except UniversityRootPlaceSelected as error:
            raise serializers.ValidationError({
                'provider_place_id': str(error),
            }) from error
        if 'provider_place_id' in attrs:
            attrs['provider_place_id'] = normalized_place_id or ''
        if university and latitude is not None and longitude is not None:
            try:
                validate_coordinates_within_university(
                    university,
                    latitude,
                    longitude,
                )
            except LocationOutsideDeliveryArea:
                raise serializers.ValidationError({
                    'location': 'This address is outside the selected university delivery area.'
                })
        return attrs


class UniversitySerializer(serializers.ModelSerializer):
    delivery_area = serializers.SerializerMethodField()

    def get_delivery_area(self, obj):
        if obj.delivery_area is None or obj.delivery_area.empty:
            return None
        return json.loads(obj.delivery_area.geojson)

    class Meta:
        model = University
        fields = '__all__'


class CustomerSignupSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=14)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    email = serializers.EmailField()

    def validate_phone_number(self, value):
        phone_number = normalize_nigerian_phone_number(value)
        validator = Customer.phone_validator
        validator(phone_number)
        if Customer.objects.filter(phone_number=phone_number).exists():
            raise serializers.ValidationError('A customer with this phone number already exists.')
        return phone_number

    def validate_email(self, value):
        email = value.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError('A customer with this email address already exists.')
        return email

    def create(self, validated_data):
        phone_number = validated_data['phone_number']
        user = User.objects.create(
            username=phone_number,
            first_name=validated_data['first_name'].strip(),
            last_name=validated_data['last_name'].strip(),
            email=validated_data['email'],
        )
        user.set_unusable_password()
        user.save(update_fields=['password'])
        return Customer.objects.create(user=user, phone_number=phone_number)


class CustomerLoginSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=14)

    def validate_phone_number(self, value):
        phone_number = normalize_nigerian_phone_number(value)
        validator = Customer.phone_validator
        validator(phone_number)
        return phone_number

class CafeteriaCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = CafeteriaCategory
        fields = '__all__'

class MenuItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    def validate_image(self, image):
        if image is None:
            return None

        try:
            return remove_image_background(image)
        except BackgroundRemovalError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    class Meta:
        model = MenuItem
        fields = '__all__'
        
class VendorSerializer(serializers.ModelSerializer):
    menu_items = MenuItemSerializer(many=True, read_only=True)
    average_rating = serializers.FloatField(read_only=True)
    rating_count = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = Vendor
        fields = '__all__'

class VendorRatingSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)

    class Meta:
        model = VendorRating
        fields = '__all__'


class FavoriteVendorSerializer(serializers.ModelSerializer):
    vendor_detail = VendorSerializer(source='vendor', read_only=True)

    class Meta:
        model = FavoriteVendor
        fields = ('id', 'vendor', 'vendor_detail', 'created_at')


class CartItemSerializer(serializers.ModelSerializer):
    menu_item_detail = MenuItemSerializer(source='menu_item', read_only=True)
    line_total = serializers.ReadOnlyField()

    class Meta:
        model = CartItem
        fields = ('id', 'menu_item', 'menu_item_detail', 'quantity', 'line_total', 'added_at', 'updated_at')


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(source='cart_items', many=True, read_only=True)
    subtotal_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    total_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    item_count = serializers.ReadOnlyField()
    notes = serializers.CharField(
        allow_blank=True,
        max_length=500,
        required=False,
        write_only=True,
    )

    class Meta:
        model = Cart
        fields = (
            'id',
            'customer',
            'items',
            'vendor_notes',
            'delivery_notes',
            'notes',
            'subtotal_amount',
            'total_amount',
            'item_count',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('customer',)

    def validate(self, attrs):
        legacy_notes = attrs.pop('notes', serializers.empty)
        if legacy_notes is not serializers.empty and 'vendor_notes' not in attrs:
            attrs['vendor_notes'] = legacy_notes
        return attrs

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation['notes'] = representation['vendor_notes']
        return representation


class DeliveryQuoteRequestSerializer(serializers.Serializer):
    university = serializers.PrimaryKeyRelatedField(
        queryset=University.objects.filter(is_active=True),
        required=False,
    )
    delivery_latitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-90,
        max_value=90,
        required=False,
    )
    delivery_longitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-180,
        max_value=180,
        required=False,
    )
    delivery_place_id = serializers.CharField(
        max_length=255,
        allow_blank=True,
        allow_null=True,
        required=False,
    )
    customer_address = serializers.PrimaryKeyRelatedField(
        queryset=CustomerAddress.objects.select_related('university').all(),
        allow_null=True,
        required=False,
    )

    def validate(self, attrs):
        request = self.context['request']
        customer = Customer.objects.filter(user=request.user).first()
        if customer is None:
            raise serializers.ValidationError({
                'customer': 'A customer profile is required.',
            })

        customer_address = attrs.get('customer_address')
        university = attrs.get('university')
        if customer_address:
            if customer_address.customer_id != customer.id:
                raise serializers.ValidationError({
                    'customer_address': 'This address does not belong to the authenticated customer.',
                })
            if (
                university
                and customer_address.university
                and university.id != customer_address.university_id
            ):
                raise serializers.ValidationError({
                    'university': 'This university does not match the selected saved address.',
                })
            university = university or customer_address.university
            attrs.setdefault('university', university)
            attrs.setdefault('delivery_latitude', customer_address.latitude)
            attrs.setdefault('delivery_longitude', customer_address.longitude)
            attrs.setdefault(
                'delivery_place_id',
                customer_address.provider_place_id,
            )
            attrs['delivery_address'] = customer_address.address

        if university is None:
            raise serializers.ValidationError({
                'university': 'This field is required.',
            })

        latitude = attrs.get('delivery_latitude')
        longitude = attrs.get('delivery_longitude')
        if latitude is None or longitude is None:
            raise serializers.ValidationError({
                'delivery_location': 'Delivery latitude and longitude are required.',
            })

        try:
            place_id = validate_google_place_id_for_university(
                university,
                attrs.get('delivery_place_id'),
            )
        except UniversityRootPlaceSelected as error:
            raise serializers.ValidationError({
                'delivery_place_id': str(error),
            }) from error
        attrs['delivery_place_id'] = place_id or ''

        try:
            validate_coordinates_within_university(
                university,
                latitude,
                longitude,
            )
        except LocationOutsideDeliveryArea as error:
            raise serializers.ValidationError({
                'delivery_location': 'This location is outside the selected university delivery area.',
            }) from error
        return attrs


class DeliveryQuoteResponseSerializer(serializers.Serializer):
    currency = serializers.ChoiceField(choices=('NGN',))
    item_count = serializers.IntegerField(min_value=1)
    subtotal_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    delivery_fee = serializers.DecimalField(max_digits=10, decimal_places=2)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    university = serializers.IntegerField(min_value=1)
        

class SavedCartNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedCartNote
        fields = ('id', 'note', 'note_type', 'created_at', 'updated_at')


class OrderItemSerializer(serializers.ModelSerializer):
    menu_item_name = serializers.ReadOnlyField(source='menu_item.name')
    price = serializers.ReadOnlyField(source='menu_item.price')

    class Meta:
        model = OrderItem
        fields = '__all__'


class OrderItemInputSerializer(serializers.Serializer):
    menu_item = serializers.PrimaryKeyRelatedField(
        queryset=MenuItem.objects.all(),
    )
    quantity = serializers.IntegerField(min_value=1)


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(source='orderitem_set', many=True, read_only=True)
    order_items = OrderItemInputSerializer(
        many=True,
        write_only=True,
        allow_empty=False,
    )
    university = serializers.PrimaryKeyRelatedField(
        queryset=University.objects.filter(is_active=True),
        required=False,
    )

    class Meta:
        model = Order
        fields = '__all__'
        read_only_fields = (
            'order_id',
            'customer',
            'subtotal_amount',
            'delivery_fee',
            'total_amount',
        )

    def validate(self, attrs):
        if 'order_id' in self.initial_data:
            raise serializers.ValidationError({
                'order_id': 'This field is generated by the server and is read-only.',
            })

        university = attrs.get('university', getattr(self.instance, 'university', None))
        customer_address = attrs.get('customer_address')
        request = self.context.get('request')

        customer = None
        if request and request.user.is_authenticated:
            customer = Customer.objects.filter(user=request.user).first()

        if customer_address:
            if not customer or customer_address.customer_id != customer.id:
                raise serializers.ValidationError({
                    'customer_address': 'This address does not belong to the authenticated customer.'
                })

            address_university = customer_address.university
            if university and address_university and university.id != address_university.id:
                raise serializers.ValidationError({
                    'university': 'This university does not match the selected saved address.'
                })
            if attrs.get('university') is None:
                attrs['university'] = address_university
            if not attrs.get('delivery_address'):
                attrs['delivery_address'] = customer_address.address
            attrs.setdefault('delivery_place_id', customer_address.provider_place_id)
            if attrs.get('delivery_latitude') is None:
                attrs['delivery_latitude'] = customer_address.latitude
            if attrs.get('delivery_longitude') is None:
                attrs['delivery_longitude'] = customer_address.longitude

        if self.instance is None:
            cart = Cart.objects.filter(customer=customer).first() if customer else None
            if 'vendor_notes' not in self.initial_data:
                attrs['vendor_notes'] = cart.vendor_notes if cart else ''
            if 'delivery_notes' not in self.initial_data:
                if cart and cart.delivery_notes:
                    attrs['delivery_notes'] = cart.delivery_notes
                elif customer_address:
                    attrs['delivery_notes'] = customer_address.delivery_instructions
                else:
                    attrs['delivery_notes'] = ''

        latitude = attrs.get(
            'delivery_latitude',
            getattr(self.instance, 'delivery_latitude', None),
        )
        longitude = attrs.get(
            'delivery_longitude',
            getattr(self.instance, 'delivery_longitude', None),
        )
        if (latitude is None) != (longitude is None):
            raise serializers.ValidationError({
                'delivery_location': 'Latitude and longitude must be provided together.'
            })

        university = attrs.get('university', getattr(self.instance, 'university', None))
        if self.instance is None and university is None:
            raise serializers.ValidationError({
                'university': 'This field is required.',
            })
        if self.instance is None and not attrs.get('delivery_address'):
            raise serializers.ValidationError({
                'delivery_address': 'This field is required.',
            })
        if self.instance is None and (latitude is None or longitude is None):
            raise serializers.ValidationError({
                'delivery_location': 'Delivery latitude and longitude are required.',
            })
        delivery_place_id = attrs.get(
            'delivery_place_id',
            getattr(self.instance, 'delivery_place_id', ''),
        )
        try:
            normalized_place_id = validate_google_place_id_for_university(
                university,
                delivery_place_id,
            )
        except UniversityRootPlaceSelected as error:
            raise serializers.ValidationError({
                'delivery_place_id': str(error),
            }) from error
        if 'delivery_place_id' in attrs:
            attrs['delivery_place_id'] = normalized_place_id or ''
        if university and latitude is not None and longitude is not None:
            try:
                validate_coordinates_within_university(
                    university,
                    latitude,
                    longitude,
                )
            except LocationOutsideDeliveryArea:
                raise serializers.ValidationError({
                    'delivery_location': 'This location is outside the selected university delivery area.'
                })

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('order_items')
        menu_item_ids = {item['menu_item'].id for item in items_data}
        locked_menu_items = {
            menu_item.id: menu_item
            for menu_item in MenuItem.objects.select_for_update().filter(
                id__in=menu_item_ids
            )
        }
        resolved_items = [
            (locked_menu_items[item['menu_item'].id], item['quantity'])
            for item in items_data
        ]
        subtotal = calculate_order_items_subtotal(resolved_items)
        customer = validated_data.get('customer')
        cart = (
            Cart.objects.filter(customer=customer).first()
            if customer is not None
            else None
        )
        delivery_point = {
            'latitude': validated_data['delivery_latitude'],
            'longitude': validated_data['delivery_longitude'],
            'place_id': validated_data.get('delivery_place_id', ''),
            'address': validated_data['delivery_address'],
        }
        try:
            delivery_fee = calculate_delivery_fee(
                university=validated_data['university'],
                cart=cart,
                delivery_point=delivery_point,
            )
        except DeliveryFeeConfigurationError as error:
            raise serializers.ValidationError({
                'delivery_fee': str(error),
            }) from error
        validated_data.update({
            'subtotal_amount': subtotal,
            'delivery_fee': delivery_fee,
            'total_amount': subtotal + delivery_fee,
        })
        try:
            order = create_order_with_generated_id(
                order_model=Order,
                **validated_data,
            )
        except OrderIdGenerationError as error:
            raise serializers.ValidationError({
                'order_id': str(error),
            }) from error
        OrderItem.objects.bulk_create([
            OrderItem(order=order, menu_item=menu_item, quantity=quantity)
            for menu_item, quantity in resolved_items
        ])
        return order


class VendorOrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(source='orderitem_set', many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            'id',
            'order_id',
            'items',
            'vendor_notes',
            'total_amount',
            'order_time',
        )

class DeliveryOrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(source='orderitem_set', many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            'id',
            'order_id',
            'customer_name',
            'phone_number',
            'delivery_address',
            'delivery_place_id',
            'delivery_latitude',
            'delivery_longitude',
            'university',
            'items',
            'delivery_notes',
            'order_time',
        )
