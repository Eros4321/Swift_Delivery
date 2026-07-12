from django.db import models
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models import Avg, Sum, F

# Create your models here.
def normalize_nigerian_phone_number(phone_number):
    phone_number = ''.join(str(phone_number).split())
    if phone_number.startswith('0'):
        phone_number = f"+234{phone_number[1:]}"
    return phone_number


class University(models.Model):
    name = models.CharField(max_length=255, unique=True)
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    detection_radius_meters = models.PositiveIntegerField(default=3000)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Customer(models.Model):
    phone_validator = RegexValidator(
        regex=r'^(\+234|0)[789]\d{9}$',
        message='Enter a valid Nigerian phone number.',
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='customer_profile')
    phone_number = models.CharField(max_length=14, unique=True, validators=[phone_validator])
    preferred_university = models.ForeignKey(
        University,
        on_delete=models.SET_NULL,
        related_name='customers',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        self.phone_number = normalize_nigerian_phone_number(self.phone_number)

    def save(self, *args, **kwargs):
        self.phone_number = normalize_nigerian_phone_number(self.phone_number)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.first_name} {self.user.last_name}".strip() or self.phone_number


class CustomerAddress(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='addresses',
    )
    university = models.ForeignKey(
        University,
        on_delete=models.SET_NULL,
        related_name='customer_addresses',
        null=True,
        blank=True,
    )
    label = models.CharField(max_length=50)
    address = models.TextField()
    provider_place_id = models.CharField(max_length=255, blank=True)
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    delivery_instructions = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_default', '-updated_at']

    def __str__(self):
        return f"{self.label} - {self.customer}"


class Vendor(models.Model):
    class VendorType(models.TextChoices):
        CAFETERIA = 'cafeteria', 'Cafeteria'
        GRILLS = 'grills', 'Grills'
        PASTRIES = 'pastries', 'Pastries'
        DRINKS = 'drinks', 'Drinks'

    name = models.CharField(max_length=255)
    image = models.ImageField(upload_to='cafeteria_images/', blank=True, null=True)
    vendor_type = models.CharField(
        max_length=20,
        choices=VendorType.choices,
        default=VendorType.CAFETERIA,
    )
    closing_time = models.TimeField(null=True, blank=True)
    university = models.ForeignKey(
        University,
        on_delete=models.SET_NULL,
        related_name='vendors',
        null=True,
        blank=True,
    )

    def __str__(self):
        return self.name

    @property
    def average_rating(self):
        average = self.ratings.aggregate(average=Avg('rating'))['average']
        return round(average, 1) if average is not None else None

    @property
    def rating_count(self):
        return self.ratings.count()


class VendorRating(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='ratings')
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    customer_name = models.CharField(max_length=255, blank=True)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.vendor.name} - {self.rating}/5"


class FavoriteVendor(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='favorite_vendors')
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='favorited_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('customer', 'vendor')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.customer} - {self.vendor}"
    
class CafeteriaCategory(models.Model):
    name = models.CharField(max_length=255, unique=True)

    class Meta:
        verbose_name_plural = 'cafeteria categories'

    def __str__(self):
        return self.name
    
class MenuItem(models.Model):
    vendors = models.ManyToManyField(Vendor, related_name='menu_items', blank=True)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=6, decimal_places=2)
    available = models.BooleanField(default=True)
    image = models.ImageField(upload_to='menu_images/', null=True, blank=True)
    category = models.ForeignKey(CafeteriaCategory, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return self.name


class Cart(models.Model):
    customer = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name='cart')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def total_amount(self):
        return sum(item.line_total for item in self.cart_items.all())

    @property
    def item_count(self):
        return sum(item.quantity for item in self.cart_items.all())

    def __str__(self):
        return f"Cart - {self.customer}"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='cart_items')
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('cart', 'menu_item')

    @property
    def line_total(self):
        return self.menu_item.price * self.quantity

    def __str__(self):
        return f"{self.quantity} x {self.menu_item.name}"


class Order(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, related_name='orders', null=True, blank=True)
    customer_name = models.CharField(max_length=255, null=True)
    phone_number = models.CharField(max_length=20, null=True)
    delivery_address = models.TextField(null=True)
    delivery_place_id = models.CharField(max_length=255, blank=True)
    customer_address = models.ForeignKey(
        CustomerAddress,
        on_delete=models.SET_NULL,
        related_name='orders',
        null=True,
        blank=True,
    )
    delivery_latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
        null=True,
        blank=True,
    )
    delivery_longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
        null=True,
        blank=True,
    )
    university = models.ForeignKey(
        University,
        on_delete=models.SET_NULL,
        related_name='orders',
        null=True,
        blank=True,
    )
    delivery_notes = models.TextField(blank=True)
    items = models.ManyToManyField(MenuItem, through="OrderItem")
    order_time = models.DateTimeField(auto_now_add=True)
    
    def total_amount(self):
        return self.orderitem_set.aggregate(
            total=Sum(F('quantity') * F('menu_item__price'))
        )['total'] or 0

    def __str__(self):
        return f"Order {self.id} - {self.customer_name}"

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
    menu_item = models.ForeignKey(MenuItem, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.quantity} x {self.menu_item.name} (Order {self.order.id})"

