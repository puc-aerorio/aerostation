// AeroStation — single-drone ground station front-end.
//
// The browser never talks to the drone directly (uav_api ships no CORS): every
// command goes over the update-info websocket to Django, which issues the HTTP
// request to uav_api and pipes the response back down the same socket.
//
// The websockets live on the same Django app that served this page, so the
// browser already knows the right host and port. Deriving them from
// window.location (rather than from a hardcoded address in config.ini) is what
// keeps the interface working when the ground station is reached over the
// field network at http://192.168.x.x:8000/ instead of from localhost -- and
// removes any chance of the configured port drifting from the real one.
const WS_BASE = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host;

// --- command numbers (see [commands-list] in config.ini) ---
const CMD = {
  GPS: 20, NED: 22,
  ARM: 24, TAKEOFF: 26, LAND: 28, RTL: 30, BRAKE: 33, GUIDED: 35,
  GO_TO_GPS: 60,
  LIST_SCRIPTS: 42, UPLOAD_SCRIPT: 44, EXECUTE_SCRIPT: 46,
  CLEAR_SCRIPTS: 48, RUNNING_SCRIPTS: 50, STOP_SCRIPT: 52, SCRIPT_LOG: 54,
};

// Emitted by Django itself when an outbound request fails.
const GS_ERROR_TYPE = 900;

// Commands whose responses update a panel silently. A 2s poll would otherwise
// bury every real command result under a stream of identical notifications.
const QUIET_COMMANDS = [CMD.RUNNING_SCRIPTS, CMD.SCRIPT_LOG];

const POLL_INTERVAL = 2.0;

// Link watchdog. uav_api pushes telemetry once a second; the server-side
// activity flags in config.ini are far coarser (25s/50s), which is too slow to
// notice a lost link mid-flight, so the interface applies its own thresholds
// and takes whichever verdict is more severe.
const ON_HOLD_AFTER_MS = 3000;
const INACTIVE_AFTER_MS = 8000;

const SEVERITY = { active: 0, on_hold: 1, inactive: 2 };


// --- state -----------------------------------------------------------------

// The single tracked drone. Multi-drone plumbing survives on the Django side;
// the interface simply always targets the most recent drone to report in.
var droneState = { id: null, status: 'inactive' };
var lastFrameAt = 0;
var lastFrameStamp = null;

var missionRunning = null;   // the running script entry, or null when idle
var drawerOpen = false;
var activeLogPoll = null;    // params of the script-log poll currently running
var missionPollOn = false;

var postSocket = null;
var updateSocket = null;


// --- websockets ------------------------------------------------------------

function connectSockets() {
  // Drop any previous pair first, or a reconnect leaves the old
  // update-periodically socket open and every frame arrives twice.
  [postSocket, updateSocket].forEach(function (sock) {
    if (!sock) return;
    sock.onclose = null;
    sock.onmessage = null;
    try { sock.close(); } catch (e) { /* already closing */ }
  });

  postSocket = new WebSocket(`${WS_BASE}/ws/update-info/`);
  updateSocket = new WebSocket(`${WS_BASE}/ws/update-periodically/`);

  postSocket.onmessage = handleFrame;
  updateSocket.onmessage = handleFrame;

  postSocket.addEventListener('open', function () {
    setLinkStatus(true);
    // Poll state lives on the Django consumer and dies with the socket, so
    // anything that should be running has to be re-armed on every (re)connect.
    missionPollOn = false;
    activeLogPoll = null;
    syncPolls();
    sendCommand(CMD.LIST_SCRIPTS);
  });

  postSocket.addEventListener('close', function () {
    setLinkStatus(false);
    // Reconnect so a Django restart doesn't require reloading the page
    // mid-flight.
    setTimeout(connectSockets, 2000);
  });

  updateSocket.onclose = function () {
    console.error('Update socket closed');
  };
}

function setLinkStatus(online) {
  const el = document.getElementById('link-status');
  el.textContent = online ? 'Ground station: online' : 'Ground station: offline';
  el.className = 'link-status ' + (online ? 'online' : 'offline');
}


// --- sending ---------------------------------------------------------------

function getDeviceReceiver() {
  // Single drone: always the one we are tracking. Before the first telemetry
  // frame arrives we fall back to the "all" sentinel Django still understands.
  return droneState.id !== null ? droneState.id : 'all';
}

function sendCommand(cmdNumber, buttonType, data, params, interval) {
  const payload = {
    id: 1,
    type: cmdNumber,
    button_type: buttonType || 'default',
    data: data || {},
    params: params || {},
    receiver: getDeviceReceiver(),
  };
  if (interval !== undefined && interval !== null) payload.interval = interval;

  if (postSocket && postSocket.readyState === WebSocket.OPEN) {
    postSocket.send(JSON.stringify(payload));
    return true;
  }
  toast('Not connected', 'Ground station socket is not open.', 'error');
  return false;
}


// --- polling ---------------------------------------------------------------

function sameParams(a, b) {
  if (!a || !b) return a === b;
  return JSON.stringify(a) === JSON.stringify(b);
}

function syncPolls() {
  // Mission status polls for as long as the page is visible.
  const wantMission = !document.hidden;
  if (wantMission !== missionPollOn) {
    sendCommand(CMD.RUNNING_SCRIPTS, wantMission ? 'poll_start' : 'poll_stop',
                {}, {}, POLL_INTERVAL);
    missionPollOn = wantMission;
  }

  // The log tail only polls while the drawer is open and a script is running.
  const wantLog = (!document.hidden && drawerOpen && missionRunning)
    ? { script_name: missionRunning.script,
        stream: document.getElementById('select-stream').value,
        tail: 200 }
    : null;

  if (!sameParams(wantLog, activeLogPoll)) {
    if (activeLogPoll) sendCommand(CMD.SCRIPT_LOG, 'poll_stop', {}, activeLogPoll);
    if (wantLog) sendCommand(CMD.SCRIPT_LOG, 'poll_start', {}, wantLog, POLL_INTERVAL);
    activeLogPoll = wantLog;
  }
}

document.addEventListener('visibilitychange', syncPolls);


// --- incoming frames -------------------------------------------------------

function handleFrame(msg) {
  var frame;
  try {
    frame = JSON.parse(msg.data);
  } catch (e) {
    console.warn('Non-JSON frame', msg.data);
    return;
  }

  // Django stamps every command response with the command that produced it.
  // uav_api's own numeric "type" codes are deprecated upstream, so they are
  // only consulted for frames that never went through the command path.
  if (frame.gs_command !== undefined) {
    handleCommandResponse(frame.gs_command, frame);
    return;
  }

  if (frame.type === 102) {
    applyTelemetry(frame);
  }
  // 101/103 are Django's own POST acks — nothing to show.
}

function handleCommandResponse(command, frame) {
  const failed = frame.type === GS_ERROR_TYPE;

  switch (command) {
    case CMD.RUNNING_SCRIPTS:
      if (!failed) applyMissionStatus(frame.scripts || []);
      return;                                   // quiet: polled every 2s

    case CMD.SCRIPT_LOG:
      applyMissionLog(frame, failed);
      return;                                   // quiet: polled every 2s

    case CMD.LIST_SCRIPTS:
      if (!failed) applyScriptList(frame.scripts || []);
      else toast('Script list failed', describe(frame), 'error');
      return;

    case CMD.UPLOAD_SCRIPT:
      report(frame, failed, 'Upload');
      if (!failed) sendCommand(CMD.LIST_SCRIPTS);
      return;

    case CMD.EXECUTE_SCRIPT:
      report(frame, failed, 'Run mission');
      return;

    case CMD.STOP_SCRIPT:
      report(frame, failed, 'Stop mission');
      return;

    case CMD.CLEAR_SCRIPTS:
      report(frame, failed, 'Clear scripts');
      if (!failed) {
        applyScriptList([]);
        sendCommand(CMD.LIST_SCRIPTS);
      }
      return;

    case CMD.GO_TO_GPS:
      report(frame, failed, 'Go to');
      // The destination marker only means "commanded" -- if the vehicle
      // refused (not armed, not in GUIDED, out of range), drop it again so the
      // map never shows a target that was never accepted.
      if (failed) aeroMap.clearTarget();
      return;

    case CMD.GPS:
    case CMD.NED:
      toast(command === CMD.GPS ? 'GPS position' : 'NED position',
            failed ? describe(frame) : describePosition(frame),
            failed ? 'error' : 'ok');
      return;

    default:
      report(frame, failed, commandName(command));
      return;
  }
}

function commandName(command) {
  for (var key in CMD) if (CMD[key] === command) return key.replace(/_/g, ' ').toLowerCase();
  return 'command';
}

function describe(frame) {
  if (frame.error) return frame.error;
  if (frame.detail) return frame.detail;
  // /command and /movement put a human sentence in "result"; /telemetry and
  // /mission put the literal "success" there and the payload under "info".
  if (frame.result && frame.result !== 'success') return frame.result;
  if (typeof frame.info === 'string') return frame.info;
  if (frame.info !== undefined) return JSON.stringify(frame.info);
  return 'Success';
}

function describePosition(frame) {
  const info = frame.info || {};
  const p = info.position || {};
  if (p.lat !== undefined) {
    return `lat ${fmtNum(p.lat, 6)}  lon ${fmtNum(p.lon, 6)}\n` +
           `alt ${fmtNum(p.alt, 1)} m MSL  ·  ${fmtNum(p.relative_alt, 1)} m above home\n` +
           `heading ${fmtNum(info.heading, 0)}°`;
  }
  if (p.x !== undefined) {
    return `N ${fmtNum(p.x, 2)} m  E ${fmtNum(p.y, 2)} m  D ${fmtNum(p.z, 2)} m`;
  }
  return describe(frame);
}

function report(frame, failed, title) {
  toast(title, describe(frame), failed ? 'error' : 'ok');
}


// --- telemetry -------------------------------------------------------------

// ArduPilot reports lat/lon as exactly 0 in GLOBAL_POSITION_INT until the EKF
// has a home origin, so for the first seconds after power-on the drone claims
// to be at Null Island, 600 km off West Africa. Rendering that would drop the
// marker in the Atlantic AND recentre the map there, since the first fix is
// what the map centres on.
function hasValidFix(lat, lng) {
  if (!isFinite(lat) || !isFinite(lng)) return false;
  // Exactly (0, 0) is the no-fix sentinel. A real site is never within a
  // metre of it, so the epsilon costs nothing and also catches -0.
  if (Math.abs(lat) < 1e-7 && Math.abs(lng) < 1e-7) return false;
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return false;
  return true;
}

function fmtNum(value, digits) {
  if (value === undefined || value === null || value === '' || isNaN(value)) return '—';
  return Number(value).toFixed(digits);
}

function applyTelemetry(frame) {
  droneState.id = frame.id;
  droneState.device = frame.device || 'uav';
  droneState.serverStatus = frame.status || 'active';
  droneState.lat = parseFloat(frame.lat);
  droneState.lng = parseFloat(frame.lng);
  droneState.alt = parseFloat(frame.alt);
  droneState.heading = parseFloat(frame.heading);
  droneState.groundspeed = parseFloat(frame.ground_speed);
  droneState.airspeed = parseFloat(frame.air_speed);
  droneState.battery = parseFloat(frame.battery_percent);
  droneState.flightMode = frame.flight_mode;
  droneState.readyToArm = frame.ready_to_arm;

  // The periodic consumer replays the last known device every 20s with a
  // recomputed status but the ORIGINAL timestamp. Keying the watchdog on that
  // timestamp is what separates a genuinely fresh push from a replay -- keying
  // it on the status would let a replay mask a link that has already dropped.
  if (frame.time && frame.time !== lastFrameStamp) {
    lastFrameStamp = frame.time;
    lastFrameAt = Date.now();
  }

  droneState.hasFix = hasValidFix(droneState.lat, droneState.lng);

  renderTelemetry();

  // No marker, no track point, no recentre until the position is real. If the
  // fix is lost later the marker simply stops moving, which is far more useful
  // than watching it teleport to the Atlantic.
  if (droneState.hasFix) {
    try {
      aeroMap.setDrone(droneState.lat, droneState.lng, droneState.status, droneState.heading);
    } catch (e) {
      console.error('Error updating the map', e);
    }
  }
}

function currentStatus() {
  const age = Date.now() - lastFrameAt;
  var watchdog = 'active';
  if (!lastFrameAt || age > INACTIVE_AFTER_MS) watchdog = 'inactive';
  else if (age > ON_HOLD_AFTER_MS) watchdog = 'on_hold';

  const server = droneState.serverStatus || 'inactive';
  return SEVERITY[watchdog] >= SEVERITY[server] ? watchdog : server;
}

function renderTelemetry() {
  const status = currentStatus();
  droneState.status = status;

  const waiting = droneState.id === null;
  document.getElementById('status-dot').className =
    'status-dot' + (waiting ? '' : ' status-' + status);
  document.getElementById('status-label').textContent =
    waiting ? 'waiting for drone'
            : (status === 'active' ? 'connected' : status.replace('_', ' '));
  document.getElementById('drone-id').textContent =
    droneState.id !== null ? (droneState.device || 'uav').toUpperCase() + '-' + droneState.id : '—';

  // Stale numbers during a lost link are worse than no numbers.
  const live = status !== 'inactive';
  const v = function (value, digits) { return live ? fmtNum(value, digits) : '—'; };

  // Showing 0.000000 would look like a real reading; "no fix" cannot.
  const latEl = document.getElementById('tm-lat');
  const lngEl = document.getElementById('tm-lng');
  if (live && !droneState.hasFix) {
    latEl.textContent = lngEl.textContent = 'no fix';
    latEl.className = lngEl.className = 'tm-value warn';
  } else {
    latEl.textContent = v(droneState.lat, 6);
    lngEl.textContent = v(droneState.lng, 6);
    latEl.className = lngEl.className = 'tm-value';
  }
  document.getElementById('tm-alt').textContent = live ? fmtNum(droneState.alt, 1) + ' m' : '—';
  document.getElementById('tm-heading').textContent = live ? fmtNum(droneState.heading, 0) + '°' : '—';
  document.getElementById('tm-gspd').textContent = live ? fmtNum(droneState.groundspeed, 2) + ' m/s' : '—';
  document.getElementById('tm-aspd').textContent = live ? fmtNum(droneState.airspeed, 2) + ' m/s' : '—';

  // null before the drone's first heartbeat, and blanked on a dead link --
  // a stale mode is exactly the kind of thing an operator would act on.
  const mode = document.getElementById('tm-mode');
  if (live && droneState.flightMode) {
    mode.textContent = droneState.flightMode;
    mode.className = 'flight-mode';
  } else {
    mode.textContent = '—';
    mode.className = 'flight-mode unknown';
  }

  // uav_api sends -1 when the battery level is not estimated.
  const battery = document.getElementById('tm-battery');
  battery.textContent = (live && droneState.battery >= 0) ? fmtNum(droneState.battery, 0) + ' %' : '—';
  battery.className = 'tm-value' + (live && droneState.battery >= 0 && droneState.battery < 25 ? ' warn' : '');

  // null (no SYS_STATUS for 5s upstream) must stay distinct from false.
  const arm = document.getElementById('tm-arm');
  if (!live || droneState.readyToArm === null || droneState.readyToArm === undefined) {
    arm.textContent = '—'; arm.className = 'tm-value';
  } else if (droneState.readyToArm) {
    arm.textContent = '✓ ready'; arm.className = 'tm-value ok';
  } else {
    arm.textContent = '✗ not ready'; arm.className = 'tm-value warn';
  }
}

// Re-render on a timer so the watchdog downgrades the status even when no
// further frames arrive.
setInterval(renderTelemetry, 1000);


// --- mission ---------------------------------------------------------------

function applyScriptList(scripts) {
  const select = document.getElementById('select-script');
  const previous = select.value;

  select.innerHTML = '<option value="" disabled selected>Select a script</option>';
  scripts.forEach(function (name) {
    select.add(new Option(name, name));
  });
  if (previous && scripts.indexOf(previous) !== -1) select.value = previous;

  updateMissionControls();
}

function applyMissionStatus(scripts) {
  const previous = missionRunning ? missionRunning.script : null;
  missionRunning = scripts.length ? scripts[0] : null;

  const dot = document.getElementById('mission-dot');
  const text = document.getElementById('mission-status-text');

  if (!missionRunning) {
    dot.className = 'status-dot';
    text.textContent = 'Idle — no mission running';
  } else {
    dot.className = 'status-dot running';
    var label = 'Running ' + missionRunning.script + '  ·  started ' +
                formatStamp(missionRunning.started_at);
    // uav_api itself allows concurrent scripts; AeroStation does not start
    // them, but it must not hide one started from outside.
    if (scripts.length > 1) {
      label += '  ·  ⚠ ' + (scripts.length - 1) + ' other script(s) also running: ' +
               scripts.slice(1).map(function (s) { return s.script; }).join(', ');
    }
    text.textContent = label;
  }

  updateMissionControls();
  if (previous !== (missionRunning ? missionRunning.script : null)) syncPolls();
}

function formatStamp(stamp) {
  // uav_api formats these as YYYYMMDD_HHMMSS.
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(stamp || '');
  return m ? `${m[4]}:${m[5]}:${m[6]}` : (stamp || '');
}

function updateMissionControls() {
  const selected = document.getElementById('select-script').value;
  document.getElementById('execute').disabled = !selected || !!missionRunning;
  document.getElementById('stop-mission').disabled = !missionRunning;
}

function applyMissionLog(frame, failed) {
  const body = document.getElementById('mission-output');
  const stream = document.getElementById('select-stream').value;
  body.className = 'drawer-body' + (stream === 'err' ? ' stream-err' : '');

  if (failed) {
    // A 404 here is routine: the script has not written this stream yet.
    body.textContent = 'No ' + (stream === 'err' ? 'stderr' : 'stdout') + ' available yet.';
    return;
  }

  const lines = frame.lines || [];
  const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 30;
  body.textContent = lines.length ? lines.join('\n') : '(no output yet)';
  if (atBottom) body.scrollTop = body.scrollHeight;
}


// --- toasts ----------------------------------------------------------------

var toastStack = null;

function toast(title, body, kind) {
  if (!toastStack) {
    toastStack = document.createElement('div');
    toastStack.className = 'toast-stack';
    document.body.appendChild(toastStack);
  }

  const el = document.createElement('div');
  el.className = 'toast' + (kind ? ' ' + kind : '');

  const t = document.createElement('div');
  t.className = 'toast-title';
  t.textContent = title;

  const b = document.createElement('div');
  b.className = 'toast-body';
  b.textContent = body;

  el.appendChild(t);
  el.appendChild(b);
  toastStack.appendChild(el);

  setTimeout(function () { el.remove(); }, kind === 'error' ? 9000 : 5000);
  while (toastStack.children.length > 4) toastStack.firstChild.remove();
}


// --- command wiring --------------------------------------------------------

function confirmThen(message, fn) {
  return function () { if (window.confirm(message)) fn(); };
}

document.getElementById('arm').onclick = function () {
  sendCommand(CMD.ARM);
};

document.getElementById('takeoff').onclick = function () {
  const alt = parseInt(document.getElementById('takeoff-alt').value, 10);
  if (!Number.isInteger(alt) || alt < 1 || alt > 500) {
    toast('Takeoff', 'Altitude must be a whole number between 1 and 500 m.', 'error');
    return;
  }
  if (window.confirm(`Take off to ${alt} m?`)) sendCommand(CMD.TAKEOFF, 'default', {}, { alt: alt });
};

document.getElementById('land').onclick = confirmThen(
  'Land the aircraft now? It will descend and disarm.',
  function () { sendCommand(CMD.LAND); });

document.getElementById('rtl').onclick = confirmThen(
  'Return to launch? The aircraft will fly home, land and disarm.',
  function () { sendCommand(CMD.RTL); });

document.getElementById('brake').onclick = confirmThen(
  'Brake? The aircraft stops hard and holds position. Movement commands stay ' +
  'disabled until you press Guided.',
  function () { sendCommand(CMD.BRAKE); });

document.getElementById('guided').onclick = function () {
  sendCommand(CMD.GUIDED);
};

document.getElementById('position-gps').onclick = function () { sendCommand(CMD.GPS); };
document.getElementById('position-ned').onclick = function () { sendCommand(CMD.NED); };


// --- click-to-fly ----------------------------------------------------------

var gotoArmed = false;

function setGotoArmed(armed) {
  gotoArmed = armed;
  const btn = document.getElementById('goto-arm');
  btn.setAttribute('aria-pressed', String(armed));
  btn.textContent = armed ? 'Click map to fly — ARMED' : 'Click map to fly';
  document.getElementById('goto-hint').hidden = !armed;
  aeroMap.setGotoArmed(armed);
  if (!armed) {
    aeroMap.clearTarget();
    document.getElementById('goto-clear').hidden = true;
  }
}

document.getElementById('goto-arm').onclick = function () {
  setGotoArmed(!gotoArmed);
};

document.getElementById('goto-clear').onclick = function () {
  aeroMap.clearTarget();
  this.hidden = true;
};

aeroMap.setClickHandler(function (lat, lng) {
  const alt = parseInt(document.getElementById('goto-alt').value, 10);
  if (!Number.isInteger(alt) || alt < 0 || alt > 10000) {
    toast('Go to', 'Altitude must be a whole number between 0 and 10000 m.', 'error');
    return;
  }

  if (droneState.id === null) {
    toast('Go to', 'No drone is reporting yet.', 'error');
    return;
  }

  // Distance is the number that tells an operator whether they mis-clicked.
  const metres = aeroMap.distanceFromDrone(lat, lng);
  const distance = metres === null ? 'unknown' : Math.round(metres) + ' m';
  const lookAt = document.getElementById('goto-look').checked;

  if (!window.confirm(
      `Fly to ${lat.toFixed(6)}, ${lng.toFixed(6)}?\n\n` +
      `Altitude: ${alt} m above home\n` +
      `Distance from aircraft: ${distance}\n` +
      (lookAt ? 'Yaw: face travel direction\n' : '') +
      `\nThe aircraft must be armed and in GUIDED mode. This returns ` +
      `immediately — it does not wait for arrival.`)) {
    return;
  }

  aeroMap.setTarget(lat, lng);
  document.getElementById('goto-clear').hidden = false;

  // uav_api's Gps_pos model spells longitude "long" (telemetry replies spell
  // it "lon", and the drone's own push spells it "lng" -- three spellings).
  sendCommand(CMD.GO_TO_GPS, 'default', {
    lat: lat,
    long: lng,
    alt: alt,
    look_at_target: lookAt,
  });
});


// --- mission wiring --------------------------------------------------------

const fileInput = document.getElementById('upload');
const submitBtn = document.getElementById('submit-file');
const fileName = document.getElementById('upload-filename');

fileInput.addEventListener('change', function () {
  const file = fileInput.files[0];
  if (!file) {
    fileName.textContent = '';
    submitBtn.disabled = true;
    return;
  }
  // uav_api's execute-script force-appends .py, so an uploaded .sh could never
  // be run — reject it here rather than letting it fail later.
  if (!file.name.endsWith('.py')) {
    toast('Upload', 'Only .py mission scripts can be run by uav_api.', 'error');
    fileInput.value = '';
    fileName.textContent = '';
    submitBtn.disabled = true;
    return;
  }
  fileName.textContent = file.name;
  submitBtn.disabled = false;
});

submitBtn.onclick = function () {
  const file = fileInput.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = function (event) {
    sendCommand(CMD.UPLOAD_SCRIPT, 'upload', {
      filename: file.name,
      content: event.target.result.split(',')[1],   // strip the data: prefix
      type: 'text/x-python',
    });
    fileInput.value = '';
    fileName.textContent = '';
    submitBtn.disabled = true;
  };
  reader.readAsDataURL(file);
};

document.getElementById('refresh-file-list').onclick = function () {
  sendCommand(CMD.LIST_SCRIPTS);
};

document.getElementById('select-script').addEventListener('change', updateMissionControls);

document.getElementById('execute').onclick = function () {
  const script = document.getElementById('select-script').value;
  if (!script) return;
  if (!window.confirm(`Run mission "${script}"?`)) return;
  sendCommand(CMD.EXECUTE_SCRIPT, 'default', { script_name: script });
};

document.getElementById('stop-mission').onclick = function () {
  if (!missionRunning) return;
  if (!window.confirm(
      `Stop "${missionRunning.script}"?\n\n` +
      'This kills the script only — it does NOT stop the aircraft. Unless the ' +
      'script lands on interrupt, the drone holds its last commanded state. ' +
      'Use Land or RTL to bring it down.')) return;
  sendCommand(CMD.STOP_SCRIPT, 'default', { script_name: missionRunning.script });
};

document.getElementById('clear-scripts').onclick = confirmThen(
  'Delete every uploaded script from the drone? A running mission keeps running.',
  function () { sendCommand(CMD.CLEAR_SCRIPTS); });


// --- output drawer ---------------------------------------------------------

document.getElementById('drawer-toggle').onclick = function () {
  drawerOpen = !drawerOpen;
  document.getElementById('mission-output').hidden = !drawerOpen;
  document.getElementById('drawer-caret').className = 'drawer-caret' + (drawerOpen ? ' open' : '');
  this.setAttribute('aria-expanded', String(drawerOpen));
  syncPolls();
};

document.getElementById('select-stream').addEventListener('change', function () {
  document.getElementById('mission-output').textContent = 'Loading…';
  syncPolls();
});


// --- start -----------------------------------------------------------------

// Leaflet has no load callback of its own (Google Maps used to call initMap),
// so the map is started here, before the first telemetry frame can arrive.
try {
  aeroMap.initMap();
} catch (e) {
  console.error('Map failed to initialise', e);
}

renderTelemetry();
updateMissionControls();
setGotoArmed(false);
connectSockets();
