"""pod-weather: where the Pod is and the weather there (agent-1).

Every --interval seconds it writes <out> (atomically), which pod-view serves at
/weather.json for the dashboard's Live mode:

    {"condition": "rain", "temp_c": 4.1, "wind_kmh": 12.3, "humidity": 81,
     "place": "London, Ontario", "lat": 42.98, "lon": -81.25,
     "location_source": "set in ~/pod.env" | "from IP address (approximate)",
     "source": "Open-Meteo", "t_ms": 1795320000000}

condition uses the habitat view's names: clear, cloudy, rain, storm, snow, fog.

Location: POD_LAT / POD_LON (and optional POD_PLACE) in the environment or
~/pod.env win. Otherwise the public IP is looked up (ip-api.com), which on a
phone hotspot gives the carrier's city, not the Pod's. The Pod has no GPS.

Weather: Open-Meteo current conditions (free, no key). If a fetch fails the
old file is kept, with its t_ms, so the dashboard can tell it is stale.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

IP_URL = "http://ip-api.com/json/?fields=status,city,regionName,country,lat,lon"
METEO_URL = "https://api.open-meteo.com/v1/forecast"


def wmo_condition(code: int) -> str:
    """WMO weather code -> habitat view condition."""
    if code in (45, 48):
        return "fog"
    if code >= 95:
        return "storm"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if code in (2, 3):
        return "cloudy"
    return "clear"


def read_env_file(path: str) -> dict:
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def get_json(url: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def locate(env: dict) -> dict | None:
    lat, lon = env.get("POD_LAT"), env.get("POD_LON")
    if lat and lon:
        return {"lat": float(lat), "lon": float(lon), "place": env.get("POD_PLACE", ""),
                "location_source": "set in ~/pod.env"}
    j = get_json(IP_URL)
    if j.get("status") != "success":
        return None
    return {"lat": j["lat"], "lon": j["lon"],
            "place": ", ".join(p for p in (j.get("city"), j.get("regionName")) if p),
            "location_source": "from IP address (approximate)"}


def fetch_weather(loc: dict) -> dict:
    q = urllib.parse.urlencode({
        "latitude": loc["lat"], "longitude": loc["lon"],
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code,is_day",
        "wind_speed_unit": "kmh", "timezone": "auto"})
    cur = get_json(METEO_URL + "?" + q)["current"]
    return {
        "condition": wmo_condition(int(cur["weather_code"])),
        "weather_code": int(cur["weather_code"]),
        "temp_c": round(float(cur["temperature_2m"]), 1),
        "wind_kmh": round(float(cur["wind_speed_10m"]), 1),
        "humidity": cur.get("relative_humidity_2m"),
        "is_day": bool(cur.get("is_day", 1)),
        "place": loc["place"], "lat": round(loc["lat"], 3), "lon": round(loc["lon"], 3),
        "location_source": loc["location_source"],
        "source": "Open-Meteo",
        "t_ms": int(time.time() * 1000),
    }


def write_atomic(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="pod-data/weather.json")
    ap.add_argument("--interval", type=float, default=600, help="seconds between updates")
    ap.add_argument("--env-file", default=os.path.expanduser("~/pod.env"))
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    loc = None
    while True:
        try:
            env = {**read_env_file(a.env_file), **{k: v for k, v in os.environ.items() if k.startswith("POD_")}}
            loc = locate(env) if (loc is None or "POD_LAT" in env) else loc   # IP lookup once
            if loc:
                w = fetch_weather(loc)
                write_atomic(a.out, w)
                print(f"pod-weather: {w['place'] or '?'} {w['condition']} {w['temp_c']} C "
                      f"wind {w['wind_kmh']} km/h ({w['location_source']})", flush=True)
            else:
                print("pod-weather: location lookup failed", flush=True)
        except Exception as e:                  # no internet etc.: keep the old file, try later
            print(f"pod-weather: {type(e).__name__}: {e}", flush=True)
            if a.once:
                return 1
            time.sleep(min(a.interval, 60))
            continue
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
