# Tests

Two layers, both standalone scripts — no pytest, no test runner.

| Suite | Needs | What it covers |
|---|---|---|
| `frontend/frontend_test.py` | `mini-racer` | Runs `main.js` and `map.js` in V8 against a stubbed DOM and Leaflet. The GPS-fix guard, where the map first centres, the status-icon mapping. |
| `integration/dispatcher_test.py` | a running server | Query params and their whitelist, DELETE, trailing slashes, throttled polling, the error envelope, poll cleanup on disconnect. |
| `integration/goto_test.py` | a running server | The click-to-fly request: method, trailing slash, and `long` vs `lng` in the body. |
| `integration/flight_mode_test.py` | a running server | `flight_mode` from the push through Django to the browser, including the `"None"` tri-state. |

## Front-end

No server needed:

```
pip install -r requirements-dev.txt
python tests/frontend/frontend_test.py
```

## Integration

These drive a real Django server and a stub standing in for `uav_api`, which
records what the dispatcher actually sent.

> **Run them on a port your browser is not using.** They default to **8002**
> for exactly this reason. A tab open on the ground station's normal port joins
> in as a second websocket client, issues its own commands and polls, and
> contaminates the assertions — the polling tests fail in ways that look like
> real bugs. This cost an afternoon once.

```
python manage.py runserver 8002 --noreload &
python tests/integration/dispatcher_test.py
python tests/integration/goto_test.py
python tests/integration/flight_mode_test.py
```

`GS_PORT` and `STUB_PORT` override the defaults (8002 and 8011).

## Flying without a drone

`fake_drone.py` stands in for `uav_api`: it serves the endpoints the interface
calls and pushes telemetry on a slow circle once a second, so the map, track
and mission panel can be exercised with nothing plugged in.

```
python manage.py runserver 8002 --noreload &
GS=http://127.0.0.1:8002 python tests/fake_drone.py
```

Then open `http://127.0.0.1:8002/`.
