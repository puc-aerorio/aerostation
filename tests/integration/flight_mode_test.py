import asyncio, json, sys
from aiohttp import ClientSession
import os

# Default to 8002, not 8000: a browser tab open on the ground station's normal
# port joins in as a second websocket client, issues its own commands and polls,
# and contaminates the assertions below. Override with GS_PORT / STUB_PORT.
GS_PORT = int(os.environ.get("GS_PORT", 8002))
STUB_PORT = int(os.environ.get("STUB_PORT", 8011))
GS = f"http://127.0.0.1:{GS_PORT}"
WS = f"ws://127.0.0.1:{GS_PORT}/ws/update-info/"
fails=[]
def check(l,c,d=""):
    print(f"  {'ok  ' if c else 'FAIL'} {l}" + ("" if c else f"   {d}")); c or fails.append(l)

async def main():
    async with ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            frames=[]
            async def pump():
                async for m in ws:
                    if m.type.name=="TEXT": frames.append(json.loads(m.data))
            t=asyncio.create_task(pump())

            async def push(seq, **extra):
                base={"id":"10","lat":"-15.84","lng":"-47.92","alt":"12.5",
                      "ground_speed":"3.2","air_speed":"3.4","heading":"271",
                      "battery_percent":"87","ready_to_arm":"True","device":"uav",
                      "type":"102","seq":str(seq),"ip":"127.0.0.1:8011/"}
                base.update(extra)
                await s.post(f"{GS}/update-info/", data=base)
                await asyncio.sleep(0.5)
                return [f for f in frames if f.get("type")==102][-1]

            # exactly what uav_api sends once a heartbeat exists
            f = await push(1, flight_mode="GUIDED")
            check("flight_mode reaches the browser", f.get("flight_mode")=="GUIDED", f.get("flight_mode"))
            check("stays a string, not coerced", isinstance(f.get("flight_mode"), str), type(f.get("flight_mode")))

            # mode changes are reflected
            f = await push(2, flight_mode="RTL")
            check("mode change propagates", f.get("flight_mode")=="RTL", f.get("flight_mode"))

            # before the first heartbeat uav_api sends the literal "None"
            f = await push(3, flight_mode="None")
            check("'None' becomes null, not the string 'None'", f.get("flight_mode") is None, repr(f.get("flight_mode")))

            # an older uav_api that doesn't send the field at all
            f = await push(4)
            check("absent field is simply absent (older uav_api)", "flight_mode" not in f, f.get("flight_mode"))

            # modes with underscores/digits survive intact
            f = await push(5, flight_mode="ALT_HOLD")
            check("underscored mode names intact", f.get("flight_mode")=="ALT_HOLD", f.get("flight_mode"))
            t.cancel()
    print("\n" + ("ALL CHECKS PASSED" if not fails else f"FAILURES: {fails}"))
    return 1 if fails else 0
sys.exit(asyncio.run(main()))
