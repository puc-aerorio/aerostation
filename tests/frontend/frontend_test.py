"""Runs main.js and map.js in a real JS engine against a stubbed DOM and a
stubbed Leaflet, so the browser logic can be tested without a browser.

Covers the guards that are easy to break and impossible to notice until you
are standing in a field: the no-GPS-fix guard, where the map first centres,
and the status-icon mapping.

    pip install -r requirements-dev.txt
    python tests/frontend/frontend_test.py
"""
import json
import pathlib
import re
import sys

from py_mini_racer import MiniRacer

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
JS = ROOT / "static" / "connections" / "js"

fails = []


def check(label, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"   {detail}"))
    if not cond:
        fails.append(label)


def new_ctx(with_map=False):
    """A fresh interpreter with the harness loaded, and optionally the real
    map.js on top of a stubbed Leaflet instead of the aeroMap stub."""
    ctx = MiniRacer()
    ctx.eval((HERE / "dom_harness.js").read_text())
    if with_map:
        ctx.eval((HERE / "leaflet_stub.js").read_text())
        ctx.eval((JS / "map.js").read_text())
    ctx.eval((JS / "main.js").read_text())
    return ctx


def push(ctx, lat, lng, seq, status="active", mode="GUIDED"):
    """One telemetry frame, shaped exactly as uav_api's location task sends it."""
    frame = {
        "type": 102, "id": "10", "device": "uav", "status": status,
        "lat": str(lat), "lng": str(lng), "alt": "1.0", "heading": "31",
        "ground_speed": "0.02", "air_speed": "0.02", "battery_percent": "100",
        "flight_mode": mode, "ready_to_arm": True,
        "time": f"2026-09-06T15:00:{seq:02d}.000000",
    }
    ctx.eval(f"applyTelemetry({json.dumps(frame)})")


def test_has_valid_fix():
    """The guard itself, lifted out of main.js rather than reimplemented."""
    print("\n[1] hasValidFix")
    src = (JS / "main.js").read_text()
    m = re.search(r"function hasValidFix\(lat, lng\) \{.*?\n\}", src, re.S)
    assert m, "hasValidFix not found in main.js"
    ctx = MiniRacer()
    ctx.eval(m.group(0))

    cases = [
        (0, 0, False, "exact Null Island (ArduPilot before EKF origin)"),
        (-0.0, 0.0, False, "negative zero"),
        (-15.8400811, -47.926642, True, "a real fix"),
        (0.00001, 0.00001, True, "genuine near-equator fix is NOT suppressed"),
        (-15.84, 0.0, True, "longitude alone zero is still a fix"),
        (0.0, -47.93, True, "latitude alone zero is still a fix"),
        (91, 10, False, "latitude out of range"),
        (10, 181, False, "longitude out of range"),
        (90, 180, True, "range extremes are valid"),
    ]
    for lat, lng, expected, desc in cases:
        check(f"{desc} ({lat}, {lng})", ctx.call("hasValidFix", lat, lng) == expected)
    for expr, desc in [("NaN, NaN", "NaN (field absent from the push)"),
                       ("Infinity, 0", "Infinity"),
                       ("parseFloat(undefined), parseFloat(undefined)", "undefined")]:
        check(desc, ctx.eval(f"hasValidFix({expr})") is False)


def test_marker_withheld_until_fix():
    print("\n[2] the marker waits for a real fix")
    ctx = new_ctx()
    for i in range(1, 6):
        push(ctx, 0.0, 0.0, i)
    check("map never drawn while at Null Island", ctx.eval("CALLS.setDrone.length") == 0)
    check("panel reads 'no fix', not 0.000000", ctx.eval("ELS['tm-lat'].textContent") == "no fix")
    check("no-fix coordinates flagged as a warning", "warn" in ctx.eval("ELS['tm-lat'].className"))
    check("the rest of telemetry still renders", ctx.eval("ELS['tm-mode'].textContent") == "GUIDED")

    push(ctx, -15.8400811, -47.926642, 6)
    check("marker drawn on the first real fix", ctx.eval("CALLS.setDrone.length") == 1)
    check("drawn at the real position",
          ctx.eval("CALLS.setDrone[0][0]") == -15.8400811
          and ctx.eval("CALLS.setDrone[0][1]") == -47.926642)
    check("coordinates now displayed", ctx.eval("ELS['tm-lat'].textContent") == "-15.840081")

    push(ctx, -15.8402, -47.9268, 7)
    check("marker keeps up with a moving aircraft", ctx.eval("CALLS.setDrone.length") == 2)
    push(ctx, 0.0, 0.0, 8)
    check("losing the fix does NOT teleport the marker", ctx.eval("CALLS.setDrone.length") == 2)
    check("panel warns the fix was lost", ctx.eval("ELS['tm-lat'].textContent") == "no fix")
    push(ctx, -15.8403, -47.9269, 9)
    check("recovers when the fix returns", ctx.eval("CALLS.setDrone.length") == 3)


def test_map_centring_and_icons():
    print("\n[3] map centring and status icons")
    ctx = new_ctx(with_map=True)
    check("map was created", ctx.eval("MAPCALLS.created") is True)

    for i in range(1, 6):
        push(ctx, 0.0, 0.0, i)
    check("view never recentres on Null Island", ctx.eval("MAPCALLS.setView.length") == 0)
    check("no track point at (0,0)", ctx.eval("MAPCALLS.trackPoints.length") == 0)

    push(ctx, -15.8400811, -47.926642, 6)
    check("view recentres exactly once, on the real fix", ctx.eval("MAPCALLS.setView.length") == 1)
    check("recentred on the flying site", ctx.eval("MAPCALLS.setView[0][0]") == -15.8400811)
    check("active aircraft gets the green icon",
          "drone-green.png" in (ctx.eval("aeroMap.marker._icon._url") or ""))

    push(ctx, -15.8402, -47.9268, 7)
    check("track holds only real positions", ctx.eval("MAPCALLS.trackPoints.length") == 2)
    check("view not recentred again -- the operator keeps control",
          ctx.eval("MAPCALLS.setView.length") == 1)

    for status, colour in [("active", "green"), ("on_hold", "yellow"), ("inactive", "red")]:
        url = ctx.eval(f"aeroMap.iconFor('{status}')._url")
        check(f"{status} maps to drone-{colour}.png", f"drone-{colour}.png" in url, url)


if __name__ == "__main__":
    test_has_valid_fix()
    test_marker_withheld_until_fix()
    test_map_centring_and_icons()
    print("\n" + ("ALL CHECKS PASSED" if not fails else f"FAILURES: {fails}"))
    sys.exit(1 if fails else 0)
