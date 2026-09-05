(function () {
    'use strict';

    const pendingEditors = [];
    let mapsScriptRequested = false;

    function setStatus(config, message, isError) {
        const root = document.getElementById(config.rootId);
        const status = root && root.querySelector('[data-role="status"]');
        if (!status) return;
        status.textContent = message;
        status.classList.toggle('is-error', Boolean(isError));
    }

    function reportMapsFailure(message) {
        pendingEditors.forEach((config) => setStatus(config, message, true));
    }

    function loadMaps(config) {
        pendingEditors.push(config);

        if (window.google && window.google.maps) {
            initializePendingEditors();
            return;
        }

        if (!config.apiKey) {
            setStatus(
                config,
                'Google Maps is not configured. Set GOOGLE_MAPS_ADMIN_BROWSER_API_KEY on the server.',
                true
            );
            return;
        }

        if (mapsScriptRequested) return;
        mapsScriptRequested = true;

        const script = document.createElement('script');
        script.src = 'https://maps.googleapis.com/maps/api/js?key=' +
            encodeURIComponent(config.apiKey) +
            '&callback=swiftDeliveryGoogleMapsReady&loading=async&v=weekly';
        script.async = true;
        script.defer = true;
        script.onerror = function () {
            reportMapsFailure('Google Maps could not be loaded. Check the browser key and its website restrictions.');
        };
        document.head.appendChild(script);
    }

    function initializePendingEditors() {
        while (pendingEditors.length) {
            const config = pendingEditors.shift();
            if (!document.getElementById(config.rootId)) continue;
            try {
                new DeliveryAreaEditor(config);
            } catch (error) {
                setStatus(config, 'The delivery-area map could not be initialized.', true);
                window.console.error('Delivery-area map initialization failed:', error);
            }
        }
    }

    window.swiftDeliveryGoogleMapsReady = initializePendingEditors;
    window.gm_authFailure = function () {
        reportMapsFailure('Google Maps rejected the browser key. Check its API and website restrictions.');
    };

    class DeliveryAreaEditor {
        constructor(config) {
            this.config = config;
            this.root = document.getElementById(config.rootId);
            this.input = document.getElementById(config.inputId);
            this.mapElement = document.getElementById(config.mapId);
            this.polygons = [];
            this.selectedPolygon = null;
            this.draftPoints = [];
            this.draftMarkers = [];
            this.draftLine = null;
            this.drawing = false;

            this.buttons = {};
            this.root.querySelectorAll('[data-action]').forEach((button) => {
                this.buttons[button.dataset.action] = button;
            });
            this.zoomDisplay = this.root.querySelector('[data-role="zoom"]');

            this.map = new google.maps.Map(this.mapElement, {
                center: {
                    lat: Number(config.defaultLatitude),
                    lng: Number(config.defaultLongitude),
                },
                zoom: Number(config.defaultZoom),
                mapTypeControl: true,
                streetViewControl: false,
                fullscreenControl: true,
                clickableIcons: false,
            });

            this.bindControls();
            this.loadExistingGeometry();
            this.updateZoomDisplay();
            this.map.addListener('zoom_changed', () => this.updateZoomDisplay());
            this.updateControls();
            this.setStatus(
                this.polygons.length
                    ? 'Delivery area loaded. Select a polygon or start drawing another one.'
                    : 'No delivery polygon is saved yet.'
            );
        }

        bindControls() {
            this.buttons.start.addEventListener('click', () => this.startDrawing());
            this.buttons.finish.addEventListener('click', () => this.finishDrawing());
            this.buttons.undo.addEventListener('click', () => this.undoPoint());
            this.buttons.cancel.addEventListener('click', () => this.cancelDrawing());
            this.buttons.delete.addEventListener('click', () => this.deleteSelected());
            this.buttons.clear.addEventListener('click', () => this.clearAll());

            this.map.addListener('click', (event) => {
                if (this.drawing && event.latLng) this.addDraftPoint(event.latLng);
            });

            if (this.config.disabled) {
                Object.values(this.buttons).forEach((button) => { button.disabled = true; });
            }
        }

        updateZoomDisplay() {
            if (!this.zoomDisplay) return;
            const zoom = this.map.getZoom();
            this.zoomDisplay.textContent = zoom === undefined ? 'Zoom: —' : 'Zoom: ' + zoom;
        }

        loadExistingGeometry() {
            const rawValue = this.input.value.trim();
            if (!rawValue) return;

            let geometry;
            try {
                geometry = JSON.parse(rawValue);
            } catch (error) {
                this.setStatus('The saved delivery area is not valid GeoJSON.', true);
                return;
            }

            let polygonCoordinates = [];
            if (geometry.type === 'MultiPolygon') polygonCoordinates = geometry.coordinates;
            if (geometry.type === 'Polygon') polygonCoordinates = [geometry.coordinates];
            if (!polygonCoordinates.length) {
                this.setStatus('The saved geometry must be a Polygon or MultiPolygon.', true);
                return;
            }

            const bounds = new google.maps.LatLngBounds();
            polygonCoordinates.forEach((rings) => {
                const paths = rings.map((ring) => {
                    const withoutClosingPoint = ring.length > 1 &&
                        ring[0][0] === ring[ring.length - 1][0] &&
                        ring[0][1] === ring[ring.length - 1][1]
                        ? ring.slice(0, -1)
                        : ring;
                    return withoutClosingPoint.map((coordinate) => {
                        const point = { lat: Number(coordinate[1]), lng: Number(coordinate[0]) };
                        bounds.extend(point);
                        return point;
                    });
                });
                this.addPolygon(paths);
            });

            if (!bounds.isEmpty()) this.map.fitBounds(bounds, 40);
        }

        addPolygon(paths) {
            const polygon = new google.maps.Polygon({
                map: this.map,
                paths: paths,
                editable: !this.config.disabled,
                clickable: !this.config.disabled,
                fillColor: '#2f7d32',
                fillOpacity: 0.24,
                strokeColor: '#1b5e20',
                strokeOpacity: 0.95,
                strokeWeight: 2,
            });

            this.polygons.push(polygon);
            polygon.addListener('click', () => this.selectPolygon(polygon));
            polygon.getPaths().forEach((path) => {
                path.addListener('insert_at', () => this.serialize());
                path.addListener('remove_at', () => this.serialize());
                path.addListener('set_at', () => this.serialize());
            });
            return polygon;
        }

        selectPolygon(polygon) {
            if (this.drawing || this.config.disabled) return;
            this.selectedPolygon = polygon;
            this.polygons.forEach((candidate) => {
                candidate.setOptions({
                    strokeColor: candidate === polygon ? '#c62828' : '#1b5e20',
                    strokeWeight: candidate === polygon ? 4 : 2,
                });
            });
            this.setStatus('Polygon selected. Drag a vertex to edit it or choose Delete selected.');
            this.updateControls();
        }

        startDrawing() {
            if (this.config.disabled || this.drawing) return;
            this.selectedPolygon = null;
            this.polygons.forEach((polygon) => polygon.setOptions({ strokeColor: '#1b5e20', strokeWeight: 2 }));
            this.drawing = true;
            this.map.setOptions({ draggableCursor: 'crosshair' });
            this.setStatus('Drawing started. Click at least three boundary points on the map.');
            this.updateControls();
        }

        addDraftPoint(latLng) {
            this.draftPoints.push(latLng);
            this.draftMarkers.push(new google.maps.Circle({
                map: this.map,
                center: latLng,
                radius: 2.5,
                fillColor: '#c62828',
                fillOpacity: 1,
                strokeColor: '#ffffff',
                strokeWeight: 1,
                clickable: false,
            }));

            if (!this.draftLine) {
                this.draftLine = new google.maps.Polyline({
                    map: this.map,
                    path: this.draftPoints,
                    strokeColor: '#c62828',
                    strokeOpacity: 1,
                    strokeWeight: 3,
                    clickable: false,
                });
            } else {
                this.draftLine.setPath(this.draftPoints);
            }
            this.setStatus(this.draftPoints.length + ' boundary point(s) selected.');
            this.updateControls();
        }

        undoPoint() {
            if (!this.drawing || !this.draftPoints.length) return;
            this.draftPoints.pop();
            const marker = this.draftMarkers.pop();
            marker.setMap(null);
            if (this.draftLine) this.draftLine.setPath(this.draftPoints);
            this.setStatus(this.draftPoints.length + ' boundary point(s) selected.');
            this.updateControls();
        }

        finishDrawing() {
            if (!this.drawing || this.draftPoints.length < 3) return;
            const polygon = this.addPolygon(this.draftPoints.slice());
            this.discardDraft();
            this.drawing = false;
            this.map.setOptions({ draggableCursor: null });
            this.selectPolygon(polygon);
            this.serialize();
            this.setStatus('Polygon added. Save the university to keep this delivery area.');
            this.updateControls();
        }

        cancelDrawing() {
            if (!this.drawing) return;
            this.discardDraft();
            this.drawing = false;
            this.map.setOptions({ draggableCursor: null });
            this.setStatus('Drawing cancelled. The saved delivery area was not changed.');
            this.updateControls();
        }

        discardDraft() {
            this.draftMarkers.forEach((marker) => marker.setMap(null));
            this.draftMarkers = [];
            this.draftPoints = [];
            if (this.draftLine) this.draftLine.setMap(null);
            this.draftLine = null;
        }

        deleteSelected() {
            if (!this.selectedPolygon || this.config.disabled) return;
            this.selectedPolygon.setMap(null);
            this.polygons = this.polygons.filter((polygon) => polygon !== this.selectedPolygon);
            this.selectedPolygon = null;
            this.serialize();
            this.setStatus('Selected polygon removed. Save the university to keep this change.');
            this.updateControls();
        }

        clearAll() {
            if (this.config.disabled) return;
            this.discardDraft();
            this.drawing = false;
            this.map.setOptions({ draggableCursor: null });
            this.polygons.forEach((polygon) => polygon.setMap(null));
            this.polygons = [];
            this.selectedPolygon = null;
            this.serialize();
            this.setStatus('All polygons cleared. Save the university to keep this change.');
            this.updateControls();
        }

        serialize() {
            const coordinates = this.polygons.map((polygon) => {
                return polygon.getPaths().getArray().map((path) => {
                    const ring = path.getArray().map((point) => [point.lng(), point.lat()]);
                    if (ring.length) ring.push(ring[0].slice());
                    return ring;
                });
            });

            this.input.value = coordinates.length
                ? JSON.stringify({ type: 'MultiPolygon', coordinates: coordinates })
                : '';
            this.input.dispatchEvent(new Event('change', { bubbles: true }));
        }

        updateControls() {
            if (this.config.disabled) return;
            this.buttons.start.disabled = this.drawing;
            this.buttons.finish.disabled = !this.drawing || this.draftPoints.length < 3;
            this.buttons.undo.disabled = !this.drawing || !this.draftPoints.length;
            this.buttons.cancel.disabled = !this.drawing;
            this.buttons.delete.disabled = this.drawing || !this.selectedPolygon;
            this.buttons.clear.disabled = !this.drawing && !this.polygons.length;
        }

        setStatus(message, isError) {
            setStatus(this.config, message, isError);
        }
    }

    window.SwiftDeliveryGoogleMaps = {
        register: loadMaps,
    };
})();
