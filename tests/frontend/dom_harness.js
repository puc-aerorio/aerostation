// Minimal DOM/Leaflet stand-in so main.js can be executed and observed.
var CALLS = { setDrone: [], clearTarget: 0, setTarget: [], initMap: 0 };
var ELS = {};
function el(id) {
  if (!ELS[id]) ELS[id] = {
    id: id, textContent: '', className: '', hidden: false, value: '', checked: false,
    disabled: false, innerHTML: '', title: '', style: {}, files: [],
    scrollHeight: 0, scrollTop: 0, clientHeight: 0, children: [], firstChild: null,
    setAttribute: function (k, v) { this['attr_' + k] = v; },
    getAttribute: function (k) { return this['attr_' + k]; },
    addEventListener: function () {}, removeEventListener: function () {},
    appendChild: function (c) { this.children.push(c); return c; },
    remove: function () {}, add: function (o) { this.children.push(o); },
    classList: { toggle: function () {}, add: function () {}, remove: function () {} },
    getElement: function () { return this; },
  };
  return ELS[id];
}
var document = {
  hidden: false,
  getElementById: el,
  querySelector: function (s) { return el(s.replace('#', '')); },
  createElement: function () { return el('created-' + Math.random()); },
  addEventListener: function () {},
  body: { appendChild: function () {} },
};
var window = { confirm: function () { return true; } };
var location = { protocol: 'http:', host: '127.0.0.1:8000' };
function WebSocket() {
  this.readyState = 1; this.send = function () {};
  this.addEventListener = function () {}; this.close = function () {};
}
WebSocket.OPEN = 1; WebSocket.CONNECTING = 0;
var console = { log: function(){}, warn: function(){}, error: function(){} };
function setInterval() { return 0; }
function setTimeout() { return 0; }
var Option = function (t, v) { return { text: t, value: v }; };

var aeroMap = {
  initMap: function () { CALLS.initMap++; },
  setDrone: function (lat, lng, status, hdg) { CALLS.setDrone.push([lat, lng, status, hdg]); },
  setTarget: function (lat, lng) { CALLS.setTarget.push([lat, lng]); },
  clearTarget: function () { CALLS.clearTarget++; },
  setGotoArmed: function () {}, setClickHandler: function () {},
  distanceFromDrone: function () { return 0; },
};
