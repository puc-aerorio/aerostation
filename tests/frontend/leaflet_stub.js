var MAPCALLS = { setView: [], trackPoints: [], icons: [], created: false };
function mkLayer() {
  return { addTo: function () { return this; }, setLatLngs: function (a) { this._p = a; },
           addLatLng: function (p) { MAPCALLS.trackPoints.push(p); },
           setLatLng: function (p) { this._ll = p; }, getLatLng: function () { return this._ll; },
           setIcon: function (i) { MAPCALLS.icons.push(i && i._url); },
           getElement: function () { return { title: '' }; },
           bindTooltip: function () { return this; }, remove: function () {},
           on: function () {} };
}
var L = {
  map: function (id, opts) {
    MAPCALLS.created = true;
    return { on: function () {},
             getContainer: function () { return { style: {} }; },
             setView: function (p, z) { MAPCALLS.setView.push(p); },
             getZoom: function () { return 18; },
             distance: function () { return 42; } };
  },
  tileLayer: function () { return mkLayer(); },
  control: { layers: function () { return { addTo: function () {} }; } },
  polyline: function () { return mkLayer(); },
  marker: function (p, o) { var m = mkLayer(); m._ll = p; m._icon = o && o.icon; return m; },
  circleMarker: function (p) { var m = mkLayer(); m._ll = p; return m; },
  icon: function (o) { return { _url: o.iconUrl }; },
  latLng: function (a, b) { return [a, b]; },
};
