"""Drives AeroStation end to end: a stub uav_api records what the Django
dispatcher actually sends, while a websocket client plays the browser."""
import asyncio, json, sys
from aiohttp import web, ClientSession

import os

# Default to 8002, not 8000: a browser tab open on the ground station's normal
# port joins in as a second websocket client, issues its own commands and polls,
# and contaminates the assertions below. Override with GS_PORT / STUB_PORT.
GS_PORT = int(os.environ.get("GS_PORT", 8002))
STUB_PORT = int(os.environ.get("STUB_PORT", 8011))
GS = f"http://127.0.0.1:{GS_PORT}"
WS = f"ws://127.0.0.1:{GS_PORT}/ws/update-info/"
seen = []          # every request the "drone" received

async def record(request):
    body = None
    if request.method in ("POST", "PUT"):
        try:
            body = await request.json()
        except Exception:
            body = "<non-json>"
    seen.append({
        "method": request.method,
        "path": request.path,
        "query": dict(request.query),
        "body": body,
    })
    path = request.path
    if path == "/mission/list-scripts":
        return web.json_response({"device": "uav", "id": "10", "result": "success",
                                  "type": 42, "scripts": ["ned_square.py", "square.py"]})
    if path == "/mission/running-scripts":
        return web.json_response({"device": "uav", "id": "10", "result": "success",
                                  "type": 50, "scripts": [
                                      {"script": "ned_square.py", "session": "UAV_API_10-x",
                                       "started_at": "20260906_143012",
                                       "out_log": "/o.log", "err_log": "/e.log"}]})
    if path == "/mission/script-log":
        return web.json_response({"device": "uav", "id": "10", "result": "success",
                                  "script": "ned_square.py", "stream": request.query.get("stream", "out"),
                                  "lines": ["takeoff to 15m", "waypoint 1 reached"]})
    if path == "/mission/clear-scripts":
        return web.json_response({"device": "uav", "id": "10", "result": "success",
                                  "type": 48, "info": "Removed 2 script(s)", "removed": ["a.py"]})
    if path == "/command/takeoff":
        return web.json_response({"device": "uav", "id": "10",
                                  "result": f"Takeoff successful! Vehicle at {request.query.get('alt')} meters"})
    return web.json_response({"device": "uav", "id": "10", "result": "ok"})


async def main():
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", record)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", STUB_PORT).start()

    failures = []
    def check(label, cond, detail=""):
        (print(f"  ok   {label}") if cond else
         (print(f"  FAIL {label} {detail}"), failures.append(label)))

    async with ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            frames = []
            async def pump():
                async for m in ws:
                    if m.type.name == "TEXT":
                        frames.append(json.loads(m.data))
            task = asyncio.create_task(pump())

            async def wait_for(pred, timeout=6):
                end = asyncio.get_event_loop().time() + timeout
                while asyncio.get_event_loop().time() < end:
                    for f in frames:
                        if pred(f):
                            return f
                    await asyncio.sleep(0.05)
                return None

            # --- 1. the drone's location push, exactly as uav_api sends it ---
            print("\n[1] telemetry push")
            await s.post(f"{GS}/update-info/", data={
                "id": "10", "lat": "-15.8401", "lng": "-47.9268", "alt": "12.5",
                "ground_speed": "3.2", "air_speed": "3.4", "heading": "271.0",
                "battery_percent": "87", "ready_to_arm": "True",
                "device": "uav", "type": "102", "seq": "1",
                "ip": f"127.0.0.1:{STUB_PORT}/",
            })
            tm = await wait_for(lambda f: f.get("type") == 102)
            check("telemetry frame reaches the browser", tm is not None)
            if tm:
                check("ready_to_arm forwarded as a real bool", tm.get("ready_to_arm") is True, tm.get("ready_to_arm"))
                check("speeds/heading/battery forwarded",
                      tm.get("ground_speed") == 3.2 and tm.get("heading") == 271.0
                      and tm.get("battery_percent") == 87.0, tm)

            # ready_to_arm = None must stay distinct from False
            await s.post(f"{GS}/update-info/", data={
                "id": "10", "lat": "-15.8401", "lng": "-47.9268", "alt": "12.5",
                "ready_to_arm": "None", "device": "uav", "type": "102", "seq": "2",
                "ip": f"127.0.0.1:{STUB_PORT}/"})
            await asyncio.sleep(0.4)
            none_frame = [f for f in frames if f.get("type") == 102][-1]
            check("ready_to_arm 'None' stays None, not False", none_frame.get("ready_to_arm") is None,
                  none_frame.get("ready_to_arm"))

            # --- 2. query params on takeoff ---
            print("\n[2] query parameters")
            frames.clear(); seen.clear()
            await ws.send_json({"id": 1, "type": 26, "button_type": "default",
                                "data": {}, "params": {"alt": 8}, "receiver": "10"})
            r = await wait_for(lambda f: f.get("gs_command") == 26)
            check("takeoff response returns", r is not None)
            check("alt reached the drone as a query param",
                  any(x["path"] == "/command/takeoff" and x["query"].get("alt") == "8" for x in seen), seen)
            check("response stamped with gs_command", r and r.get("gs_command") == 26)

            # --- 3. the param whitelist ---
            print("\n[3] parameter whitelist")
            seen.clear(); frames.clear()
            await ws.send_json({"id": 1, "type": 26, "button_type": "default", "data": {},
                                "params": {"alt": 5, "evil": "../../etc/passwd"}, "receiver": "10"})
            await wait_for(lambda f: f.get("gs_command") == 26)
            q = [x for x in seen if x["path"] == "/command/takeoff"][-1]["query"]
            check("undeclared param dropped", "evil" not in q, q)
            check("declared param kept", q.get("alt") == "5", q)

            # --- 4. trailing slash fidelity ---
            print("\n[4] trailing slashes")
            seen.clear(); frames.clear()
            await ws.send_json({"id": 1, "type": 46, "button_type": "default",
                                "data": {"script_name": "ned_square.py"}, "params": {}, "receiver": "10"})
            await wait_for(lambda f: f.get("gs_command") == 46)
            check("execute-script keeps its trailing slash",
                  any(x["path"] == "/mission/execute-script/" for x in seen), [x["path"] for x in seen])
            check("POST body survives (no 307 body drop)",
                  any(x["body"] == {"script_name": "ned_square.py"} for x in seen), seen)

            # --- 5. DELETE ---
            print("\n[5] DELETE method")
            seen.clear(); frames.clear()
            await ws.send_json({"id": 1, "type": 48, "button_type": "default",
                                "data": {}, "params": {}, "receiver": "10"})
            await wait_for(lambda f: f.get("gs_command") == 48)
            check("clear-scripts issued as DELETE",
                  any(x["method"] == "DELETE" and x["path"] == "/mission/clear-scripts" for x in seen), seen)

            # --- 6. throttled polling ---
            print("\n[6] polling")
            seen.clear(); frames.clear()
            await ws.send_json({"id": 1, "type": 50, "button_type": "poll_start",
                                "data": {}, "params": {}, "receiver": "10", "interval": 1.0})
            await asyncio.sleep(3.2)
            polls = [x for x in seen if x["path"] == "/mission/running-scripts"]
            check(f"poll runs repeatedly ({len(polls)} ticks in 3.2s)", 2 <= len(polls) <= 5, len(polls))
            check("poll responses stamped with gs_command",
                  any(f.get("gs_command") == 50 for f in frames))

            # a floor is enforced even when the UI asks for something absurd
            seen.clear()
            await ws.send_json({"id": 1, "type": 50, "button_type": "poll_stop",
                                "data": {}, "params": {}, "receiver": "10"})
            await asyncio.sleep(0.3); seen.clear()
            await ws.send_json({"id": 1, "type": 50, "button_type": "poll_start",
                                "data": {}, "params": {}, "receiver": "10", "interval": 0.001})
            await asyncio.sleep(2.2)
            fast = [x for x in seen if x["path"] == "/mission/running-scripts"]
            check(f"min_interval floor holds ({len(fast)} ticks in 2.2s, not hundreds)", len(fast) <= 4, len(fast))

            # two script-log polls with different params coexist
            seen.clear()
            for stream in ("out", "err"):
                await ws.send_json({"id": 1, "type": 54, "button_type": "poll_start", "data": {},
                                    "params": {"script_name": "ned_square.py", "stream": stream, "tail": 200},
                                    "receiver": "10", "interval": 1.0})
            await asyncio.sleep(2.2)
            streams = {x["query"].get("stream") for x in seen if x["path"] == "/mission/script-log"}
            check("stdout and stderr polls run concurrently", streams == {"out", "err"}, streams)

            # --- 7. poll_stop ---
            print("\n[7] poll_stop")
            for stream in ("out", "err"):
                await ws.send_json({"id": 1, "type": 54, "button_type": "poll_stop", "data": {},
                                    "params": {"script_name": "ned_square.py", "stream": stream, "tail": 200},
                                    "receiver": "10"})
            await ws.send_json({"id": 1, "type": 50, "button_type": "poll_stop",
                                "data": {}, "params": {}, "receiver": "10"})
            await asyncio.sleep(0.6); seen.clear(); await asyncio.sleep(6.0)
            paths = [x["path"] for x in seen]
            print(f"       [diag] leftovers in 6.0s window: {len(seen)} -> {paths}")
            check("all polls stopped", len(seen) == 0, seen)

            # --- 8. failures surface instead of vanishing ---
            print("\n[8] error envelope")
            frames.clear()
            await s.post(f"{GS}/update-info/", data={
                "id": "10", "lat": "0", "lng": "0", "alt": "0", "device": "uav",
                "type": "102", "seq": "3", "ip": "127.0.0.1:9/"})   # nothing listening
            await asyncio.sleep(0.3); frames.clear()
            await ws.send_json({"id": 1, "type": 24, "button_type": "default",
                                "data": {}, "params": {}, "receiver": "10"})
            err = await wait_for(lambda f: f.get("type") == 900)
            check("failed request produces a GS error frame", err is not None, frames)
            check("error frame stamped with the command", err and err.get("gs_command") == 24)

            task.cancel()

    # --- 9. polls die with the socket ---
    print("\n[9] disconnect hygiene")
    seen.clear()
    async with ClientSession() as s2:
        async with s2.ws_connect(WS) as ws2:
            await s2.post(f"{GS}/update-info/", data={
                "id": "10", "lat": "0", "lng": "0", "alt": "0", "device": "uav",
                "type": "102", "seq": "4", "ip": f"127.0.0.1:{STUB_PORT}/"})
            await asyncio.sleep(0.3)
            await ws2.send_json({"id": 1, "type": 50, "button_type": "poll_start",
                                 "data": {}, "params": {}, "receiver": "10", "interval": 1.0})
            await asyncio.sleep(1.5)
    await asyncio.sleep(0.8); seen.clear(); await asyncio.sleep(6.0)
    print(f"       [diag] leftovers in 6.0s window: {len(seen)} -> {[x['path'] for x in seen]}")
    check("poll cancelled when the socket closed", len(seen) == 0, seen)

    await runner.cleanup()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILURES: {failures}"))
    return 1 if failures else 0

sys.exit(asyncio.run(main()))
