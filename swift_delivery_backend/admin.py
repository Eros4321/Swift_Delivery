from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

# Register your models here.
from .forms import MenuItemAdminForm
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
)
from .widgets import GoogleMapsMultiPolygonWidget

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1  

class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 1

class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'order_id',
        'customer',
        'customer_name',
        'phone_number',
        'delivery_address',
        'subtotal_amount',
        'delivery_fee',
        'total_amount_display',
        'order_time',
    )
    readonly_fields = ('order_id', 'subtotal_amount', 'delivery_fee', 'total_amount')
    search_fields = ('order_id', 'customer_name', 'phone_number')
    inlines = [OrderItemInline]
    
    def total_amount_display(self, obj):
        return f"₦{obj.total_amount}"
    total_amount_display.short_description = "Total Amount"

class VendorAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'logo_uploaded',
        'vendor_type',
        'closing_time',
        'average_rating',
        'rating_count',
    )
    list_filter = ('vendor_type',)
    search_fields = ('name',)

    @admin.display(boolean=True, description='Logo')
    def logo_uploaded(self, obj):
        return bool(obj.logo)

class MenuItemAdmin(admin.ModelAdmin):
    form = MenuItemAdminForm

class VendorRatingAdmin(admin.ModelAdmin):
    list_display = ('id', 'vendor', 'rating', 'customer_name', 'created_at')
    list_filter = ('rating', 'created_at')
    search_fields = ('vendor__name', 'customer_name', 'comment')

class CustomerAdmin(admin.ModelAdmin):
    list_display = ('id', 'phone_number', 'first_name', 'last_name', 'email', 'created_at')
    search_fields = ('phone_number', 'user__first_name', 'user__last_name', 'user__email')

    def first_name(self, obj):
        return obj.user.first_name

    def last_name(self, obj):
        return obj.user.last_name

    def email(self, obj):
        return obj.user.email

class CustomerAddressAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer', 'label', 'university', 'is_default', 'updated_at')
    list_filter = ('university', 'is_default')
    search_fields = ('customer__phone_number', 'label', 'address')

class CartAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer', 'item_count', 'total_amount', 'updated_at')
    inlines = [CartItemInline]

class FavoriteVendorAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer', 'vendor', 'created_at')
    search_fields = ('customer__phone_number', 'vendor__name')

class SavedCartNoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer', 'note_type', 'note', 'created_at', 'updated_at')
    list_filter = ('note_type',)
    search_fields = ('customer__phone_number', 'note')
    readonly_fields = ('created_at', 'updated_at')

class UniversityAdmin(GISModelAdmin):
    gis_widget = GoogleMapsMultiPolygonWidget
    list_display = (
        'id',
        'name',
        'google_place_id',
        'detection_radius_meters',
        'delivery_fee',
        'has_delivery_area',
        'is_active',
    )
    list_filter = ('is_active',)
    search_fields = ('name', 'google_place_id')

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        delivery_area_field = form.base_fields.get('delivery_area')
        if obj is not None and delivery_area_field is not None:
            delivery_area_field.widget.attrs.update({
                'default_lon': float(obj.longitude),
                'default_lat': float(obj.latitude),
                'default_zoom': 16,
            })
        return form

    @admin.display(boolean=True, description='Polygon configured')
    def has_delivery_area(self, obj):
        return obj.delivery_area is not None and not obj.delivery_area.empty

admin.site.register(Customer, CustomerAdmin)
admin.site.register(CustomerAddress, CustomerAddressAdmin)
admin.site.register(Cart, CartAdmin)
admin.site.register(CartItem)
admin.site.register(FavoriteVendor, FavoriteVendorAdmin)
admin.site.register(SavedCartNote, SavedCartNoteAdmin)
admin.site.register(Vendor, VendorAdmin)
admin.site.register(VendorRating, VendorRatingAdmin)
admin.site.register(MenuItem, MenuItemAdmin)
admin.site.register(Order, OrderAdmin)
admin.site.register(CafeteriaCategory)
admin.site.register(OrderItem)
admin.site.register(University, UniversityAdmin)
