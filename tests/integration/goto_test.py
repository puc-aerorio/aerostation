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
STUB = STUB_PORT
seen = []

async def record(request):
    body = await request.json() if request.method == "POST" else None
    seen.append({"method": request.method, "path": request.path, "body": body})
    if request.path == "/movement/go_to_gps/":
        b = body or {}
        return web.json_response({"device":"uav","id":"10",
            "result": f"Going to coord ({b.get('lat')}, {b.get('long')}, {b.get('alt')})"})
    return web.json_response({"device":"uav","id":"10","result":"ok"})

async def main():
    app = web.Application(); app.router.add_route("*", "/{t:.*}", record)
    r = web.AppRunner(app); await r.setup()
    await web.TCPSite(r, "127.0.0.1", STUB).start()

    fails = []
    def check(label, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  {detail}"))
        if not cond: fails.append(label)

    async with ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            frames = []
            async def pump():
                async for m in ws:
                    if m.type.name == "TEXT": frames.append(json.loads(m.data))
            t = asyncio.create_task(pump())

            # register the drone so the dispatcher knows its address
            await s.post(f"{GS}/update-info/", data={
                "id":"10","lat":"-15.8400811","lng":"-47.926642","alt":"12.5",
                "device":"uav","type":"102","seq":"1","ip":f"127.0.0.1:{STUB}/"})
            await asyncio.sleep(0.6)

            # exactly what the map click sends
            await ws.send_json({"id":1,"type":60,"button_type":"default",
                "data":{"lat":-15.8395,"long":-47.9260,"alt":25,"look_at_target":True},
                "params":{},"receiver":"10"})
            await asyncio.sleep(1.5); t.cancel()

            hits = [x for x in seen if "go_to_gps" in x["path"]]
            check("request reached the drone", len(hits) == 1, seen)
            if hits:
                h = hits[0]
                check("method is POST", h["method"] == "POST", h["method"])
                check("trailing slash preserved", h["path"] == "/movement/go_to_gps/", h["path"])
                b = h["body"] or {}
                check("longitude sent as 'long' (uav_api's spelling)", "long" in b, list(b))
                check("no stray 'lng'/'lon' key", "lng" not in b and "lon" not in b, list(b))
                check("lat/long/alt values intact",
                      b.get("lat") == -15.8395 and b.get("long") == -47.9260 and b.get("alt") == 25, b)
                check("look_at_target passed through", b.get("look_at_target") is True, b)
            resp = [f for f in frames if f.get("gs_command") == 60]
            check("response returns stamped with gs_command 60", len(resp) == 1, frames)
            if resp:
                check("result reaches the UI", "Going to coord" in resp[0].get("result",""), resp[0])

    await r.cleanup()
    print("\n" + ("ALL CHECKS PASSED" if not fails else f"FAILURES: {fails}"))
    return 1 if fails else 0

sys.exit(asyncio.run(main()))
