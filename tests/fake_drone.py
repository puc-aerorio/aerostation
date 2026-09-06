"""Stands in for uav_api: serves the endpoints AeroStation calls and pushes
telemetry to /update-info/ once a second, exactly like the real location task."""
import asyncio, math, random
from aiohttp import web, ClientSession

PORT = int(os.environ.get("STUB_PORT", 8011))
import os
GS = os.environ.get("GS", "http://127.0.0.1:8002")
scripts = ["ned_square.py", "takeoff_land.py"]
running = []
log_lines = ["connecting to uav_api...", "armed", "takeoff to 15m",
             "waypoint 1 reached", "waypoint 2 reached"]

async def handler(request):
    p = request.path
    if p == "/mission/list-scripts":
        return web.json_response({"device":"uav","id":"10","result":"success","type":42,"scripts":scripts})
    if p == "/mission/running-scripts":
        return web.json_response({"device":"uav","id":"10","result":"success","type":50,"scripts":running})
    if p == "/mission/execute-script/":
        b = await request.json()
        running.clear()
        running.append({"script": b["script_name"], "session":"UAV_API_10-x",
                        "started_at":"20260906_143012","out_log":"/o.log","err_log":"/e.log"})
        return web.json_response({"device":"uav","id":"10","result":"success","type":46,"script":b["script_name"]})
    if p == "/mission/stop-script/":
        running.clear()
        return web.json_response({"device":"uav","id":"10","result":"success","type":52,"script":"x","info":"Stopped"})
    if p == "/mission/script-log":
        n = min(len(log_lines), 2 + int(asyncio.get_event_loop().time()) % 4)
        stream = request.query.get("stream","out")
        lines = log_lines[:n] if stream == "out" else []
        return web.json_response({"device":"uav","id":"10","result":"success",
                                  "script":"ned_square.py","stream":stream,"lines":lines})
    if p == "/mission/clear-scripts":
        scripts.clear()
        return web.json_response({"device":"uav","id":"10","result":"success","type":48,
                                  "info":"Removed 2 script(s)","removed":["a.py","b.py"]})
    if p == "/telemetry/gps":
        return web.json_response({"device":"uav","id":"10","result":"success",
            "info":{"position":{"lat":-15.8401,"lon":-47.9268,"alt":1063.1,"relative_alt":12.5},
                    "velocity":{"vx":3.2,"vy":0.1,"vz":0.0},"heading":271.0}})
    if p == "/command/takeoff":
        return web.json_response({"device":"uav","id":"10",
            "result":f"Takeoff successful! Vehicle at {request.query.get('alt')} meters"})
    if p == "/command/arm":
        return web.json_response({"device":"uav","id":"10","result":"Armed vehicle"})
    return web.json_response({"device":"uav","id":"10","result":"ok"})

async def push():
    seq, t = 0, 0.0
    async with ClientSession() as s:
        while True:
            t += 0.05
            # small circular track so the map polyline has something to draw
            lat = -15.8401 + 0.0004 * math.sin(t)
            lng = -47.9268 + 0.0004 * math.cos(t)
            try:
                await s.post(f"{GS}/update-info/", data={
                    "id":"10","lat":f"{lat:.7f}","lng":f"{lng:.7f}","alt":f"{12.5+math.sin(t)*2:.2f}",
                    "ground_speed":f"{3.2+random.random()*0.4:.2f}","air_speed":f"{3.4:.2f}",
                    "heading":f"{(t*20)%360:.1f}","battery_percent":"87","ready_to_arm":"True",
                    "device":"uav","type":"102","seq":str(seq),"ip":f"127.0.0.1:{PORT}/"})
                seq += 1
            except Exception as e:
                print("push failed", e)
            await asyncio.sleep(1)

async def main():
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    r = web.AppRunner(app); await r.setup()
    await web.TCPSite(r, "127.0.0.1", PORT).start()
    print(f"fake drone on {PORT}, pushing telemetry to {GS}")
    await push()

asyncio.run(main())
