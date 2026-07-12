from django.contrib import admin

# Register your models here.
from .models import Cart, CartItem, Customer, CustomerAddress, FavoriteVendor, University, Vendor, VendorRating, MenuItem, Order, CafeteriaCategory, OrderItem

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1  

class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 1

class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer', 'customer_name', 'phone_number', 'delivery_address', 'total_amount_display', 'order_time')
    inlines = [OrderItemInline]
    
    def total_amount_display(self, obj):
        return f"₦{obj.total_amount()}"
    total_amount_display.short_description = "Total Amount"

class VendorAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'vendor_type', 'closing_time', 'average_rating', 'rating_count')
    list_filter = ('vendor_type',)
    search_fields = ('name',)

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

class UniversityAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'detection_radius_meters', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)

admin.site.register(Customer, CustomerAdmin)
admin.site.register(CustomerAddress, CustomerAddressAdmin)
admin.site.register(Cart, CartAdmin)
admin.site.register(CartItem)
admin.site.register(FavoriteVendor, FavoriteVendorAdmin)
admin.site.register(Vendor, VendorAdmin)
admin.site.register(VendorRating, VendorRatingAdmin)
admin.site.register(MenuItem)
admin.site.register(Order, OrderAdmin)
admin.site.register(CafeteriaCategory)
admin.site.register(OrderItem)
admin.site.register(University, UniversityAdmin)
