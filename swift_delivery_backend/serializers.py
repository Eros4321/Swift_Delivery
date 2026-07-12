from rest_framework import serializers
from django.contrib.auth.models import User
from .location_services import distance_in_meters
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
    University,
    Vendor,
    VendorRating,
    normalize_nigerian_phone_number,
)


class CustomerSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    preferred_university = serializers.PrimaryKeyRelatedField(
        queryset=University.objects.filter(is_active=True),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = Customer
        fields = ('id', 'phone_number', 'first_name', 'last_name', 'email', 'preferred_university', 'created_at')


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
        if university and latitude is not None and longitude is not None:
            distance = distance_in_meters(
                university.latitude,
                university.longitude,
                latitude,
                longitude,
            )
            if distance > university.detection_radius_meters:
                raise serializers.ValidationError({
                    'location': 'This address is outside the selected university delivery area.'
                })
        return attrs


class UniversitySerializer(serializers.ModelSerializer):
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
    total_amount = serializers.ReadOnlyField()
    item_count = serializers.ReadOnlyField()

    class Meta:
        model = Cart
        fields = ('id', 'customer', 'items', 'total_amount', 'item_count', 'created_at', 'updated_at')
        read_only_fields = ('customer',)
        
class OrderItemSerializer(serializers.ModelSerializer):
    menu_item_name = serializers.ReadOnlyField(source='menu_item.name')
    price = serializers.ReadOnlyField(source='menu_item.price')

    class Meta:
        model = OrderItem
        fields = '__all__'

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(source='orderitem_set', many=True, read_only=True)  # Include related order items
    order_items = serializers.ListField(write_only=True)
    total_amount = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = '__all__'
        read_only_fields = ('customer',)

    def validate(self, attrs):
        university = attrs.get('university')
        customer_address = attrs.get('customer_address')
        request = self.context.get('request')

        if customer_address:
            customer = None
            if request and request.user.is_authenticated:
                customer = Customer.objects.filter(user=request.user).first()
            if not customer or customer_address.customer_id != customer.id:
                raise serializers.ValidationError({
                    'customer_address': 'This address does not belong to the authenticated customer.'
                })

            address_university = customer_address.university
            if university and address_university and university.id != address_university.id:
                raise serializers.ValidationError({
                    'university': 'This university does not match the selected saved address.'
                })
            attrs.setdefault('university', address_university)
            attrs.setdefault('delivery_address', customer_address.address)
            attrs.setdefault('delivery_place_id', customer_address.provider_place_id)
            attrs.setdefault('delivery_latitude', customer_address.latitude)
            attrs.setdefault('delivery_longitude', customer_address.longitude)
            if not attrs.get('delivery_notes'):
                attrs['delivery_notes'] = customer_address.delivery_instructions

        latitude = attrs.get('delivery_latitude')
        longitude = attrs.get('delivery_longitude')
        if (latitude is None) != (longitude is None):
            raise serializers.ValidationError({
                'delivery_location': 'Latitude and longitude must be provided together.'
            })

        university = attrs.get('university')
        if university and latitude is not None and longitude is not None:
            distance = distance_in_meters(
                university.latitude,
                university.longitude,
                latitude,
                longitude,
            )
            if distance > university.detection_radius_meters:
                raise serializers.ValidationError({
                    'delivery_location': 'This location is outside the selected university delivery area.'
                })

        return attrs
        
    def get_total_amount(self, obj):
        return sum(item.menu_item.price * item.quantity for item in obj.orderitem_set.all())  
    
    def create(self, validated_data):
        items_data = validated_data.pop('order_items', [])
        order = Order.objects.create(**validated_data)

        for item_data in items_data:
            menu_item_id = item_data['menu_item']  # Extract menu item ID
            quantity = item_data['quantity']  # Extract quantity
            menu_item = MenuItem.objects.get(id=menu_item_id)  # Retrieve menu item instance

            OrderItem.objects.create(order=order, menu_item=menu_item, quantity=quantity)
        return order
