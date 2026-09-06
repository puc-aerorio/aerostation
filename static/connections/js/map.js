// AeroStation map layer — Leaflet over OpenStreetMap.
//
// Leaflet is vendored under static/connections/vendor/leaflet rather than
// pulled from a CDN: at a competition the ground station may have no route to
// the internet, and the interface must still load and run. Map *tiles* do need
// connectivity — see PRELOAD note at the bottom for flying without it.
//
// The old ground station kept one marker per drone and picked a pre-rendered
// icon per (colour, id) pair out of a 1224-file directory. AeroStation flies
// one aircraft, so the three status icons below are enough.

const DRONE_ICONS = {
  active: '/static/connections/images/drone-green.png',
  on_hold: '/static/connections/images/drone-yellow.png',
  inactive: '/static/connections/images/drone-red.png',
};

const ICON_SIZE = 38;

// Only used until the drone reports a position, at which point the map
// recentres on the real fix.
const FALLBACK_CENTER = [-15.84163738782225, -47.92686308971462];

class GroundStationMap {
  constructor() {
    this.map = null;
    this.marker = null;
    this.track = null;
    this.centered = false;
    this.icons = {};
    this.status = null;

    // Click-to-fly. Disarmed by default: a stray click on the map must never
    // command an aircraft.
    this.clickHandler = null;
    this.gotoArmed = false;
    this.target = null;
    this.targetLine = null;
  }

  buildIcons() {
    Object.keys(DRONE_ICONS).forEach((status) => {
      this.icons[status] = L.icon({
        iconUrl: DRONE_ICONS[status],
        iconSize: [ICON_SIZE, ICON_SIZE],
        iconAnchor: [ICON_SIZE / 2, ICON_SIZE / 2],   // centred: this is a top-down view, not a pin
        popupAnchor: [0, -ICON_SIZE / 2],
      });
    });
  }

  initMap() {
    this.buildIcons();

    const streets = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    });

    // The Google map this replaced defaulted to satellite view, which is what
    // you actually want over a flying field. Esri's imagery keeps that
    // available; OSM stays the default, per the layer order below.
    const satellite = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 19,
        attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics',
      });

    this.map = L.map('map', {
      center: FALLBACK_CENTER,
      zoom: 18,
      layers: [streets],
      zoomControl: true,
      attributionControl: true,
    });

    L.control.layers(
      { 'OpenStreetMap': streets, 'Satellite': satellite },
      {},
      { position: 'topright' },
    ).addTo(this.map);

    this.track = L.polyline([], {
      color: '#2a9bea',
      weight: 2,
      opacity: 0.9,
    }).addTo(this.map);

    // Dashed line from the aircraft to the destination it was last sent to.
    this.targetLine = L.polyline([], {
      color: '#f2b134',
      weight: 2,
      opacity: 0.85,
      dashArray: '6 6',
    }).addTo(this.map);

    this.map.on('click', (e) => {
      if (!this.gotoArmed || !this.clickHandler) return;
      this.clickHandler(e.latlng.lat, e.latlng.lng);
    });
  }

  // --- click-to-fly ---------------------------------------------------------

  setClickHandler(fn) {
    this.clickHandler = fn;
  }

  setGotoArmed(armed) {
    this.gotoArmed = !!armed;
    if (this.map) {
      // Crosshair is the only cue that a map click is now a flight command.
      this.map.getContainer().style.cursor = this.gotoArmed ? 'crosshair' : '';
    }
  }

  // Metres between the aircraft and a candidate destination, so the confirm
  // dialog can state how far it is being asked to fly.
  distanceFromDrone(lat, lng) {
    if (!this.map || !this.marker) return null;
    return this.map.distance(this.marker.getLatLng(), L.latLng(lat, lng));
  }

  setTarget(lat, lng) {
    if (!this.map) return;
    const position = [lat, lng];

    if (!this.target) {
      this.target = L.circleMarker(position, {
        radius: 7,
        color: '#f2b134',
        weight: 2,
        fillColor: '#f2b134',
        fillOpacity: 0.35,
      }).addTo(this.map);
    } else {
      this.target.setLatLng(position);
    }
    this.target.bindTooltip(
      `Target ${lat.toFixed(6)}, ${lng.toFixed(6)}`, { direction: 'top' });
    this.drawTargetLine();
  }

  clearTarget() {
    if (this.target) { this.target.remove(); this.target = null; }
    if (this.targetLine) this.targetLine.setLatLngs([]);
  }

  drawTargetLine() {
    if (!this.targetLine || !this.target || !this.marker) return;
    this.targetLine.setLatLngs([this.marker.getLatLng(), this.target.getLatLng()]);
  }

  iconFor(status) {
    return this.icons[status] || this.icons.inactive;
  }

  setDrone(lat, lng, status, heading) {
    if (!this.map) return;   // Leaflet has not initialised yet

    const position = [lat, lng];
    const label = 'Drone' + (isNaN(heading) ? '' : ' — heading ' + Math.round(heading) + '°');

    if (!this.marker) {
      this.marker = L.marker(position, {
        icon: this.iconFor(status),
        title: label,
      }).addTo(this.map);
    } else {
      this.marker.setLatLng(position);
      // setIcon rebuilds the DOM node, so only swap when the status changed.
      if (status !== this.status) this.marker.setIcon(this.iconFor(status));
      const el = this.marker.getElement();
      if (el) el.title = label;
    }
    this.status = status;

    // Recentre once, on the first real fix — after that the operator stays in
    // control of the viewport.
    if (!this.centered) {
      this.map.setView(position, this.map.getZoom());
      this.centered = true;
    }

    // A lost link would otherwise draw a straight line across the last gap.
    if (status !== 'inactive') this.track.addLatLng(position);

    // The target stays put; the line has to follow the aircraft.
    this.drawTargetLine();
  }

  clearTrack() {
    if (this.track) this.track.setLatLngs([]);
  }
}

var aeroMap = new GroundStationMap();

// PRELOAD: OSM/Esri tiles are fetched on demand, so a field with no uplink
// shows an empty grid. To fly offline, browse the flying site at the zoom
// levels you need while still on a network — the browser HTTP cache keeps
// them — or point the tileLayer URLs at a local tile server.
