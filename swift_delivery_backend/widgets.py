from django.conf import settings
from django.contrib.gis.forms import BaseGeometryWidget


class GoogleMapsMultiPolygonWidget(BaseGeometryWidget):
    """Edit a GeoDjango MultiPolygon using the Google Maps JavaScript API."""

    template_name = 'swift_delivery_backend/widgets/google_maps_multipolygon.html'
    map_srid = 4326
    default_lon = 0
    default_lat = 0
    default_zoom = 12

    class Media:
        css = {
            'all': (
                'swift_delivery_backend/admin/google_maps_delivery_area.css',
            ),
        }
        js = (
            'swift_delivery_backend/admin/google_maps_delivery_area.js',
        )

    def __init__(self, attrs=None):
        widget_attrs = {
            'default_lon': self.default_lon,
            'default_lat': self.default_lat,
            'default_zoom': self.default_zoom,
        }
        if attrs:
            widget_attrs.update(attrs)
        super().__init__(widget_attrs)

    def serialize(self, value):
        return value.json if value else ''

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['google_maps_api_key'] = getattr(
            settings,
            'GOOGLE_MAPS_ADMIN_BROWSER_API_KEY',
            '',
        )
        return context
